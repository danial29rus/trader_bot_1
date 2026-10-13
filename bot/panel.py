"""Single-panel UI: every screen edits one bot message instead of adding new ones.

A screen is text + keyboard + an optional section banner. The panel is edited in
place when possible (caption, media or text) and recreated only when Telegram
cannot convert between a photo and a text message or the old one is gone.
"""
from __future__ import annotations

import asyncio
import logging
import re
from html import unescape
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message

logger = logging.getLogger(__name__)

BANNER_DIR = Path("assets/banners")
CAPTION_LIMIT = 1024
_TAG = re.compile(r"<[^>]+>")

_file_ids: dict[str, str] = {}
_unique_to_banner: dict[str, str] = {}
_upload_lock = asyncio.Lock()
# Supplier image URLs Telegram failed to fetch; those screens fall back to the section banner.
_bad_urls: set[str] = set()
URL_PREFIX = "url:"
# chat_id -> (message_id, banner key or None for a text panel); lost on restart, then a new panel is sent.
_panels: dict[int, tuple[int, str | None]] = {}
_UNKNOWN_PHOTO = "?"


def _visible_length(html: str) -> int:
    return len(unescape(_TAG.sub("", html)).encode("utf-16-le")) // 2


def _usable_banner(banner: str | None, text: str) -> str | None:
    if not banner or _visible_length(text) > CAPTION_LIMIT:
        return None
    if banner.startswith(URL_PREFIX):
        return banner if banner not in _bad_urls else None
    return banner if (BANNER_DIR / f"{banner}.jpg").exists() else None


def _remember(chat_id: int, sent: Message | bool, banner: str | None) -> None:
    if not isinstance(sent, Message):
        return
    if banner and sent.photo:
        photo = sent.photo[-1]
        _file_ids[banner] = photo.file_id
        _unique_to_banner[photo.file_unique_id] = banner
    _panels[chat_id] = (sent.message_id, banner)


def _banner_of(message: Message) -> str | None:
    if not message.photo:
        return None
    return _unique_to_banner.get(message.photo[-1].file_unique_id, _UNKNOWN_PHOTO)


def _is_not_modified(exc: TelegramBadRequest) -> bool:
    return "message is not modified" in str(exc).lower()


async def _with_banner(banner: str, action):
    """Run a send/edit with a cached file_id, uploading the banner only once."""
    if banner in _file_ids:
        return await action(_file_ids[banner])
    if banner.startswith(URL_PREFIX):
        # Telegram downloads the URL itself; nothing is uploaded from here.
        return await action(banner.removeprefix(URL_PREFIX))
    async with _upload_lock:
        if banner in _file_ids:
            return await action(_file_ids[banner])
        return await action(FSInputFile(BANNER_DIR / f"{banner}.jpg"))


async def _edit(bot: Bot, chat_id: int, message_id: int, current: str | None, text: str, markup, banner: str | None, strict: bool = False) -> bool:
    """Edit the existing panel; False means it has to be replaced by a new message."""
    try:
        if banner is None and current is None:
            sent = await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id, reply_markup=markup, parse_mode="HTML")
        elif banner is not None and banner == current:
            sent = await bot.edit_message_caption(caption=text, chat_id=chat_id, message_id=message_id, reply_markup=markup, parse_mode="HTML")
        elif banner is not None and current is not None:
            sent = await _with_banner(banner, lambda media: bot.edit_message_media(
                media=InputMediaPhoto(media=media, caption=text, parse_mode="HTML"),
                chat_id=chat_id, message_id=message_id, reply_markup=markup,
            ))
        else:
            return False
    except TelegramBadRequest as exc:
        if _is_not_modified(exc):
            return True
        if strict and current is not None and banner is not None:
            # The photo swap failed because of the new picture itself; let the caller fall back.
            raise
        logger.info("Panel edit failed, sending a new one: %s", exc)
        return False
    _remember(chat_id, sent, banner)
    if not isinstance(sent, Message):
        _panels[chat_id] = (message_id, banner)
    return True


async def _delete(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        # Messages older than 48 hours cannot be deleted; leaving one is harmless.
        pass


async def show(
    bot: Bot,
    chat_id: int,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
    banner: str | None = None,
    message: Message | None = None,
    new: bool = False,
    image: str | None = None,
) -> None:
    """Render a screen into the chat's panel.

    `message` is the panel the user clicked (for callbacks); without it the last
    known panel of the chat is used. `new=True` always sends a fresh panel at the
    bottom of the chat and removes the previous one (used by /start). `image` is
    an external picture URL (product cover) shown instead of the banner.
    """
    if image and _usable_banner(URL_PREFIX + image, text):
        try:
            await _render(bot, chat_id, text, markup, URL_PREFIX + image, message, new, strict=True)
            return
        except TelegramBadRequest as exc:
            logger.info("Image %s rejected by Telegram, using the banner: %s", image, exc)
            _bad_urls.add(URL_PREFIX + image)
    await _render(bot, chat_id, text, markup, _usable_banner(banner, text), message, new)


async def _render(bot: Bot, chat_id: int, text: str, markup, banner: str | None, message: Message | None, new: bool, strict: bool = False) -> None:
    if message is not None:
        known = _panels.get(chat_id)
        current = known if known and known[0] == message.message_id else (message.message_id, _banner_of(message))
    else:
        current = _panels.get(chat_id)

    if current and not new and await _edit(bot, chat_id, current[0], current[1], text, markup, banner, strict):
        return

    if banner:
        sent = await _with_banner(banner, lambda media: bot.send_photo(chat_id, media, caption=text, reply_markup=markup, parse_mode="HTML"))
    else:
        sent = await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")
    _remember(chat_id, sent, banner)
    if current and current[0] != sent.message_id:
        await _delete(bot, chat_id, current[0])


def clicked(callback: CallbackQuery) -> Message | None:
    """The clicked panel; messages older than 48 hours arrive as InaccessibleMessage and are replaced instead."""
    return callback.message if isinstance(callback.message, Message) else None


async def consume(message: Message) -> None:
    """Remove a user's input message (search query, promo code) once it has been handled."""
    await _delete(message.bot, message.chat.id, message.message_id)
