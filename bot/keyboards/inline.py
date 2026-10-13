from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=data) for text, data in row] for row in rows])


def main_menu() -> InlineKeyboardMarkup:
    """Only show storefront sections that this curated catalogue actually imports."""
    rows = [
        [("🎮 Игры Steam", "cat_type:steam_gift")],
        [("🎁 Подарочные карты", "cat_type:gift_card")],
        [("🔎 Поиск", "search"), ("🛒 Корзина", "cart")],
        [("👤 Личный кабинет", "profile")],
        [("📄 Документы", "documents"), ("💬 Поддержка", "support")],
    ]
    return kb(*rows)


def nav(back: str = "menu") -> list[tuple[str, str]]:
    return [("⬅️ Назад", back), ("🏠 Главное меню", "menu")]
