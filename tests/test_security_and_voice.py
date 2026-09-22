import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest
from app.database.connection import connect
from app.database.repository import Repository
from app.schemas.message import MessageRecord
from app.ai.summarizer import Summarizer
from app.services.voice_service import VoiceService

class DummyGPT:
    def __init__(self): self.calls=[]
    async def complete(self,**kw): self.calls.append(kw); return 'ok'

def test_duplicate_message_is_idempotent():
    r=Repository(connect(':memory:')); m=MessageRecord('x',1,2,'u','hi','text',datetime.now(timezone.utc)); assert r.save_message(m); assert not r.save_message(m)

def test_poll_source_allowed():
    r=Repository(connect(':memory:')); m=MessageRecord('p',1,2,'u','Q: A 1 vote','poll',datetime.now(timezone.utc)); assert r.save_message(m)

def test_poll_data_persisted():
    r=Repository(connect(':memory:')); r.record_poll('p',1,'Q',{'options':[{'text':'A','votes':2}]},2); row=r.connection.execute('select * from poll_context').fetchone(); assert row['poll_id']=='p'

def test_message_time_bounds():
    r=Repository(connect(':memory:')); t=datetime(2026,1,1,tzinfo=timezone.utc); r.save_message(MessageRecord('a',1,2,'u','a','text',t)); r.save_message(MessageRecord('b',1,2,'u','b','text',t.replace(day=2))); rows=r.get_messages(1,t,t.replace(day=2)); assert [x['max_message_id'] for x in rows]==['a']

@pytest.mark.asyncio
async def test_summarizer_splits_long_single_line():
    g=DummyGPT(); s=Summarizer(g,100); out=await s.summarize('x'*1000); assert out=='ok'; assert len(g.calls)>1; assert all(len(c['user_prompt'])<500 for c in g.calls)

@pytest.mark.asyncio
async def test_voice_uses_url_bytes(monkeypatch):
    got={}
    class T:
        async def transcribe(self,b): got['b']=b; return 'распознано'
    class Resp:
        status=200
        async def __aenter__(self): return self
        async def __aexit__(self,*a): pass
        async def read(self): return b'audio-bytes'
        def raise_for_status(self): pass
    class Sess:
        def __init__(self,*a,**k): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*a): pass
        def get(self,*a,**k): return Resp()
    monkeypatch.setattr('app.services.voice_service.aiohttp.ClientSession',Sess)
    v=VoiceService(T(),100,'token'); m=type('M',(),{})(); m.body=type('B',(),{})(); m.body.attachments=[{'type':'audio','payload':{'url':'https://file.test/a'}}]
    assert await v.process_voice_message(m)=='распознано'; assert got['b']==b'audio-bytes'

@pytest.mark.asyncio
async def test_voice_rejects_missing_attachment():
    class T:
        async def transcribe(self,b): return 'x'
    v=VoiceService(T(),100,'token'); m=type('M',(),{})(); m.body=type('B',(),{})(); m.body.attachments=[]
    with pytest.raises(ValueError): await v.process_voice_message(m)

@pytest.mark.asyncio
async def test_voice_rejects_large_file(monkeypatch):
    class T:
        async def transcribe(self,b): return 'x'
    class Resp:
        status=200
        async def __aenter__(self): return self
        async def __aexit__(self,*a): pass
        async def read(self): return b'x'*101
        def raise_for_status(self): pass
    class Sess:
        def __init__(self,*a,**k): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*a): pass
        def get(self,*a,**k): return Resp()
    monkeypatch.setattr('app.services.voice_service.aiohttp.ClientSession',Sess)
    v=VoiceService(T(),100,'token'); m=type('M',(),{})(); m.body=type('B',(),{})(); m.body.attachments=[{'type':'audio','payload':{'url':'https://file.test/a'}}]
    with pytest.raises(ValueError): await v.process_voice_message(m)
