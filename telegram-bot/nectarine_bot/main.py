"""Telegram bot process entry point."""

import asyncio
import logging

from aiogram import Bot, Dispatcher

from nectarine_bot.api import PanelApi
from nectarine_bot.config import settings
from nectarine_bot.handlers import router

CONFIG_RETRY_SECONDS = 10


def apply_runtime_config(runtime: dict[str, object]) -> bool:
    """Apply persisted bot settings and report whether polling can start."""
    token = runtime.get("bot_token")
    owner_id = runtime.get("owner_id")
    max_file_bytes = runtime.get("max_file_bytes")
    if isinstance(token, str):
        settings.telegram_bot_token = token
    if isinstance(owner_id, int) or owner_id is None:
        settings.telegram_owner_id = owner_id
    if isinstance(max_file_bytes, int) and max_file_bytes > 0:
        settings.telegram_max_file_bytes = max_file_bytes
    return bool(settings.telegram_bot_token and settings.telegram_owner_id is not None)


async def wait_for_configuration(api: PanelApi) -> None:
    """Wait until persisted or environment Telegram credentials are available."""
    logger = logging.getLogger(__name__)
    while True:
        try:
            runtime = await api.runtime_config()
        except Exception:
            logger.warning(
                "Unable to load persisted Telegram settings; using environment fallback"
            )
        else:
            if apply_runtime_config(runtime):
                return
        if settings.telegram_bot_token and settings.telegram_owner_id is not None:
            return
        logger.warning("Telegram bot is waiting for token and owner ID configuration")
        await asyncio.sleep(CONFIG_RETRY_SECONDS)


async def run() -> None:
    """Wait for Telegram configuration and start long polling."""
    api = PanelApi()
    try:
        await wait_for_configuration(api)
        bot = Bot(settings.telegram_bot_token)
        dispatcher = Dispatcher()
        dispatcher.include_router(router)
        try:
            await dispatcher.start_polling(bot, api=api)
        finally:
            await bot.session.close()
    finally:
        await api.close()


def main() -> None:
    """Configure logging and run the bot event loop."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
