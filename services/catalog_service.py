from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Category, Product


async def categories(session: AsyncSession, parent_id: int | None = None) -> list[Category]:
    return list((await session.scalars(select(Category).where(Category.active, Category.parent_id == parent_id).order_by(Category.name))).all())


async def product_page(session: AsyncSession, category_id: int, page: int, size: int = 6) -> tuple[list[Product], int]:
    base = select(Product).where(Product.active, Product.category_id == category_id)
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = await session.scalars(base.order_by(Product.price_minor.asc(), Product.name.asc()).offset((page - 1) * size).limit(size))
    return list(rows), total


async def search_products(session: AsyncSession, query: str, page: int, size: int = 6) -> tuple[list[Product], int]:
    base = select(Product).where(Product.active, Product.name.ilike(f"%{query[:80]}%"))
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = await session.scalars(base.order_by(Product.price_minor.asc(), Product.name.asc()).offset((page - 1) * size).limit(size))
    return list(rows), total


def money(minor: int, currency: str = "RUB") -> str:
    return f"{minor // 100:,}.{minor % 100:02d} {currency}".replace(",", " ")
