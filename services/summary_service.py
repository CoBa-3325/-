"""Подготовка контекста и вызов LLM для формирования сводки."""

from datetime import datetime, timezone
from  schemas.summary import SummaryPeriod
from  services.period_service import period_for_interval

class SummaryService:
    def __init__(self,repository,summarizer): self.repository=repository; self.summarizer=summarizer
    async def generate(self,chat_id,interval='weekly',custom_days=None,start=None,end=None):
        period=SummaryPeriod(start,end) if start is not None and end is not None else period_for_interval(interval,custom_days=custom_days)
        rows=self.repository.get_messages(chat_id,period.start,period.end)
        if not rows:return 'За выбранный период сообщений нет.'
        parts=[]
        for row in rows:
            source={'voice':'голос','poll':'опрос'}.get(row['source'],'текст')
            parts.append(f"[{row['timestamp']}] {row['user_name']} ({source}): {row['text']}")
        return await self.summarizer.summarize('\n'.join(parts))
    def save_run(self,chat_id,start,end,text): self.repository.save_summary_run(chat_id,start,end,text)
