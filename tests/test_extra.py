from datetime import datetime, timezone, timedelta
from app.database.connection import connect
from app.database.repository import Repository
from app.schemas.message import MessageRecord
from app.services.cooldown_service import CooldownService
from app.services.period_service import period_for_interval

def test_command_removed_is_not_in_code():
    import app.bot.commands as commands
    assert '/settings' not in commands.HELP_TEXT

def test_cooldown_is_per_chat_and_persistent():
    r=Repository(connect(':memory:'));c=CooldownService(r,10)
    assert c.acquire(1);c.activate(1);assert c.remaining_seconds(1)>0;assert c.remaining_seconds(2)==0

def test_cooldown_concurrent_slot_guard():
    r=Repository(connect(':memory:'));c=CooldownService(r,10)
    assert c.acquire(1);assert not c.acquire(1);c.release(1);assert c.acquire(1)

def test_message_deduplication():
    r=Repository(connect(':memory:'));m=MessageRecord('m',1,2,'u','hi','text',datetime.now(timezone.utc));assert r.save_message(m);assert not r.save_message(m)

def test_poll_context_is_persisted():
    r=Repository(connect(':memory:'));r.record_poll('p',1,'Q',{'options':[{'text':'A','votes':2}]},2);assert r.connection.execute('select poll_id from poll_context').fetchone()['poll_id']=='p'

def test_periods():
    now=datetime(2026,9,18,tzinfo=timezone.utc);assert period_for_interval('daily',now).end-period_for_interval('daily',now).start==timedelta(days=1);assert period_for_interval('custom',now,30).end-period_for_interval('custom',now,30).start==timedelta(days=30)
