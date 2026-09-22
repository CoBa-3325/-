"""Клиент YooKassa: создание платежа и повторная проверка статуса."""

import base64, httpx

class YooKassaClient:
    BASE_URL='https://api.yookassa.ru/v3'
    def __init__(self,shop_id,secret_key): self.shop_id=shop_id; self.secret_key=secret_key
    def _check(self):
        if not self.shop_id or not self.secret_key: raise RuntimeError('YooKassa is not configured')
    def _auth(self): return base64.b64encode(f'{self.shop_id}:{self.secret_key}'.encode()).decode()
    async def create_payment(self,amount,description,order_id,return_url):
        self._check(); payload={'amount':{'value':f'{amount:.2f}','currency':'RUB'},'capture':True,'description':description,'metadata':{'order_id':order_id}}
        if return_url: payload['confirmation']={'type':'redirect','return_url':return_url}
        headers={'Authorization':f'Basic {self._auth()}','Content-Type':'application/json','Idempotence-Key':order_id}
        async with httpx.AsyncClient(timeout=30) as c:
            r=await c.post(f'{self.BASE_URL}/payments',headers=headers,json=payload); r.raise_for_status(); return r.json()
    async def get_payment(self,payment_id):
        self._check(); headers={'Authorization':f'Basic {self._auth()}'}
        async with httpx.AsyncClient(timeout=30) as c:
            r=await c.get(f'{self.BASE_URL}/payments/{payment_id}',headers=headers); r.raise_for_status(); return r.json()
