"""Background subscription warning scheduler."""
from __future__ import annotations
import asyncio,logging
from datetime import datetime,timezone,timedelta
logger=logging.getLogger(__name__)
class Scheduler:
    def __init__(self,repository,bot,warning_days=1):
        self.repository=repository;self.bot=bot;self.warning_days=warning_days;self._stop=asyncio.Event()
    async def run(self):
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
            try:await asyncio.wait_for(self._stop.wait(),timeout=3600)
            except asyncio.TimeoutError:pass
    def stop(self):self._stop.set()
