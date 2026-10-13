from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import logging
import re
from typing import Any

import httpx
from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import Category, ImportRun, Product
from integrations.resellcodes_client import ReSellCodesClient

logger = logging.getLogger(__name__)


def slug(prefix: str, raw: str) -> str:
    return f"{prefix}-{raw}".lower().replace(" ", "-")[:155]


def price_fields(price_usd: str | None) -> tuple[int, str] | None:
    if price_usd is None:
        return None
    rate = get_settings().usd_to_rub_minor
    if rate:
        return int((Decimal(str(price_usd)) * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)), "RUB"
    return int((Decimal(str(price_usd)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)), "USD"


def steam_offer_name(offer: dict[str, Any], game: dict[str, Any]) -> str:
    return str(offer.get("name") or game.get("name") or "Steam-товар").strip()


def steam_offer_key(offer: dict[str, Any], game: dict[str, Any], region: str) -> tuple[str, str]:
    """Supplier can expose multiple opaque package IDs for the same named game and region."""
    normalized_name = re.sub(r"\s+", " ", steam_offer_name(offer, game)).casefold()
    return normalized_name, region.upper()


def select_steam_offers(details: dict[str, Any], allowed_regions: set[str]) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], int]:
    """Keep one least-expensive opaque offer per displayed name and region.

    RU and CIS remain distinct because their activation eligibility differs. Editions whose
    supplier names differ (for example, Deluxe Edition) are deliberately not merged.
    """
    game = details.get("game", {})
    selected: dict[tuple[str, str], tuple[Decimal, str, dict[str, Any], dict[str, Any]]] = {}
    duplicates = 0
    for offer in details.get("offers", []):
        for region_data in offer.get("regions", []):
            region = str(region_data.get("region") or "").upper()
            if region not in allowed_regions or region_data.get("price_usd") is None:
                continue
            key = steam_offer_key(offer, game, region)
            try:
                candidate = (Decimal(str(region_data["price_usd"])), str(offer.get("sub_id") or ""), offer, region_data)
            except Exception:
                continue
            existing = selected.get(key)
            if existing is None or candidate[:2] < existing[:2]:
                if existing is not None:
                    duplicates += 1
                selected[key] = candidate
            else:
                duplicates += 1
    return [(offer, region_data) for _, _, offer, region_data in selected.values()], duplicates


async def get_category(session: AsyncSession, name: str, kind: str, identifier: str, parent_id: int | None = None, *, image: str | None = None, sort_order: int | None = None) -> Category:
    category_slug = slug(kind, identifier)
    category = await session.scalar(select(Category).where(Category.slug == category_slug))
    if not category:
        category = Category(name=name[:255], slug=category_slug, parent_id=parent_id, type=kind, active=True, image_url=image, sort_order=1000 if sort_order is None else sort_order)
        session.add(category)
        await session.flush()
    else:
        category.name, category.active = name[:255], True
        if parent_id is not None:
            category.parent_id = parent_id
        if image:
            category.image_url = image
        if sort_order is not None:
            category.sort_order = sort_order
    return category


async def upsert_product(session: AsyncSession, category: Category, product_type: str, external_id: str, name: str, raw: dict[str, Any], *, price_usd: str | None, image: str | None = None, platform: str | None = None, region: str | None = None, activation: str | None = None, stock: int | None = None, description: str | None = None, price: tuple[int, str] | None = None) -> bool:
    price = price or price_fields(price_usd)
    if not price:
        return False
    minor, currency = price
    supplier_usd = Decimal(str(price_usd)) if price_usd is not None else None
    statement = insert(Product).values(category_id=category.id, supplier="resellcodes", external_id=str(external_id), product_type=product_type, name=name[:500], description=description, image_url=image, platform=platform, region=region, activation_type=activation, price_minor=minor, currency=currency, supplier_price_usd=supplier_usd, available_by_import=(stock > 0 if stock is not None else None), active=True, imported_at=datetime.now(timezone.utc), raw_payload=raw)
    # Keep the operator's publication decision and any manually edited price on re-imports.
    locked = Product.__table__.c.price_locked
    statement = statement.on_conflict_do_update(constraint="uq_product_external", set_={
        "category_id": category.id, "name": name[:500], "description": description, "image_url": image, "platform": platform,
        "region": region, "activation_type": activation, "supplier_price_usd": supplier_usd,
        "price_minor": case((locked, Product.__table__.c.price_minor), else_=statement.excluded.price_minor),
        "currency": case((locked, Product.__table__.c.currency), else_=statement.excluded.currency),
        "available_by_import": (stock > 0 if stock is not None else None), "imported_at": datetime.now(timezone.utc), "raw_payload": raw,
    })
    await session.execute(statement)
    return True


