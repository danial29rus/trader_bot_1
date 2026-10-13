from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows])


def main_menu() -> InlineKeyboardMarkup:
    """Only show storefront sections that this curated catalogue actually imports."""
    rows = [
        [("⭐ Telegram", "cat_type:telegram"), ("⚡ Пополнение игр", "cat_type:game_topup")],
        [("🎮 Steam", "cat_type:steam_gift"), ("🎁 Подарочные карты", "cat_type:gift_card")],
        [("🔎 Поиск", "search"), ("🛒 Корзина", "cart")],
        [("👤 Кабинет", "profile"), ("📦 Заказы", "orders:1")],
        [("💬 Поддержка", "support"), ("📄 Документы", "documents")],
    ]
    return kb(*rows)


def nav(back: str = "menu") -> list[tuple[str, str]]:
    return [("⬅️ Назад", back), ("🏠 Меню", "menu")]
