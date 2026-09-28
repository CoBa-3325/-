"""Application composition and MAX/YooKassa startup."""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from maxapi import Bot, Dispatcher

from ai.summarizer import Summarizer
from ai.yandex_gpt import YandexGPT
from bot.handlers import register_handlers
from config.settings import Settings
from database.connection import connect
from database.repository import Repository
from services.cabinet_service import CabinetService
from services.cooldown_service import CooldownService
from services.message_service import MessageService
from services.payment_service import PaymentService
from services.promotion_service import PromotionService
from services.role_service import RoleService
from services.settings_service import SettingsService
from services.subscription_service import SubscriptionService
from services.summary_service import SummaryService
from services.token_service import TokenService
from scheduler.scheduler import Scheduler


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class Services:
    pass


async def _disable_webhooks_for_polling(settings):
    """Long Polling не работает одновременно с активным Webhook MAX."""
    url = "https://platform-api2.max.ru/subscriptions"
    headers = {"Authorization": settings.max_bot_token}
    timeout = aiohttp.ClientTimeout(total=10)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                data = await response.json(content_type=None)

            subscriptions = (
                data.get("subscriptions", [])
                if isinstance(data, dict)
                else []
            )

            for item in subscriptions:
                webhook_url = (
                    item.get("url")
                    if isinstance(item, dict)
                    else None
                )

                if not webhook_url:
                    continue

                async with session.delete(
                    url,
                    params={"url": webhook_url},
                    headers=headers,
                ) as response:
                    result = await response.json(content_type=None)

                    logger.info(
                        "MAX_WEBHOOK_REMOVED url=%s success=%s status=%s",
                        webhook_url,
                        result.get("success")
                        if isinstance(result, dict)
                        else None,
                        response.status,
                    )

    except Exception:
        logger.exception("MAX_WEBHOOK_CLEANUP_FAILED")


def build_services(settings):
    connection = connect(settings.db_path)
    repository = Repository(connection)

    initial = repository.ensure_initial_admin_promo()

    if initial:
        logger.warning(
            "INITIAL_ADMIN_PROMO_CREATED code=%s",
            initial["code"],
        )

    roles = RoleService(repository)

    gpt = YandexGPT(
        settings.yc_api_key,
        settings.yc_folder_id,
        settings.yandex_gpt_model,
    )

    summarizer = Summarizer(
        gpt,
        settings.max_prompt_chars,
    )

    message = MessageService(repository)
    summary = SummaryService(repository, summarizer)

    tokens = TokenService(
        repository,
        settings.free_tokens_per_user,
    )

    subscription = SubscriptionService(
        repository,
        roles,
    )

    promotion = PromotionService(
        repository,
        settings,
        roles,
    )

    cabinet = CabinetService(
        repository,
        subscription,
        tokens,
        roles,
    )

    payment = PaymentService(
        repository,
        settings,
        promotion,
        subscription,
        tokens,
    )

    cooldown = CooldownService(
        repository,
        settings.request_cooldown_minutes,
    )

    services = Services()

    services.connection = connection
    services.repository = repository
    services.message = message
    services.summary = summary
    services.settings = SettingsService(repository)
    services.tokens = tokens
    services.subscription = subscription
    services.promotion = promotion
    services.cabinet = cabinet
    services.payment = payment
    services.roles = roles
    services.cooldown = cooldown
    services.settings_config = settings

    return services


async def main():
    settings = Settings.from_env()

    logger.info("Configuration loaded")

    services = build_services(settings)

    bot = Bot(settings.max_bot_token)

    scheduler = Scheduler(
        services.repository,
        bot,
        settings.subscription_expiration_warning_days,
    )

    scheduler_task = asyncio.create_task(
        scheduler.run()
    )

    dp = Dispatcher()

    register_handlers(
        dp,
        services,
    )

    logger.info(
        "MAX bot starting mode=%s",
        settings.max_mode,
    )

    try:
        if settings.max_mode == "webhook":
            if (
                not settings.max_webhook_url
                or not settings.max_webhook_secret
            ):
                raise RuntimeError(
                    "MAX_WEBHOOK_URL and MAX_WEBHOOK_SECRET "
                    "are required for webhook mode"
                )

            from fastapi import FastAPI, Request
            from fastapi.responses import JSONResponse
            import uvicorn
            from maxapi.webhook.fastapi import FastAPIMaxWebhook

            webhook = FastAPIMaxWebhook(
                dp=dp,
                bot=bot,
                secret=settings.max_webhook_secret,
            )

            app = FastAPI(
                lifespan=webhook.lifespan
            )

            webhook.setup(
                app,
                path="/webhook",
            )

            @app.post("/yookassa/webhook")
            async def yookassa_webhook(request: Request):
                payload = await request.json()

                obj = payload.get("object", {}) or {}

                try:
                    await services.payment.handle_webhook_payment(
                        obj.get("id", "")
                    )

                    return JSONResponse(
                        {"ok": True}
                    )

                except Exception:
                    logger.exception(
                        "YooKassa webhook failed"
                    )

                    return JSONResponse(
                        {"ok": False},
                        status_code=500,
                    )

            @app.get("/health")
            async def health():
                return {"status": "ok"}

            await bot.subscribe_webhook(
                url=settings.max_webhook_url,
                secret=settings.max_webhook_secret,
                update_types=[
                    "message_created",
                    "message_callback",
                    "message_removed",
                    "message_edited",
                    "bot_started",
                ],
            )

            await uvicorn.Server(
                uvicorn.Config(
                    app,
                    host=settings.max_webhook_host,
                    port=settings.max_webhook_port,
                    log_level="info",
                )
            ).serve()

        else:
            # Если раньше использовался webhook,
            # удаляем его перед запуском Long Polling.
            await _disable_webhooks_for_polling(settings)

            await dp.start_polling(bot)

    except asyncio.CancelledError:
        logger.info("MAX bot cancelled")

    finally:
        scheduler.stop()

        scheduler_task.cancel()

        services.connection.close()

        await bot.close_session()

        logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info("MAX bot stopped by user")