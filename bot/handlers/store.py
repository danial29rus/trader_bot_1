from __future__ import annotations

import asyncio
import math
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards.inline import kb, main_menu, nav
from bot.states import Checkout, Search
from config import get_settings
from database.models import Category, Order, Product, PromoCode, User
from database.session import SessionLocal
from services import cart_service, catalog_service, order_service
from services.legal import payment_and_refunds, prices_and_tariffs, privacy_policy, service_rules, terms_of_service

router = Router()
settings = get_settings()
PAGE_SIZE = 6
_welcome_photo_file_id: str | None = None
_welcome_photo_lock = asyncio.Lock()


async def user_for(session: AsyncSession, message: Message | CallbackQuery) -> User:
    tg = message.from_user
    assert tg
    user = await session.scalar(select(User).where(User.telegram_id == tg.id))
    if not user:
        user = User(telegram_id=tg.id, username=tg.username, first_name=tg.first_name)
        session.add(user)
        await session.flush()
    else:
        user.username, user.first_name = tg.username, tg.first_name
    return user


async def render(callback: CallbackQuery, text: str, markup=None) -> None:
    await callback.answer()
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            await callback.message.answer(text, reply_markup=markup, parse_mode="HTML")


def status_text(status: str) -> str:
    return {"pending_payment": "ожидает оплаты", "paid_test": "оплачен", "cancelled": "отменён"}.get(status, status)


def product_text(product: Product) -> str:
    kind = {"steam_gift": "🎮 Steam Gift", "gift_card": "🎁 Подарочная карта", "game_key": "🔑 Цифровой ключ"}.get(product.product_type, product.product_type)
    fields = [f"<b>{escape(product.name)}</b>", kind]
    for label, value in (("Платформа", product.platform), ("Регион", product.region), ("Способ выдачи", product.activation_type)):
        if value:
            fields.append(f"{label}: {escape(value)}")
    if product.description:
        fields.append(escape(product.description[:700]))
    fields.append(f"Цена: <b>{catalog_service.money(product.price_minor, product.currency)}</b>")
    fields.append("Статус: <b>В наличии</b>")
    return "\n".join(fields)


def offer_label(product: Product) -> str:
    name = product.name.strip()
    price = catalog_service.money(product.price_minor, product.currency)
    variant = f" [{product.region}]" if product.region else ""
    available_for_name = max(12, 64 - len(variant) - len(price) - 3)
    short_name = name if len(name) <= available_for_name else name[:available_for_name - 1].rstrip() + "…"
    return f"{short_name}{variant} · {price}"[:64]


def documents_keyboard():
    return kb(
        [("🔒 Конфиденциальность", "doc:privacy")],
        [("📜 Пользовательское соглашение", "doc:terms")],
        [("💳 Цены и тарифы", "doc:prices")],
        [("↩️ Оплата и отмена", "doc:payments")],
        [("🛡 Правила сервиса", "doc:rules")],
        nav(),
    )


async def main_text() -> str:
    async with SessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(Product).where(Product.active)) or 0
    return (
        f"<b>{escape(settings.store_name)}</b>\n"
        "Игры Steam и подарочные карты для популярных сервисов.\n"
        "Выбирайте регион, способ активации и подходящий вариант в каталоге.\n\n"
        f"<i>В каталоге: {count} предложений · цены от дешёвых к дорогим.</i>"
    )


async def send_welcome(message: Message, caption: str) -> None:
    """Reuse Telegram's CDN copy instead of uploading the local banner per /start."""
    global _welcome_photo_file_id

    if _welcome_photo_file_id:
        await message.answer_photo(
            photo=_welcome_photo_file_id,
            caption=caption,
            reply_markup=main_menu(),
            parse_mode="HTML",
        )
        return

    image = next(
        (candidate for candidate in (Path("assets/welcome.png"), Path("assets/welcome.jpg")) if candidate.exists()),
        None,
    )
    if not image:
        await message.answer(caption, reply_markup=main_menu(), parse_mode="HTML")
        return

    # A burst of /start messages must not trigger several banner uploads.
    async with _welcome_photo_lock:
        if _welcome_photo_file_id:
            await message.answer_photo(
                photo=_welcome_photo_file_id,
                caption=caption,
                reply_markup=main_menu(),
                parse_mode="HTML",
            )
            return
        sent = await message.answer_photo(
            photo=FSInputFile(image),
            caption=caption,
            reply_markup=main_menu(),
            parse_mode="HTML",
        )
        if sent.photo:
            _welcome_photo_file_id = sent.photo[-1].file_id


