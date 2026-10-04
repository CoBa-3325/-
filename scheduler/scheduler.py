"""Background subscription warning and chat membership scheduler."""
from __future__ import annotations
import asyncio, logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

class Scheduler:
    def __init__(self, repository, bot, warning_days=1):
        self.repository = repository
        self.bot = bot
        self.warning_days = warning_days
        self._stop = asyncio.Event()
        self._last_chat_check = None

    @staticmethod
    def _chat_unavailable(exc):
        status = getattr(exc, 'code', None) or getattr(exc, 'status_code', None) or getattr(exc, 'status', None)
        if status in (403, 404):
            return True
        response = getattr(exc, 'response', None)
        response_status = getattr(response, 'status_code', None)
        if response_status in (403, 404):
            return True
        text = str(exc).lower()
        markers = (
            'chat not found', 'chat_not_found', 'not found', 'forbidden',
            'permission denied', 'access denied', 'not a member',
            'bot is not a member', 'bot not a member', 'membership',
        )
        return any(marker in text for marker in markers)

    async def check_chats(self):
        """Check actual bot membership, not just chat existence."""
        rows = self.repository.list_group_chats()
        checked = 0
        removed = 0
        for row in rows:
            chat_id = row['chat_id']
            try:
                # This endpoint specifically checks whether the current bot
                # is a member of the chat. get_chat_by_id() is not enough:
                # the chat may still exist after the bot was removed.
                membership = await self.bot.get_me_from_chat(int(chat_id))
                if membership is None:
                    if self.repository.delete_chat(chat_id):
                        removed += 1
                        logger.info('CHAT_REMOVED_FROM_DB chat_id=%s reason=no_membership', chat_id)
                else:
                    checked += 1
            except Exception as exc:
                if self._chat_unavailable(exc):
                    if self.repository.delete_chat(chat_id):
                        removed += 1
                        logger.info('CHAT_REMOVED_FROM_DB chat_id=%s reason=no_membership error=%s', chat_id, exc)
                else:
                    logger.warning('CHAT_CHECK_FAILED chat_id=%s error=%s', chat_id, exc)
        logger.info('CHAT_MEMBERSHIP_CHECK_FINISHED total=%s active=%s removed=%s', len(rows), checked, removed)

    async def run(self):
        # Проверяем беседы сразу после запуска.
        try:
            await self.check_chats()
            self._last_chat_check = datetime.now(timezone.utc)
        except Exception:
            logger.exception('CHAT_CHECK_STARTUP_FAILED')

        while not self._stop.is_set():
            try:
                if self.warning_days:
                    now = datetime.now(timezone.utc)
                    limit = now + timedelta(days=self.warning_days)
                    for row in self.repository.list_subscriptions_expiring(now, limit):
                        end = datetime.fromisoformat(row['ends_at'])
                        if self.repository.mark_subscription_warning(row['user_id'], row['ends_at']):
                            try:
                                await self.bot.send_message(
                                    chat_id=row['user_id'],
                                    text=f'До конца подписки осталось {max(1, (end-now).days)} дней, если вы не продлите подписку то оставшиеся токены сгорят'
                                )
                            except Exception:
                                logger.exception('SUBSCRIPTION_WARNING_SEND_FAILED user_id=%s', row['user_id'])
            except Exception:
                logger.exception('SUBSCRIPTION_WARNING_FAILED')

            try:
                now = datetime.now(timezone.utc)
                if self._last_chat_check is None or (now - self._last_chat_check).total_seconds() >= 86400:
                    await self.check_chats()
                    self._last_chat_check = now
            except Exception:
                logger.exception('CHAT_CHECK_FAILED')

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=3600)
            except asyncio.TimeoutError:
                pass

    def stop(self):
        self._stop.set()
