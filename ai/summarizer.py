"""Подготовка длинного контекста и многошаговая суммаризация."""

import asyncio

from  ai.prompts import SYSTEM_PROMPT,SUMMARY_PROMPT

# Summary chunks are independent network calls. Sending a few of them in
# parallel cuts the wall-clock time of a long report substantially while
# staying well inside the YandexGPT rate limits.
MAX_PARALLEL_REQUESTS = 3


class Summarizer:
    """Не допускает переполнения prompt: длинный контекст разбивается на части."""
    def __init__(self,client,max_prompt_chars,max_parallel_requests=MAX_PARALLEL_REQUESTS):
        self.client=client
        self.max_prompt_chars=max_prompt_chars
        self.max_parallel_requests=max(1,int(max_parallel_requests))

    def _split_dialog(self,dialog):
        limit=self.max_prompt_chars
        chunks=[]
        current=[]
        current_len=0
        for line in dialog.splitlines():
            pieces=[line[i:i+limit-1] for i in range(0,max(len(line),1),limit-1)] or ['']
            for piece in pieces:
                # current_len mirrors the length of '\n'.join(current), so a
                # chunk that is still empty behaves exactly like '' before.
                if current_len:
                    added=len(piece)+1
                    if current_len+added>limit:
                        chunks.append('\n'.join(current))
                        current=[piece]
                        current_len=len(piece)
                    else:
                        current.append(piece)
                        current_len+=added
                else:
                    current=[piece]
                    current_len=len(piece)
        if current_len: chunks.append('\n'.join(current))
        return chunks

    async def _summarize_chunks(self,chunks):
        results=[None]*len(chunks)
        semaphore=asyncio.Semaphore(self.max_parallel_requests)

        async def run(index,chunk):
            async with semaphore:
                results[index]=await self.client.complete(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=SUMMARY_PROMPT+'\n\nДИАЛОГ:\n'+chunk,
                )

        tasks=[asyncio.create_task(run(i,chunk)) for i,chunk in enumerate(chunks)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            # Do not leave sibling requests running (and unfetched) after a
            # failure: the caller reports the error and releases the slot.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            raise
        return results

    async def summarize(self,dialog):
        chunks=self._split_dialog(dialog)
        if not chunks:return 'За выбранный период сообщений нет.'
        partial=await self._summarize_chunks(chunks)
        while len(partial)>1:
            combined='\n\n---\n\n'.join(partial)
            chunks=self._split_dialog(combined)
            partial=await self._summarize_chunks(chunks)
        return partial[0]
