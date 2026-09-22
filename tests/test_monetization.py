from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import pytest
import asyncio
from app.database.connection import connect
from app.database.repository import Repository
from app.services.token_service import TokenService
from app.services.subscription_service import SubscriptionService
from app.services.role_service import RoleService
from app.services.promotion_service import PromotionService
from app.services.payment_service import PaymentService

class Settings:
    token_price_10=300
    subscription_price_1_month=99
    subscription_price_3_months=259
    subscription_price_6_months=559
    yookassa_shop_id='shop'; yookassa_secret_key='secret'; yookassa_return_url='https://example.test/return'

def test_trial_is_user_wide_once():
    r=Repository(connect(':memory:'));t=TokenService(r,30)
    t.ensure_trial(1);t.ensure_trial(1);assert t.balance(1)==30
    assert 'chat_id' not in {x['name'] for x in r.connection.execute('pragma table_info(token_grants)').fetchall()}

def test_token_consumption_uses_preferred_grants():
    r=Repository(connect(':memory:'));t=TokenService(r,30);t.ensure_trial(1)
    r.add_token_grant(1,5,'purchased',None,'test-purchase');r.add_token_grant(1,2,'promo',None,'test-promo')
    # subscription is highest priority
    r.add_token_grant(1,1,'subscription',(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),'test-sub')
    op=t.deduct_for_llm(1);assert op
    row=r.connection.execute("select token_type,remaining_amount from token_grants where source_id='test-sub'").fetchone();assert row['remaining_amount']==0

def test_refund_preserves_original_grant():
    r=Repository(connect(':memory:'));t=TokenService(r,30);t.ensure_trial(1);op=t.deduct_for_llm(1);assert t.balance(1)==29;assert t.refund(1,1,op);assert not t.refund(1,1,op);assert t.balance(1)==30

def test_roles_and_permissions():
    r=Repository(connect(':memory:'));roles=RoleService(r)
    for uid in (1,2,3):r.upsert_user(uid,str(uid),'')
    r.set_role_if_current(2,'user','admin');r.set_role_if_current(3,'user','creator')
    assert roles.can_create_role_promo(2,'admin');assert not roles.can_create_role_promo(2,'creator');assert roles.can_create_role_promo(3,'creator')
    assert not roles.can_manage(2,2);assert roles.can_manage(3,2);assert not roles.can_manage(3,3)

def test_creator_can_demote_admin_atomically():
    r=Repository(connect(':memory:'));roles=RoleService(r)
    r.upsert_user(1,'A','');r.upsert_user(2,'B','');r.set_role_if_current(1,'user','creator');r.set_role_if_current(2,'user','admin')
    assert roles.set_role(1,2,'user');assert r.get_role(2)=='user'
    with pytest.raises(PermissionError): roles.set_role(1,2,'user')

def test_promo_roles_and_usage_limit():
    r=Repository(connect(':memory:'));roles=RoleService(r);p=PromotionService(r,Settings(),roles)
    for uid in (1,2,3):r.upsert_user(uid,str(uid),'')
    r.set_role_if_current(2,'user','admin');r.set_role_if_current(3,'user','creator')
    try:p.create(2,code='CRE',usage_type='once',reward_type='role',admin_role='creator');assert False
    except PermissionError:pass
    p.create(2,code='ADM',usage_type='once',reward_type='role',admin_role='admin');assert p.redeem(1,' adm ');assert r.get_role(1)=='admin';assert p.redeem(1,'ADM') is None
    p.create(3,code='TOK',usage_type='limited',max_uses=2,reward_type='tokens',token_amount=7)
    assert p.redeem(2,'TOK');assert p.redeem(3,'TOK');assert p.redeem(1,'TOK') is None

def test_initial_admin_promo_is_created_once():
    r=Repository(connect(':memory:'));a=r.ensure_initial_admin_promo();b=r.ensure_initial_admin_promo();assert a and b is None;assert r.get_promo(a['code'])['used_count']==0

def test_subscription_prices_and_expiration():
    r=Repository(connect(':memory:'));roles=RoleService(r);s=SubscriptionService(r,roles);now=datetime(2026,9,1,tzinfo=timezone.utc)
    end=s.extend(1,3,now);assert end==datetime(2026,11,30,tzinfo=timezone.utc);assert r.connection.execute("select amount from token_grants where token_type='subscription'").fetchone()['amount']==90

class FakeYK:
    def __init__(self):self.order_id=None
    async def create_payment(self,*a,**k):return {'id':'pay-1','confirmation':{'confirmation_url':'https://pay.test'}}
    async def get_payment(self,pid):return {'id':pid,'status':'succeeded','amount':{'value':'300.00','currency':'RUB'},'metadata':{'order_id':self.order_id}}

def test_payment_webhook_is_idempotent():
    r=Repository(connect(':memory:'));roles=RoleService(r);t=TokenService(r,30);s=SubscriptionService(r,roles);p=PromotionService(r,Settings(),roles);ps=PaymentService(r,Settings(),p,s,t);yk=FakeYK();ps.yookassa=yk;r.upsert_user(1,'A','')
    async def run():
        order,_=await ps.create_order(1,'tokens','10');yk.order_id=order['order_id'];assert await ps.handle_webhook_payment('pay-1');assert await ps.handle_webhook_payment('pay-1')
    asyncio.run(run());assert t.balance(1)==40
