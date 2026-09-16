"""Main entrypoint for sd-notif service."""
import asyncio
import logging
import sys
import httpx
from config import settings
from elma_client import ElmaClient
from bot_handler import BotHandler
from watchdog import WatchdogService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("sd_notif.main")


async def tg_polling_loop(bot: BotHandler, elma_client: httpx.AsyncClient):
    """Dedicated fast Telegram polling loop using proxy if configured."""
    logger.info(f"Starting dedicated Telegram polling loop (Proxy: {settings.TELEGRAM_PROXY})...")
    proxy_url = settings.TELEGRAM_PROXY if settings.TELEGRAM_PROXY else None
    async with httpx.AsyncClient(proxy=proxy_url, verify=False, timeout=15.0) as tg_client:
        while True:
            try:
                await bot.poll_updates(elma_client, tg_client)
                await asyncio.sleep(0.3)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"TG poll error: {e}")
                await asyncio.sleep(2.0)


async def watchdog_loop(watchdog: WatchdogService, elma_client: httpx.AsyncClient):
    """Background periodic watchdog check loop."""
    logger.info("Starting Watchdog background loop...")
    tg_proxy = settings.TELEGRAM_PROXY if settings.TELEGRAM_PROXY else None
    async with httpx.AsyncClient(proxy=tg_proxy, verify=False, timeout=15.0) as tg_client:
        while True:
            try:
                await watchdog.check_discrepancies(elma_client, tg_client)
                await watchdog.check_shift_handover(elma_client, tg_client)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Watchdog check cycle error: {e}")
            await asyncio.sleep(settings.CHECK_INTERVAL_SECONDS)


async def main():
    logger.info("Starting sd-notif Service...")
    logger.info(f"ELMA Instance: {settings.ELMA_BASE_URL}")
    logger.info(f"Telegram Group ID: {settings.TELEGRAM_GROUP_CHAT_ID}")
    logger.info(f"Grace Period: {settings.GRACE_PERIOD_MINUTES} min, Check Interval: {settings.CHECK_INTERVAL_SECONDS} sec")
    logger.info(f"Shift Times: {settings.SHIFT_TIMES} (UTC+{settings.TIMEZONE_OFFSET_HOURS})")
    logger.info(f"Supervisors: IDs={settings.SUPERVISOR_USER_IDS}, Names={settings.SUPERVISOR_USERNAMES}")
    logger.info(f"Telegram Proxy: {settings.TELEGRAM_PROXY}")

    elma = ElmaClient()
    bot = BotHandler(elma)
    watchdog = WatchdogService(elma, bot)

    async with httpx.AsyncClient(verify=False, timeout=25.0) as elma_client:
        await asyncio.gather(
            tg_polling_loop(bot, elma_client),
            watchdog_loop(watchdog, elma_client)
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("sd-notif terminated.")
