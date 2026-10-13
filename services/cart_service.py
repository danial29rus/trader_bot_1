import hashlib
import json

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import CartItem, Product


def config_key(configuration: dict | None) -> str:
    if not configuration:
        return ""
    return hashlib.sha256(json.dumps(configuration, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def get_cart(session: AsyncSession, user_id: int) -> list[CartItem]:
    stmt = select(CartItem).options(selectinload(CartItem.product)).where(CartItem.user_id == user_id).order_by(CartItem.id)
    return list((await session.scalars(stmt)).all())


async def add_item(session: AsyncSession, user_id: int, product_id: int, configuration: dict | None = None) -> CartItem:
    product = await session.get(Product, product_id)
    if not product or not product.active:
        raise ValueError("Товар больше недоступен")
    if product.currency != "RUB":
        raise ValueError("Для этого товара не задана цена в RUB; оформление пока недоступно")
    key = config_key(configuration)
    line = await session.scalar(select(CartItem).where(CartItem.user_id == user_id, CartItem.product_id == product_id, CartItem.configuration_key == key))
    if line:
        line.quantity += 1
    else:
        line = CartItem(user_id=user_id, product_id=product_id, quantity=1, configuration=configuration, configuration_key=key)
        session.add(line)
    await session.flush()
    return line


async def change_quantity(session: AsyncSession, user_id: int, item_id: int, delta: int) -> bool:
    line = await session.scalar(select(CartItem).where(CartItem.id == item_id, CartItem.user_id == user_id).with_for_update())
    if not line:
        return False
    line.quantity += delta
    if line.quantity <= 0:
        await session.delete(line)
    return True


async def clear_cart(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(CartItem).where(CartItem.user_id == user_id))
