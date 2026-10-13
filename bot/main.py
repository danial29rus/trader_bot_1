import asyncio
import logging
from time import perf_counter

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy import text

from bot.handlers import admin, store, support
from config import get_settings
from database.session import engine


logger = logging.getLogger(__name__)


class SlowUpdateMiddleware(BaseMiddleware):
    """Expose slow Telegram API/database work in container logs."""

    async def __call__(self, handler, event, data):
        started = perf_counter()
        try:
            return await handler(event, data)
        finally:
            elapsed = perf_counter() - started
            if elapsed >= 1:
                logger.warning("Slow update handled in %.2f seconds", elapsed)


async def keep_warm(bot: Bot, interval: float = 60) -> None:
    """Keep Telegram API and database connections alive during idle periods."""
    while True:
        await asyncio.sleep(interval)
        started = perf_counter()
        try:
            await bot.get_me()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            logger.exception("Keep-warm ping failed")
        else:
            elapsed = perf_counter() - started
            if elapsed >= 1:
                logger.warning("Slow keep-warm ping: %.2f seconds", elapsed)


async def main() -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is empty; copy .env.example to .env and set it")
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(admin.router)
    dp.include_router(support.router)
    dp.include_router(store.router)
    dp.update.outer_middleware(SlowUpdateMiddleware())
    await bot.delete_webhook(drop_pending_updates=False)
    warm_task = asyncio.create_task(keep_warm(bot))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        warm_task.cancel()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