@router.message(CommandStart())
async def start(message: Message) -> None:
    async with SessionLocal() as session, session.begin():
        await user_for(session, message)
    welcome = await main_text()
    await send_welcome(message, welcome)


@router.callback_query(F.data == "menu")
async def menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render(callback, await main_text(), main_menu())


@router.callback_query(F.data.startswith("cat_type:"))
async def categories_by_type(callback: CallbackQuery) -> None:
    kind = callback.data.split(":", 1)[1]
    async with SessionLocal() as session:
        rows = list((await session.scalars(select(Category).where(Category.type == kind, Category.active).order_by(Category.name))).all())
    if not rows:
        await render(callback, "Этот раздел пока не подключён к текущему каталогу.", kb(nav()))
        return
    if len(rows) == 1:
        await render_list(callback, rows[0].id, 1)
        return
    buttons = [[(category.name[:64], f"cat:{category.id}")] for category in rows]
    buttons.append(nav())
    await render(callback, "<b>Выберите категорию</b>", kb(*buttons))


@router.callback_query(F.data.startswith("cat:"))
async def category(callback: CallbackQuery) -> None:
    category_id = int(callback.data.split(":")[1])
    async with SessionLocal() as session:
        child = list((await session.scalars(select(Category).where(Category.parent_id == category_id, Category.active).order_by(Category.name))).all())
    if child:
        await render(callback, "<b>Выберите подкатегорию</b>", kb(*[[(x.name[:64], f"cat:{x.id}")] for x in child], nav()))
    else:
        await render_list(callback, category_id, 1)


async def render_list(callback: CallbackQuery, category_id: int, page: int) -> None:
    async with SessionLocal() as session:
        products, total = await catalog_service.product_page(session, category_id, max(1, page), PAGE_SIZE)
        category = await session.get(Category, category_id)
    if not products:
        await render(callback, "В этой категории пока нет товаров.", kb(nav()))
        return
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(page, pages)
    buttons = [[(offer_label(p), f"prod:{p.id}:{category_id}:{page}")] for p in products]
    controls = []
    if page > 1: controls.append(("⬅️", f"list:{category_id}:{page - 1}"))
    controls.append((f"{page}/{pages}", "noop"))
    if page < pages: controls.append(("➡️", f"list:{category_id}:{page + 1}"))
    buttons += [controls, nav()]
    title = escape(category.name) if category else "Каталог"
    await render(callback, f"<b>{title}</b>\n{total} предложений · от дешёвых к дорогим · страница {page}/{pages}", kb(*buttons))


@router.callback_query(F.data.startswith("list:"))
async def list_page(callback: CallbackQuery) -> None:
    _, category_id, page = callback.data.split(":")
    await render_list(callback, int(category_id), int(page))


@router.callback_query(F.data.startswith("prod:"))
async def product(callback: CallbackQuery) -> None:
    _, product_id, category_id, page = callback.data.split(":")
    async with SessionLocal() as session:
        item = await session.get(Product, int(product_id))
    if not item or not item.active:
        await render(callback, "Товар больше недоступен.", kb(nav(f"list:{category_id}:{page}")))
        return
    back = "menu" if category_id == 0 else f"list:{category_id}:{page}"
    await render(callback, product_text(item), kb([("🛒 Добавить в корзину", f"add:{item.id}")], [("⬅️ К каталогу", back), ("🏠 Главное меню", "menu")]))


@router.callback_query(F.data == "search")
async def search_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Search.waiting_query)
    await render(callback, "<b>Поиск по каталогу</b>\nОтправьте название игры или подарочной карты. Например: <code>Steam</code>, <code>Roblox</code>, <code>Counter-Strike</code>.", kb(nav()))


