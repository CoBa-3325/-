"""Background subscription warning scheduler."""
from __future__ import annotations
import asyncio,logging
from datetime import datetime,timezone,timedelta
logger=logging.getLogger(__name__)
class Scheduler:
    def __init__(self,repository,bot,warning_days=1):
        self.repository=repository;self.bot=bot;self.warning_days=warning_days;self._stop=asyncio.Event();self._last_chat_check=None

    @staticmethod
    def _chat_unavailable(exc):
        """Return True only for errors that indicate the bot can no longer access the chat."""
        status=getattr(exc,'status_code',None) or getattr(exc,'status',None) or getattr(exc,'code',None)
        if status in (403,404):
            return True
        response=getattr(exc,'response',None)
        response_status=getattr(response,'status_code',None)
        if response_status in (403,404):
            return True
        text=str(exc).lower()
        markers=('chat not found','chat_not_found','not found','forbidden','permission denied','access denied','bot is not a member','bot not a member','not a member')
        return any(marker in text for marker in markers)

    async def check_chats(self):
        """Verify saved chats once per day and remove chats the bot no longer has access to."""
        rows=self.repository.list_group_chats()
        for row in rows:
            chat_id=row['chat_id']
            try:
                chat=await self.bot.get_chat_by_id(int(chat_id))
                if chat is None:
                    removed=self.repository.delete_chat(chat_id)
                    if removed:
                        logger.info('CHAT_REMOVED_FROM_DB chat_id=%s reason=not_found',chat_id)
            except Exception as exc:
                if self._chat_unavailable(exc):
                    removed=self.repository.delete_chat(chat_id)
                    logger.info('CHAT_REMOVED_FROM_DB chat_id=%s reason=unavailable error=%s removed=%s',chat_id,exc,removed)
                else:
                    logger.warning('CHAT_CHECK_FAILED chat_id=%s error=%s',chat_id,exc)

    async def run(self):
        # Сразу после запуска проверяем все сохранённые беседы.
        try:
            await self.check_chats()
            self._last_chat_check = datetime.now(timezone.utc)
        except Exception:
            logger.exception('CHAT_CHECK_STARTUP_FAILED')

        while not self._stop.is_set():
            try:
                if self.warning_days:
                    now=datetime.now(timezone.utc);limit=now+timedelta(days=self.warning_days)
                    for row in self.repository.list_subscriptions_expiring(now,limit):
                        end=datetime.fromisoformat(row['ends_at']);
                        if self.repository.mark_subscription_warning(row['user_id'],row['ends_at']):
                            try:await self.bot.send_message(chat_id=row['user_id'],text=f'До конца подписки осталось {max(1,(end-now).days)} дней, если вы не продлите подписку то оставшиеся токены сгорят')
                            except Exception:logger.exception('SUBSCRIPTION_WARNING_SEND_FAILED user_id=%s',row['user_id'])
            except Exception:logger.exception('SUBSCRIPTION_WARNING_FAILED')

            try:
                now=datetime.now(timezone.utc)
                if self._last_chat_check is None or (now-self._last_chat_check).total_seconds() >= 86400:
                    await self.check_chats()
                    self._last_chat_check=now
            except Exception:
                logger.exception('CHAT_CHECK_FAILED')

            try:await asyncio.wait_for(self._stop.wait(),timeout=3600)
            except asyncio.TimeoutError:pass
    def stop(self):self._stop.set()
