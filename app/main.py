"""Application composition and MAX/YooKassa startup."""
from __future__ import annotations
import asyncio,logging
from maxapi import Bot,Dispatcher
from app.ai.speechkit import SpeechKitTranscriber
from app.ai.summarizer import Summarizer
from app.ai.yandex_gpt import YandexGPT
from app.bot.handlers import register_handlers
from app.config.settings import Settings
from app.database.connection import connect
from app.database.repository import Repository
from app.services.message_service import MessageService
from app.services.settings_service import SettingsService
from app.services.summary_service import SummaryService
from app.services.voice_service import VoiceService
from app.services.token_service import TokenService
from app.services.subscription_service import SubscriptionService
from app.services.promotion_service import PromotionService
from app.services.cabinet_service import CabinetService
from app.services.payment_service import PaymentService
from app.services.role_service import RoleService
from app.services.cooldown_service import CooldownService
from app.scheduler.scheduler import Scheduler
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s');logger=logging.getLogger(__name__)
class Services:pass

def build_services(settings):
    connection=connect(settings.db_path);repository=Repository(connection)
    initial=repository.ensure_initial_admin_promo()
    if initial:logger.warning('INITIAL_ADMIN_PROMO_CREATED code=%s',initial['code'])
    roles=RoleService(repository);gpt=YandexGPT(settings.yc_api_key,settings.yc_folder_id,settings.yandex_gpt_model);summarizer=Summarizer(gpt,settings.max_prompt_chars)
    message=MessageService(repository);summary=SummaryService(repository,summarizer);tokens=TokenService(repository,settings.free_tokens_per_user);subscription=SubscriptionService(repository,roles);promotion=PromotionService(repository,settings,roles);cabinet=CabinetService(repository,subscription,tokens,roles);payment=PaymentService(repository,settings,promotion,subscription,tokens);cooldown=CooldownService(repository,settings.request_cooldown_minutes)
    voice=VoiceService(SpeechKitTranscriber(settings.yc_api_key,settings.stt_language),settings.max_voice_file_mb*1024*1024,settings.max_bot_token,settings.max_voice_download_timeout,settings.max_voice_download_retries,settings.max_ca_bundle)
    s=Services();s.connection=connection;s.repository=repository;s.message=message;s.summary=summary;s.settings=SettingsService(repository);s.tokens=tokens;s.subscription=subscription;s.promotion=promotion;s.cabinet=cabinet;s.payment=payment;s.voice=voice;s.roles=roles;s.cooldown=cooldown;s.settings_config=settings;return s

async def main():
    settings=Settings.from_env();logger.info('Configuration loaded');services=build_services(settings);bot=Bot(settings.max_bot_token);scheduler=Scheduler(services.repository,bot,settings.subscription_expiration_warning_days);scheduler_task=asyncio.create_task(scheduler.run());dp=Dispatcher();register_handlers(dp,services);logger.info('MAX bot starting mode=%s',settings.max_mode)
    try:
        if settings.max_mode=='webhook':
            if not settings.max_webhook_url or not settings.max_webhook_secret:raise RuntimeError('MAX_WEBHOOK_URL and MAX_WEBHOOK_SECRET are required for webhook mode')
            from fastapi import FastAPI,Request
            from fastapi.responses import JSONResponse
            import uvicorn
            from maxapi.webhook.fastapi import FastAPIMaxWebhook
            webhook=FastAPIMaxWebhook(dp=dp,bot=bot,secret=settings.max_webhook_secret);app=FastAPI(lifespan=webhook.lifespan);webhook.setup(app,path='/webhook')
            @app.post('/yookassa/webhook')
            async def yookassa_webhook(request:Request):
                payload=await request.json();obj=payload.get('object',{}) or {}
                try:await services.payment.handle_webhook_payment(obj.get('id',''));return JSONResponse({'ok':True})
                except Exception:logger.exception('YooKassa webhook failed');return JSONResponse({'ok':False},status_code=500)
            @app.get('/health')
            async def health():return {'status':'ok'}
            await bot.subscribe_webhook(url=settings.max_webhook_url,secret=settings.max_webhook_secret,update_types=['message_created','message_callback','bot_started']);await uvicorn.Server(uvicorn.Config(app,host=settings.max_webhook_host,port=settings.max_webhook_port,log_level='info')).serve()
        else:await dp.start_polling(bot)
    except asyncio.CancelledError:logger.info('MAX bot cancelled')
    finally:scheduler.stop();scheduler_task.cancel();services.connection.close();await bot.close_session();logger.info('Shutdown complete')
if __name__=='__main__':
    try:asyncio.run(main())
    except KeyboardInterrupt:logger.info('MAX bot stopped by user')