@router.message(Search.waiting_query)
async def search_receive(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()[:80]
    await state.clear()
    if not query:
        await message.answer("Пустой запрос. Попробуйте ещё раз.", reply_markup=main_menu())
        return
    async with SessionLocal() as session:
        rows, total = await catalog_service.search_products(session, query, 1, PAGE_SIZE)
    if not rows:
        await message.answer("Ничего не найдено в локальном каталоге.", reply_markup=main_menu())
        return
    await message.answer(f"<b>Результаты: {escape(query)}</b>\nНайдено: {total}. Показаны самые доступные предложения.", parse_mode="HTML", reply_markup=kb(*[[(offer_label(p), f"prod:{p.id}:0:1")] for p in rows], nav()))


@router.callback_query(F.data.startswith("add:"))
async def add(callback: CallbackQuery) -> None:
    product_id = int(callback.data.split(":")[1])
    try:
        async with SessionLocal() as session, session.begin():
            user = await user_for(session, callback)
            await cart_service.add_item(session, user.id, product_id)
        await callback.answer("Добавлено в корзину")
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)


async def render_cart(callback: CallbackQuery, user_id: int) -> None:
    async with SessionLocal() as session:
        lines = await cart_service.get_cart(session, user_id)
    if not lines:
        await render(callback, "<b>Корзина пуста</b>", kb([("Продолжить покупки", "menu")], nav()))
        return
    total = sum(line.product.price_minor * line.quantity for line in lines)
    text = ["<b>Корзина</b>"]
    buttons = []
    for line in lines:
        text.append(f"• {escape(line.product.name)} — {catalog_service.money(line.product.price_minor)} × {line.quantity} = <b>{catalog_service.money(line.product.price_minor * line.quantity)}</b>")
        buttons.append([("➖", f"qty:{line.id}:-1"), (f"{line.quantity} шт.", "noop"), ("➕", f"qty:{line.id}:1"), ("🗑", f"rm:{line.id}")])
    text.append(f"\nИтого: <b>{catalog_service.money(total)}</b>")
    buttons += [[("🧹 Очистить", "clear"), ("Продолжить покупки", "menu")], [("Оформить заказ", "checkout")], nav()]
    await render(callback, "\n".join(text), kb(*buttons))


@router.callback_query(F.data == "cart")
async def cart(callback: CallbackQuery) -> None:
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        user_id = user.id
    await render_cart(callback, user_id)


@router.callback_query(F.data.startswith("qty:"))
async def quantity(callback: CallbackQuery) -> None:
    _, item_id, delta = callback.data.split(":")
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        ok = await cart_service.change_quantity(session, user.id, int(item_id), int(delta))
        user_id = user.id
    if not ok:
        await callback.answer("Строка уже удалена", show_alert=True)
    await render_cart(callback, user_id)


@router.callback_query(F.data.startswith("rm:"))
async def remove(callback: CallbackQuery) -> None:
    item_id = int(callback.data.split(":")[1])
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        await cart_service.change_quantity(session, user.id, item_id, -10**9)
        user_id = user.id
    await render_cart(callback, user_id)


@router.callback_query(F.data == "clear")
async def clear(callback: CallbackQuery) -> None:
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        await cart_service.clear_cart(session, user.id)
        user_id = user.id
    await render_cart(callback, user_id)


async def checkout_view(user_id: int, promo_code: str | None, use_bonus: bool) -> tuple[str, object]:
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        lines = await cart_service.get_cart(session, user_id)
        if not lines:
            return "<b>Корзина пуста</b>", kb([("Продолжить покупки", "menu")], nav())
        subtotal = sum(line.product.price_minor * line.quantity for line in lines)
        normalized_code = order_service.normalize_promo_code(promo_code)
        promo_discount = 0
        promo_line = "Промокод: <i>не выбран</i>"
        if normalized_code:
            promo = await session.scalar(select(PromoCode).where(PromoCode.code == normalized_code))
            if promo and order_service.is_promo_available(promo):
                promo_discount = subtotal * promo.discount_percent // 100
                promo_line = f"Промокод <b>{escape(promo.code)}</b>: −{catalog_service.money(promo_discount)}"
            else:
                normalized_code = None
                promo_line = "Промокод: <i>недоступен, введите другой</i>"
        after_promo = subtotal - promo_discount
        balance = user.bonus_balance_minor if user else 0
        bonus_used = min(balance, after_promo) if use_bonus else 0
        bonus_line = f"Бонусный баланс: <b>{catalog_service.money(balance)}</b>"
        if bonus_used:
            bonus_line += f"\nК списанию: −{catalog_service.money(bonus_used)}"
        total = after_promo - bonus_used
    text = (
        "<b>Оформление заказа</b>\n\n"
        f"Сумма товаров: <b>{catalog_service.money(subtotal)}</b>\n"
        f"{promo_line}\n{bonus_line}\n"
        f"К оплате: <b>{catalog_service.money(total)}</b>\n\n"
        "<i>В данный момент оплата находится на стадии подключения.</i>"
    )
    buttons = [[("🏷 Ввести промокод", "checkout:promo")]]
    if normalized_code:
        buttons.append([("✖️ Убрать промокод", "checkout:clear_promo")])
    bonus_button = "✅ Списать бонусы" if use_bonus else "🎁 Списать бонусы"
    buttons.append([(bonus_button, "checkout:bonus")])
    buttons += [[("📄 Условия оплаты", "doc:payments"), ("💬 Поддержка", "support")], [("⬅️ К корзине", "cart"), ("🏠 Главное меню", "menu")]]
    return text, kb(*buttons)