async def import_catalog(session: AsyncSession) -> dict[str, int]:
    """Import documented read-only endpoints only; no purchasing/fulfilment call exists here."""
    running = await session.scalar(select(ImportRun).where(ImportRun.status == "running"))
    if running:
        raise RuntimeError("Импорт уже выполняется")
    run = ImportRun(status="running", counters={"imported": 0, "skipped": 0, "errors": 0, "deduplicated": 0})
    session.add(run)
    await session.commit()
    counters = run.counters.copy()
    warnings: list[str] = []
    settings = get_settings()

    async def detail_or_skip(api: ReSellCodesClient, path: str, label: str) -> dict[str, Any] | None:
        """A catalogue list can transiently contain an offer already removed by supplier."""
        try:
            return await api.get(path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            counters["errors"] += 1
            warning = f"{label}: supplier returned 404; skipped"
            warnings.append(warning)
            logger.warning(warning)
            return None

    try:
        async with ReSellCodesClient(get_settings().resellcodes_api_key) as api:
            # Gift cards belong to the one-off snapshot (scripts/snapshot_import.py): it builds the
            # brand → region tree and applies the markup, which a pass here would overwrite.
            # Keys have no documented popularity signal. They are opt-in and restricted to Steam.
            if settings.import_steam_keys:
                category = await get_category(session, "Steam-ключи", "game_key", "steam-keys")
                games = (await api.get("/game-keys", {"q": "", "platform": "Steam", "limit": settings.steam_gift_limit})).get("data", [])
                for game in games:
                    details = await detail_or_skip(api, f"/game-keys/{game['game_id']}/keys", f"game key {game['game_id']}")
                    if details is None:
                        continue
                    for offer in details.get("keys", []):
                        ok = await upsert_product(session, category, "game_key", f"{game['game_id']}:{offer['key_id']}", offer.get("name", game["name"]), {"game": game, "offer": offer}, price_usd=offer.get("price_usd"), image=game.get("image"), platform=game.get("platform"), region=game.get("region"), stock=offer.get("stock"), activation="key")
                        counters["imported" if ok else "skipped"] += 1
            # Steam Gifts endpoint exposes the supplier's ranked top-games list, capped at 100.
            category = await get_category(session, "Игры Steam", "steam_gift", "steam-gifts")
            imported_steam_ids: set[str] = set()
            steam_candidates: dict[tuple[str, str], tuple[Decimal, str, dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
            for game in (await api.get("/steam-gifts/games", {"limit": min(max(settings.steam_gift_limit, 1), 100)})).get("data", []):
                details = await detail_or_skip(api, f"/steam-gifts/games/{game['app_id']}", f"Steam gift {game['app_id']}")
                if details is None:
                    continue
                # Some API responses carry game data only in the list endpoint.
                details.setdefault("game", game)
                offers, duplicates = select_steam_offers(details, settings.steam_gift_region_codes)
                counters["deduplicated"] += duplicates
                for offer, region_data in offers:
                    candidate_key = steam_offer_key(offer, game, str(region_data.get("region") or ""))
                    candidate = (Decimal(str(region_data["price_usd"])), f"{game['app_id']}:{offer['sub_id']}", game, offer, region_data)
                    existing = steam_candidates.get(candidate_key)
                    if existing is None or candidate[:2] < existing[:2]:
                        if existing is not None:
                            counters["deduplicated"] += 1
                        steam_candidates[candidate_key] = candidate
                    else:
                        counters["deduplicated"] += 1
            for _, _, game, offer, region_data in steam_candidates.values():
                key = f"{game['app_id']}:{offer['sub_id']}:{region_data['region']}"
                description = offer.get("description") or game.get("description")
                ok = await upsert_product(session, category, "steam_gift", key, steam_offer_name(offer, game), {"game": game, "offer": offer, "region": region_data}, price_usd=region_data.get("price_usd"), image=game.get("image"), region=region_data.get("region"), platform="Steam", activation="Steam Gift", description=description)
                if ok:
                    imported_steam_ids.add(key)
                counters["imported" if ok else "skipped"] += 1
            # Hide stale variants only after the whole Steam pass finishes successfully.
            if imported_steam_ids:
                await session.execute(
                    update(Product)
                    .where(Product.supplier == "resellcodes", Product.product_type == "steam_gift", Product.external_id.not_in(imported_steam_ids))
                    .values(active=False)
                )
            run.status, run.counters, run.error_summary, run.finished_at = "success", counters, "\n".join(warnings[:100]) or None, datetime.now(timezone.utc)
            await session.commit()
            return counters
    except Exception as exc:
        run.status, run.counters, run.error_summary, run.finished_at = "failed", counters, str(exc)[:2000], datetime.now(timezone.utc)
        await session.commit()
        raise
