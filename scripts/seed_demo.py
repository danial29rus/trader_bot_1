"""Insert explicitly labelled demo products. Never contacts ReSellCodes."""
import asyncio
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert

from database.models import Category, Product
from database.session import SessionLocal

DATA = [
    ("Игры Steam", "game_key", "demo-games", "game_key", "Hades — Steam key (демо)", 99000, "Steam", "GLOBAL", "key"),
    ("Игры Steam", "game_key", "demo-games", "game_key", "Stardew Valley — Steam key (демо)", 65000, "Steam", "GLOBAL", "key"),
    ("Подарочные карты", "gift_card", "demo-gift-cards", "gift_card", "Steam Gift Card 500 ₽ (демо)", 50000, "Steam", "RU", "code"),
    ("Игровые пополнения", "game_topup", "demo-topups", "game_topup", "PUBG Mobile 60 UC (демо)", 15000, None, "GLOBAL", "top-up"),
]


async def main() -> None:
    async with SessionLocal() as session, session.begin():
        for cat_name, kind, slug, product_type, name, price, platform, region, activation in DATA:
            stmt = insert(Category).values(name=cat_name, slug=slug, type=kind, active=True).on_conflict_do_update(index_elements=["slug"], set_={"name": cat_name, "active": True}).returning(Category.id)
            category_id = (await session.execute(stmt)).scalar_one()
            external = f"demo:{name}"
            product = insert(Product).values(category_id=category_id, supplier="demo", external_id=external, product_type=product_type, name=name, description="Демо-товар для проверки сценариев. Реальный товар не выдаётся.", platform=platform, region=region, activation_type=activation, price_minor=price, currency="RUB", available_by_import=True, active=True, imported_at=datetime.now(timezone.utc), raw_payload={"demo": True})
            await session.execute(product.on_conflict_do_update(constraint="uq_product_external", set_={"price_minor": price, "active": True, "imported_at": datetime.now(timezone.utc)}))
    print("Demo catalogue seeded.")


if __name__ == "__main__":
    asyncio.run(main())
