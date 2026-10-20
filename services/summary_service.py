"""Подготовка контекста и вызов LLM для формирования сводки."""

from datetime import datetime, timezone

from services.period_service import period_for_interval
from schemas.summary import SummaryPeriod

class SummaryService:
    def __init__(self, repository, summarizer):
        self.repository = repository
        self.summarizer = summarizer

    async def generate(self, chat_id, interval='weekly', custom_days=None, start=None, end=None, user_ids=None):
        period = SummaryPeriod(start, end) if start is not None and end is not None else period_for_interval(interval, custom_days=custom_days)
        rows = self.repository.get_messages(chat_id, period.start, period.end, user_ids=user_ids)
        if not rows:
            return 'За выбранный период сообщений нет.'
        parts = []
        dated_rows = []
        for row in rows:
            source = 'опрос' if row['source'] == 'poll' else 'текст'
            timestamp = datetime.fromisoformat(row['timestamp'])
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            dated_rows.append((timestamp.astimezone(timezone.utc), row, source))
        dated_rows.sort(key=lambda item: item[0])
        parts.append(
            'КОНТЕКСТ ВРЕМЕНИ: текущая дата формирования отчёта — '
            f'{datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")}. '
            'У каждой реплики ниже указаны дата и время отправки в UTC. '
            'Слова «сегодня», «вчера», «завтра» и другие относительные даты '
            'относятся к дате сообщения, в котором они написаны, а не к дате отчёта. '
            'Не называй старое предложение решением, принятым сегодня. '
            'В раздел «Что решили» включай только явно подтверждённые решения; '
            'указывай дату решения/обсуждения, если она известна. '
            'Если подтверждение, дата или актуальность неясны, обозначь это и не '
            'представляй старую договорённость как текущую.'
        )
        for timestamp, row, source in dated_rows:
            parts.append(
                f"[{timestamp.strftime('%d.%m.%Y %H:%M UTC')}] "
                f"{row['user_name']} ({source}): {row['text']}"
            )
        return await self.summarizer.summarize('\n'.join(parts))

    def save_run(self, chat_id, start, end, text):
        self.repository.save_summary_run(chat_id, start, end, text)