async def show_checkout(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        user_id = user.id
    text, markup = await checkout_view(user_id, data.get("promo_code"), bool(data.get("use_bonus")))
    await render(callback, text, markup)


@router.callback_query(F.data == "checkout")
async def checkout(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    await show_checkout(callback, state)


@router.callback_query(F.data == "checkout:promo")
async def enter_promo(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(Checkout.waiting_promo)
    await callback.message.answer("<b>Промокод</b>\nОтправьте код одним сообщением. Используйте только латинские буквы, цифры, «-» или «_».", reply_markup=kb([("⬅️ Вернуться к оформлению", "checkout")]), parse_mode="HTML")


@router.message(Checkout.waiting_promo)
async def save_promo(message: Message, state: FSMContext) -> None:
    code = order_service.normalize_promo_code(message.text)
    if not code or len(code) > 40 or not all(char.isalnum() or char in "-_" for char in code):
        await message.answer("Введите корректный промокод: латинские буквы, цифры, «-» или «_».")
        return
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, message)
        promo = await session.scalar(select(PromoCode).where(PromoCode.code == code))
        if not promo or not order_service.is_promo_available(promo):
            await message.answer("Этот промокод не действует или его лимит уже исчерпан. Попробуйте другой.")
            return
        user_id = user.id
    await state.update_data(promo_code=code)
    await state.set_state(None)
    data = await state.get_data()
    text, markup = await checkout_view(user_id, code, bool(data.get("use_bonus")))
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data == "checkout:clear_promo")
async def clear_promo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(promo_code=None)
    await show_checkout(callback, state)


@router.callback_query(F.data == "checkout:bonus")
async def toggle_bonus(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(use_bonus=not bool(data.get("use_bonus")))
    await show_checkout(callback, state)


@router.callback_query(F.data.startswith("pay:"))
async def pay_choice(callback: CallbackQuery, state: FSMContext) -> None:
    await show_checkout(callback, state)


@router.callback_query(F.data.startswith("create:"))
async def create(callback: CallbackQuery, state: FSMContext) -> None:
    await show_checkout(callback, state)


async def order_details(callback: CallbackQuery, order: Order) -> None:
    items = "\n".join(f"• {escape(x.product_name_snapshot)} × {x.quantity} — {catalog_service.money(x.total_minor)}" for x in order.items)
    method = "Банковская карта" if order.payment_method == "card" else "СБП"
    buttons = []
    if order.status == "pending_payment":
        buttons.append([("❌ Отменить заказ", f"cancel:{order.id}")])
    buttons += [[("📦 Мои заказы", "orders:1")], nav("profile")]
    discounts = []
    if order.promo_discount_minor:
        discounts.append(f"Промокод {escape(order.promo_code or '')}: −{catalog_service.money(order.promo_discount_minor)}")
    if order.bonus_used_minor:
        discounts.append(f"Списано бонусов: −{catalog_service.money(order.bonus_used_minor)}")
    if order.bonus_accrued_minor and order.status == "paid":
        discounts.append(f"Начислено бонусов: +{catalog_service.money(order.bonus_accrued_minor)}")
    discount_text = ("\n" + "\n".join(discounts)) if discounts else ""
    await render(callback, f"<b>Заказ {escape(order.public_number)}</b>\nСтатус: <b>{status_text(order.status)}</b>\nОплата: {method}\n\n{items}\n\nСумма товаров: <b>{catalog_service.money(order.subtotal_minor or order.total_minor)}</b>{discount_text}\nИтого: <b>{catalog_service.money(order.total_minor)}</b>", kb(*buttons))


@router.callback_query(F.data.startswith("paid:"))
async def payment_callback_disabled(callback: CallbackQuery, state: FSMContext) -> None:
    await show_checkout(callback, state)


@router.callback_query(F.data.startswith("cancel:"))
async def transition(callback: CallbackQuery) -> None:
    _, raw_id = callback.data.split(":")
    target = "cancelled"
    try:
        async with SessionLocal() as session, session.begin():
            user = await user_for(session, callback)
            order, changed = await order_service.transition_order(session, int(raw_id), user.id, target)
            if not order:
                await callback.answer("Заказ не найден", show_alert=True)
                return
            order = await order_service.get_order(session, order.id, user.id)
        await order_details(callback, order)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery) -> None:
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        text = f"<b>Личный кабинет</b>\nИмя: {escape(user.first_name or 'не указано')}\nUsername: @{escape(user.username or 'не указан')}\nTelegram ID: <code>{user.telegram_id}</code>\nРегистрация: {user.created_at.strftime('%d.%m.%Y') if user.created_at else 'сейчас'}\n\n<b>Бонусный баланс: {catalog_service.money(user.bonus_balance_minor)}</b>\nНачисляем 2% от суммы каждого успешно оплаченного заказа. Бонусами можно оплатить до 100% следующего заказа."
    await render(callback, text, kb([("📦 Мои заказы", "orders:1")], [("💬 Поддержка", "support")], nav()))


@router.callback_query(F.data.startswith("orders:"))
async def orders(callback: CallbackQuery) -> None:
    page = max(1, int(callback.data.split(":")[1]))
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        total = await session.scalar(select(func.count()).select_from(Order).where(Order.user_id == user.id)) or 0
        rows = list((await session.scalars(select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc()).offset((page-1)*PAGE_SIZE).limit(PAGE_SIZE))).all())
    if not rows:
        await render(callback, "Заказов пока нет.", kb(nav("profile")))
        return
    pages = math.ceil(total / PAGE_SIZE)
    buttons = [[(f"{x.public_number} · {status_text(x.status)}", f"order:{x.id}")] for x in rows]
    buttons.append([("⬅️", f"orders:{page-1}") if page > 1 else (f"{page}/{pages}", "noop"), ("➡️", f"orders:{page+1}") if page < pages else (f"{page}/{pages}", "noop")])
    buttons.append(nav("profile"))
    await render(callback, "<b>Мои заказы</b>", kb(*buttons))


