"""Клиент YooKassa: создание платежа и повторная проверка статуса."""

import base64, httpx

class YooKassaClient:
    BASE_URL='https://api.yookassa.ru/v3'
    def __init__(self,shop_id,secret_key):
        self.shop_id=shop_id; self.secret_key=secret_key
        self._client=None

    def _http(self):
        # Reuse the connection pool instead of building a client (new TCP +
        # TLS handshake) for every payment call.
        if self._client is None or self._client.is_closed:
            self._client=httpx.AsyncClient(timeout=30)
        return self._client

    async def aclose(self):
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client=None

    def _check(self):
        if not self.shop_id or not self.secret_key: raise RuntimeError('YooKassa is not configured')
    def _auth(self): return base64.b64encode(f'{self.shop_id}:{self.secret_key}'.encode()).decode()
    async def create_payment(self,amount,description,order_id,return_url):
        self._check(); payload={'amount':{'value':f'{amount:.2f}','currency':'RUB'},'capture':True,'description':description,'metadata':{'order_id':order_id}}
        if return_url: payload['confirmation']={'type':'redirect','return_url':return_url}
        headers={'Authorization':f'Basic {self._auth()}','Content-Type':'application/json','Idempotence-Key':order_id}
        r=await self._http().post(f'{self.BASE_URL}/payments',headers=headers,json=payload); r.raise_for_status(); return r.json()
    async def get_payment(self,payment_id):
        self._check(); headers={'Authorization':f'Basic {self._auth()}'}
        r=await self._http().get(f'{self.BASE_URL}/payments/{payment_id}',headers=headers); r.raise_for_status(); return r.json()
