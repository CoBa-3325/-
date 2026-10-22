"""Минимальный асинхронный клиент YandexGPT без вывода секретов в лог."""

from __future__ import annotations
import json
import logging
import aiohttp
logger=logging.getLogger(__name__)
class YandexGPT:
    URL='https://ai.api.cloud.yandex.net/foundationModels/v1/completion'
    def __init__(self,api_key,folder_id,model='yandexgpt'):
        self.api_key=api_key; self.folder_id=folder_id; self.model=model
        self._session=None

    def _http(self):
        # One long-lived session keeps the TLS connection alive between
        # requests; creating a session per call forced a new handshake every
        # time and leaked connectors when a request failed.
        if self._session is None or self._session.closed:
            self._session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120))
        return self._session

    async def close(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session=None

    async def complete(self,*,system_prompt,user_prompt,temperature=0.2,max_tokens=2000):
        model_uri=self.model.strip() if self.model.strip().startswith('gpt://') else f'gpt://{self.folder_id}/{self.model.strip()}/latest'
        payload={'modelUri':model_uri,'completionOptions':{'stream':False,'temperature':temperature,'maxTokens':max_tokens},'messages':[{'role':'system','text':system_prompt},{'role':'user','text':user_prompt}]}
        headers={'Authorization':f'Api-Key {self.api_key}','Content-Type':'application/json'}
        logger.info('Запрос к YandexGPT: model=%s prompt_chars=%s',model_uri,len(system_prompt)+len(user_prompt))
        async with self._http().post(self.URL,headers=headers,json=payload) as response:
            body=await response.text()
            if response.status>=400: raise RuntimeError(f'YandexGPT API error {response.status}')
        try:data=json.loads(body)
        except Exception as exc:raise RuntimeError('Некорректный ответ YandexGPT') from exc
        alts=data.get('result',{}).get('alternatives',[])
        result=(alts[0].get('message',{}).get('text','').strip() if alts else '')
        if not result: raise RuntimeError('YandexGPT вернул пустой ответ.')
        logger.info('YandexGPT успешно ответил: result_chars=%s',len(result)); return result
