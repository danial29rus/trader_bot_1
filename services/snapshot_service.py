"""One-off catalogue snapshot from ReSellCodes: Telegram, game top-ups and gift cards.

The snapshot is meant to be run by hand, rarely. It stays under the supplier's
rate limit (paced requests plus a hard request budget), commits after every
supplier category so an interrupted run keeps its progress, and skips
categories that are already imported unless `refresh` is set. Prices are
converted to RUB once (rate × markup, rounded up to whole roubles); after that
the operator owns them in the admin panel, and re-runs keep manually edited
prices (`Product.price_locked`).
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Category, Product
from integrations.resellcodes_client import ReSellCodesClient
from services.import_service import get_category, upsert_product

logger = logging.getLogger(__name__)

# Popular brands, in the order they should appear in the bot.
TOPUP_BRANDS = (
    "PUBG Mobile", "Genshin Impact", "Mobile Legends", "Free Fire", "Honkai: Star Rail",
    "Zenless Zone Zero", "Call of Duty Mobile", "Brawl Stars", "Clash of Clans", "Clash Royale",
    "Roblox", "Valorant", "Wuthering Waves", "Honor of Kings", "Arena Breakout", "Delta Force",
    "Blood Strike", "Standoff 2", "8 Ball Pool", "Stumble Guys", "EA Sports FC Mobile", "Marvel Rivals",
)
GIFT_CARD_BRANDS = (
    "PlayStation", "Xbox", "Nintendo", "Steam", "Apple", "Google Play", "Roblox", "Spotify",
    "Netflix", "Razer Gold", "Riot", "Blizzard", "EA", "Ubisoft", "Amazon", "Discord", "Fortnite", "Minecraft",
)
STAR_PACKS = (50, 100, 250, 500, 1000, 2500, 5000, 10000)
TELEGRAM_USERNAME_FIELD = {"key": "telegram_username", "label": "Username получателя в Telegram (например, @durov)", "type": "text"}
SECTIONS = ("telegram", "topups", "giftcards")


class BudgetExhausted(RuntimeError):
    pass


class PacedClient(ReSellCodesClient):
    """ReSellCodes client that spaces requests out and refuses to exceed a request budget."""

    def __init__(self, api_key: str, budget: int, per_minute: int):
        super().__init__(api_key)
        self.budget, self.used = budget, 0
        self.interval = 60 / max(per_minute, 1)
        self._last = 0.0
        self.remaining_day: str | None = None

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(5):
            if self.used >= self.budget:
                raise BudgetExhausted(f"request budget of {self.budget} is used up")
            wait = self._last + self.interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            self.used += 1
            response = await self.client.get(path, params=params)
            self.remaining_day = response.headers.get("X-RateLimit-Remaining-Day", self.remaining_day)
            if response.status_code != 429:
                response.raise_for_status()
                return response.json()
            await asyncio.sleep(min(int(response.headers.get("Retry-After", "5")), 120) + attempt)
        raise RuntimeError("ReSellCodes rate limit retry limit exceeded")


@dataclass
class Pricing:
    rub_per_usd: Decimal
    markup_percent: Decimal

    def rub_minor(self, price_usd: Any) -> int:
        rub = Decimal(str(price_usd)) * self.rub_per_usd * (1 + self.markup_percent / 100)
        return math.ceil(rub) * 100


@dataclass
class SnapshotResult:
    counters: dict[str, int] = field(default_factory=lambda: {"imported": 0, "skipped_existing": 0, "skipped": 0, "errors": 0})
    warnings: list[str] = field(default_factory=list)
    requests: int = 0
    stopped_by_budget: bool = False


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) not in (None, ""):
            return mapping[key]
    return None


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "offers", "packages", "plans", "items", "prices"):
            if isinstance(payload.get(key), list):
                return [x for x in payload[key] if isinstance(x, dict)]
    return []


def parse_star_packs(payload: dict[str, Any]) -> list[tuple[int, str]]:
    """(stars, price_usd) pairs. The docs only promise "live pricing", so accept a pack list or a per-star price."""
    packs = []
    for item in _items(payload):
        quantity, price = _first(item, "quantity", "stars", "amount", "count"), _first(item, "price_usd", "usd", "price")
        if quantity and price is not None:
            packs.append((int(quantity), str(price)))
    if packs:
        return sorted(packs)
    unit = _first(payload, "price_per_star_usd", "star_price_usd", "usd_per_star", "price_usd", "rate")
    if unit is None:
        return []
    low, high = int(_first(payload, "min_quantity", "min") or 50), int(_first(payload, "max_quantity", "max") or 10000)
    return [(q, str((Decimal(str(unit)) * q).quantize(Decimal("0.0001")))) for q in STAR_PACKS if low <= q <= high]


def parse_premium_plans(payload: dict[str, Any]) -> list[tuple[int, str]]:
    plans = []
    for item in _items(payload):
        months, price = _first(item, "months", "duration", "period"), _first(item, "price_usd", "usd", "price")
        if months and price is not None:
            plans.append((int(re.sub(r"\D", "", str(months)) or 0), str(price)))
    if not plans:
        # Shape like {"3": "11.99", "6": ...} or {"prices": {"3": ...}}.
        source = payload.get("prices") if isinstance(payload.get("prices"), dict) else payload
        plans = [(int(k), str(v)) for k, v in source.items() if str(k).isdigit() and v is not None]
    return sorted(p for p in plans if p[0] > 0)


def region_label(region: str | None, variant: str | None = None) -> str:
    code = (region or "GLOBAL").upper()
    if code in {"GLOBAL", "WW", "WORLDWIDE"}:
        label = "🌍 Global"
    elif code == "CIS":
        label = "🌐 СНГ"
    elif re.fullmatch(r"[A-Z]{2}", code):
        label = "".join(chr(0x1F1E6 + ord(c) - 65) for c in code) + f" {code}"
    else:
        label = code
    return f"{label} · {variant}" if variant else label


def _matches(query: str, brand: str) -> bool:
    return query.casefold() in brand.casefold()


async def _already_imported(session: AsyncSession, product_type: str, prefix: str) -> bool:
    # supplier_price_usd is only set by snapshot-era imports, so legacy rows are re-imported once.
    stmt = select(Product.id).where(Product.product_type == product_type, Product.external_id.like(f"{prefix}%"), Product.supplier_price_usd.is_not(None))
    found = await session.scalar(stmt.limit(1))
    return found is not None


async def snapshot_telegram(api: PacedClient, session: AsyncSession, pricing: Pricing, result: SnapshotResult) -> None:
    root = await get_category(session, "Telegram", "telegram", "root", sort_order=0)
    raw_fields = [TELEGRAM_USERNAME_FIELD]

    stars = parse_star_packs(await api.get("/telegram/stars"))
    if stars:
        category = await get_category(session, "⭐ Telegram Stars", "telegram", "stars", root.id, sort_order=0)
        for quantity, usd in stars:
            ok = await upsert_product(session, category, "telegram", f"stars:{quantity}", f"⭐ {quantity} Stars", {"kind": "stars", "quantity": quantity, "fields": raw_fields},
                                      price_usd=usd, price=(pricing.rub_minor(usd), "RUB"), platform="Telegram", activation="На аккаунт по username",
                                      description="Звёзды зачисляются на аккаунт получателя по username. Пароль и код входа не нужны.")
            result.counters["imported" if ok else "skipped"] += 1
    else:
        result.warnings.append("telegram stars: unknown response shape, nothing imported (run --probe)")

    premium = parse_premium_plans(await api.get("/telegram/premium"))
    if premium:
        category = await get_category(session, "💎 Telegram Premium", "telegram", "premium", root.id, sort_order=1)
        for months, usd in premium:
            ok = await upsert_product(session, category, "telegram", f"premium:{months}", f"💎 Premium · {months} мес.", {"kind": "premium", "months": months, "fields": raw_fields},
                                      price_usd=usd, price=(pricing.rub_minor(usd), "RUB"), platform="Telegram", activation="Подарок на аккаунт по username",
                                      description="Подписка Telegram Premium оформляется подарком на аккаунт получателя по username.")
            result.counters["imported" if ok else "skipped"] += 1
    else:
        result.warnings.append("telegram premium: unknown response shape, nothing imported (run --probe)")
    await session.commit()


async def _brand_snapshot(
    api: PacedClient, session: AsyncSession, pricing: Pricing, result: SnapshotResult, refresh: bool, *,
    brands: tuple[str, ...], brands_path: str, offers_path: str, offers_key: str, offer_id_key: str,
    kind: str, product_type: str, activation: str,
) -> None:
    seen: set[str] = set()
    for order, query in enumerate(brands):
        try:
            found = _items(await api.get(brands_path, {"q": query}))
        except BudgetExhausted:
            raise
        except Exception as exc:
            result.counters["errors"] += 1
            result.warnings.append(f"{kind} brand search {query!r}: {exc}")
            continue
        for brand in found:
            name = str(brand.get("brand") or "").strip()
            subcategories = [s for s in brand.get("subcategories", []) if s.get("category_id")]
            if not name or name.casefold() in seen or not _matches(query, name) or not subcategories:
                continue
            seen.add(name.casefold())
            image = next((s.get("image") for s in subcategories if s.get("image")), None)
            parent = await get_category(session, name, kind, f"brand-{name}", image=image, sort_order=order)
            for sub in subcategories:
                supplier_id = str(sub["category_id"])
                if not refresh and await _already_imported(session, product_type, f"{supplier_id}:"):
                    result.counters["skipped_existing"] += 1
                    continue
                try:
                    details = await api.get(offers_path.format(id=supplier_id))
                except BudgetExhausted:
                    raise
                except Exception as exc:
                    result.counters["errors"] += 1
                    result.warnings.append(f"{kind} {supplier_id}: {exc}")
                    continue
                offers = [o for o in details.get(offers_key, []) if o.get("price_usd") is not None and o.get("stock") != 0]
                if not offers:
                    result.counters["skipped"] += 1
                    continue
                single = len(subcategories) == 1
                target = parent if single else await get_category(
                    session, region_label(sub.get("region"), sub.get("variant") or None), kind, supplier_id, parent.id, image=sub.get("image") or image,
                )
                fields = details.get("fields") or []
                for offer in offers:
                    ok = await upsert_product(
                        session, target, product_type, f"{supplier_id}:{offer[offer_id_key]}", str(offer.get("name") or name),
                        {"brand": name, "subcategory": sub, "offer": offer, "fields": fields},
                        price_usd=offer["price_usd"], price=(pricing.rub_minor(offer["price_usd"]), "RUB"),
                        image=sub.get("image") or image, region=(sub.get("region") or "GLOBAL"), platform=name,
                        activation=activation, stock=offer.get("stock"),
                    )
                    result.counters["imported" if ok else "skipped"] += 1
                await session.commit()
    await _hide_legacy_categories(session, kind)


async def _hide_legacy_categories(session: AsyncSession, kind: str) -> None:
    """Older imports put each supplier category at the top level; hide those not adopted into a brand."""
    legacy = (await session.scalars(select(Category).where(
        Category.type == kind, Category.parent_id.is_(None), Category.active, Category.slug.not_like(f"{kind}-brand-%"),
    ))).all()
    for category in legacy:
        category.active = False
        await session.execute(update(Product).where(Product.category_id == category.id, Product.supplier_price_usd.is_(None)).values(active=False))
    await session.commit()


async def run_snapshot(
    session: AsyncSession, api_key: str, pricing: Pricing, *, sections: tuple[str, ...] = SECTIONS,
    budget: int = 1500, per_minute: int = 25, refresh: bool = False,
) -> SnapshotResult:
    result = SnapshotResult()
    async with PacedClient(api_key, budget, per_minute) as api:
        try:
            if "telegram" in sections:
                await snapshot_telegram(api, session, pricing, result)
            if "topups" in sections:
                await _brand_snapshot(api, session, pricing, result, refresh, brands=TOPUP_BRANDS, brands_path="/top-ups/brands",
                                      offers_path="/top-ups/categories/{id}/offers", offers_key="offers", offer_id_key="offer_id",
                                      kind="game_topup", product_type="game_topup", activation="Пополнение на аккаунт")
            if "giftcards" in sections:
                await _brand_snapshot(api, session, pricing, result, refresh, brands=GIFT_CARD_BRANDS, brands_path="/gift-cards/brands",
                                      offers_path="/gift-cards/categories/{id}/cards", offers_key="offers", offer_id_key="card_id",
                                      kind="gift_card", product_type="gift_card", activation="code")
        except BudgetExhausted as exc:
            await session.commit()
            result.stopped_by_budget = True
            result.warnings.append(f"{exc}; run again later to continue — imported categories are skipped")
        finally:
            result.requests = api.used
            if api.remaining_day is not None:
                result.warnings.append(f"supplier daily requests remaining: {api.remaining_day}")
    return result


async def probe(api_key: str) -> dict[str, Any]:
    """A handful of read-only calls showing the real response shapes for this account."""
    samples: dict[str, Any] = {}
    async with PacedClient(api_key, budget=12, per_minute=20) as api:
        async def take(label: str, path: str, params: dict[str, Any] | None = None) -> Any:
            try:
                samples[label] = await api.get(path, params)
            except Exception as exc:
                samples[label] = {"error": str(exc)}
            return samples[label]

        me = await take("me", "/me")
        if isinstance(me, dict):
            me.pop("email", None)
        await take("telegram_stars", "/telegram/stars")
        await take("telegram_premium", "/telegram/premium")
        topups = await take("topup_brands_pubg", "/top-ups/brands", {"q": "PUBG"})
        cards = await take("giftcard_brands_playstation", "/gift-cards/brands", {"q": "PlayStation"})
        for label, payload, path in (("topup_offers_sample", topups, "/top-ups/categories/{id}/offers"), ("giftcard_cards_sample", cards, "/gift-cards/categories/{id}/cards")):
            sub = next((s for b in _items(payload) for s in b.get("subcategories", []) if s.get("category_id")), None)
            if sub:
                await take(label, path.format(id=sub["category_id"]))
        samples["_requests_used"] = api.used
        samples["_daily_remaining"] = api.remaining_day
    return samples


async def count_by_section(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(select(Product.product_type, func.count()).where(Product.active).group_by(Product.product_type))
    return {kind: count for kind, count in rows.all()}