@router.callback_query(F.data.startswith("order:"))
async def order(callback: CallbackQuery) -> None:
    order_id = int(callback.data.split(":")[1])
    async with SessionLocal() as session, session.begin():
        user = await user_for(session, callback)
        entity = await order_service.get_order(session, order_id, user.id)
    if not entity:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    await order_details(callback, entity)


@router.callback_query((F.data == "documents") | (F.data == "rules"))
async def documents(callback: CallbackQuery) -> None:
    await render(
        callback,
        "<b>Документы и правила</b>\n"
        "Здесь всегда доступны условия использования, обработки данных, цены и правила оплаты.",
        documents_keyboard(),
    )


@router.callback_query(F.data.startswith("doc:"))
async def document(callback: CallbackQuery) -> None:
    code = callback.data.split(":", 1)[1]
    texts = {
        "privacy": privacy_policy,
        "terms": terms_of_service,
        "prices": prices_and_tariffs,
        "payments": payment_and_refunds,
        "rules": service_rules,
    }
    builder = texts.get(code)
    if not builder:
        await callback.answer("Документ не найден", show_alert=True)
        return
    await render(callback, builder(settings), kb([("⬅️ К документам", "documents")], [("🏠 Главное меню", "menu")]))


@router.callback_query(F.data == "support")
async def support(callback: CallbackQuery) -> None:
    contact = escape(settings.support_contact) if settings.has_public_support_contact else "Напишите обращение через кнопку «Новое обращение»."
    await render(callback, f"<b>Поддержка</b>\n{contact}\n\nЕсли заказ уже создан, укажите его номер — так мы быстрее найдём историю. Никогда не отправляйте пароли, коды подтверждения или реквизиты карты.", kb(nav()))


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()
