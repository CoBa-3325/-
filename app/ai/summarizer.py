"""Подготовка длинного контекста и многошаговая суммаризация."""

from app.ai.prompts import SYSTEM_PROMPT,SUMMARY_PROMPT
class Summarizer:
    """Не допускает переполнения prompt: длинный контекст разбивается на части."""
    def __init__(self,client,max_prompt_chars): self.client=client; self.max_prompt_chars=max_prompt_chars
    def _split_dialog(self,dialog):
        chunks=[]; current=''
        for line in dialog.splitlines():
            pieces=[line[i:i+self.max_prompt_chars-1] for i in range(0,max(len(line),1),self.max_prompt_chars-1)] or ['']
            for piece in pieces:
                candidate=piece if not current else current+'\n'+piece
                if current and len(candidate)>self.max_prompt_chars:
                    chunks.append(current); current=piece
                else: current=candidate
        if current: chunks.append(current)
        return chunks
    async def _summarize_chunks(self,chunks):
        out=[]
        for chunk in chunks:
            out.append(await self.client.complete(system_prompt=SYSTEM_PROMPT,user_prompt=SUMMARY_PROMPT+'\n\nДИАЛОГ:\n'+chunk))
        return out
    async def summarize(self,dialog):
        chunks=self._split_dialog(dialog)
        if not chunks:return 'За выбранный период сообщений нет.'
        partial=await self._summarize_chunks(chunks)
        while len(partial)>1:
            combined='\n\n---\n\n'.join(partial)
            chunks=self._split_dialog(combined)
            partial=await self._summarize_chunks(chunks)
        return partial[0]
