"""Twilio Media Streams WebSocket endpoint with offline injectable speech adapters."""
from __future__ import annotations
from collections import OrderedDict
from datetime import datetime, timezone
from html import escape
from urllib.parse import parse_qsl
import asyncio
import logging
import time
import re
import threading
import base64
import binascii
import hashlib
import hmac
import json
import os
import threading
from pathlib import Path
from typing import Callable
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from receptionist import health
from receptionist.booking import BookingStore
from receptionist.dialogue import Dialogue
from receptionist.audio import MockASR, MockTTS, WhisperCppASR, PiperTTS, rms_mulaw

class TextInput(BaseModel):
    session_id: str
    text: str

class Settings:
    def __init__(self, db_path:str|None=None, dev_allow_unsigned:bool|None=None, wss_url:str|None=None, public_url:str|None=None, auth_token:str|None=None):
        self.db_path=db_path or os.getenv('RECEPTIONIST_DB','./data/receptionist.sqlite')
        self.dev_allow_unsigned=(os.getenv('DEV_ALLOW_UNSIGNED','0')=='1') if dev_allow_unsigned is None else dev_allow_unsigned
        self.wss_url=wss_url or os.getenv('PUBLIC_WSS_URL','wss://example.invalid/twilio/media')
        self.public_url=public_url or os.getenv('PUBLIC_BASE_URL','https://example.invalid')
        self.auth_token=auth_token if auth_token is not None else os.getenv('TWILIO_AUTH_TOKEN','')
        # Production sets REQUIRE_CALLER_ID=1: anonymous callers can't book/cancel/reschedule.
        self.require_caller_id=os.getenv('REQUIRE_CALLER_ID','0')=='1'

# A caller number we accept as an identity: optional +, 5-15 digits (E.164-ish).
# Anything else (blocked/"anonymous", injection attempts) is treated as no number.
CALLER_RE=re.compile(r'\+?[0-9]{5,15}')

log=logging.getLogger('receptionist.call')

def split_sentences(text:str)->list[str]:
    # Split only where punctuation is followed by whitespace, so "1.2.3" and times stay whole.
    return [p for p in re.split(r'(?<=[.!?])\s+',text.strip()) if p]

def caller_number(value:object)->str|None:
    return value if isinstance(value,str) and CALLER_RE.fullmatch(value) else None

def valid_signature(url:str, params:dict[str,str], actual:str|None, token:str) -> bool:
    if not token or not actual:return False
    message=url+''.join(k+params[k] for k in sorted(params))
    sig=base64.b64encode(hmac.new(token.encode(),message.encode(),hashlib.sha1).digest()).decode()
    return hmac.compare_digest(sig,actual)

MAX_SIM_SESSIONS=1000

def create_app(settings:Settings|None=None, asr_factory:Callable[[],object]|None=None, tts:object|None=None, max_sessions:int=MAX_SIM_SESSIONS) -> FastAPI:
    settings=settings or Settings()
    store=BookingStore(settings.db_path)
    if asr_factory is None:
        asr_factory=(lambda: WhisperCppASR(os.getenv('WHISPER_CLI','whisper-cli'),os.environ['WHISPER_MODEL'])) if os.getenv('VOICE_BACKEND')=='local' and os.getenv('WHISPER_MODEL') else (lambda: MockASR())
    if tts is None:
        tts=PiperTTS(os.getenv('PIPER_CLI','piper'),os.environ['PIPER_MODEL']) if os.getenv('VOICE_BACKEND')=='local' and os.getenv('PIPER_MODEL') else MockTTS()
    app=FastAPI(title='Pi Local Receptionist',version='0.1.0')
    app.state.store=store
    # Bounded LRU: an unbounded map of session_id -> Dialogue is a memory leak.
    sessions:OrderedDict[str,Dialogue]=OrderedDict()
    app.state.sessions=sessions
    sessions_lock=threading.Lock()  # sync endpoints run on a thread pool

    @app.get('/healthz')
    def healthz():return {'status':'ok','voice_backend':os.getenv('VOICE_BACKEND','mock')}

    @app.get('/readyz')
    def readyz():
        # Can this Pi take calls right now? (DB lock, disk, power, temperature)
        result=health.readiness(settings.db_path,os.path.dirname(os.path.abspath(settings.db_path)))
        return JSONResponse(result,status_code=200 if result['ready'] else 503)

    @app.post('/simulate/text')
    def simulate(x:TextInput):
        # Dev endpoint is intentionally disabled except during an explicit local test run.
        if not settings.dev_allow_unsigned:raise HTTPException(404)
        with sessions_lock:
            dialogue=sessions.get(x.session_id)
            if dialogue is None:
                dialogue=sessions[x.session_id]=Dialogue(store)
                while len(sessions)>max_sessions:sessions.popitem(last=False)
            else:sessions.move_to_end(x.session_id)
        result=dialogue.handle(x.text,x.session_id)
        return {'text':result.text,'action':result.action,'committed_id':result.committed_id}

    @app.post('/twilio/voice')
    async def voice(request:Request):
        raw=(await request.body()).decode()
        params=dict(parse_qsl(raw,keep_blank_values=True))
        if not settings.dev_allow_unsigned and not valid_signature(settings.public_url.rstrip('/')+'/twilio/voice',params,request.headers.get('X-Twilio-Signature'),settings.auth_token):
            raise HTTPException(403,'Invalid Twilio signature')
        caller=caller_number(params.get('From'))
        param=f'<Parameter name="callerId" value="{escape(caller,quote=True)}" />' if caller else ''
        twiml=f'<Response><Connect><Stream url="{escape(settings.wss_url,quote=True)}">{param}</Stream></Connect></Response>'
        return Response(twiml,media_type='application/xml')

    @app.websocket('/twilio/media')
    async def media(ws:WebSocket):
        if not settings.dev_allow_unsigned and not valid_signature(settings.wss_url,{},ws.headers.get('X-Twilio-Signature'),settings.auth_token):
            await ws.close(code=1008);return
        await ws.accept()
        stream_id=None;call_id=None
        dialogue=None
        asr=asr_factory()
        generation=0
        audio=bytearray();heard_speech=False;silence_ms=0;spoken_ms=0
        send_lock=asyncio.Lock()
        # Serialises this call's dialogue across worker threads: a barge-in cancels the
        # awaiting task, but its thread keeps running until handle() returns.
        dialogue_lock=threading.Lock()
        caller_id:str|None=None
        def handle_locked(text:str,session:str):
            with dialogue_lock:return dialogue.handle(text,session,caller_id=caller_id)
        jobs:set[asyncio.Task]=set()
        async def send(event:dict):
            async with send_lock:await ws.send_json(event)
        async def process(packet:bytes, this_generation:int):
            try:
                t0=time.monotonic()
                utterance=await asr.transcribe(packet)
                t_asr=time.monotonic()
                log.info('call=%s gen=%d phase=asr audio_s=%.2f took_s=%.2f stale=%s',call_id,this_generation,len(packet)/8000,t_asr-t0,this_generation!=generation)
                if this_generation!=generation or not utterance or not dialogue:return
                # Booking does blocking SQLite I/O (busy waits up to 15 s); keep it off the
                # event loop so other calls keep streaming.
                reply=await asyncio.to_thread(handle_locked,utterance,call_id or 'invalid')
                # Synthesise sentence k+1 while sentence k streams: the caller hears the first
                # sentence without waiting for the whole reply (PI4: 8 s reply rendered up front).
                words=reply.speech or reply.text   # phone wording when the dialogue provides it (#38)
                parts=split_sentences(words) or [words]
                pending=asyncio.create_task(tts.synthesize(parts[0]))
                try:
                    for k in range(len(parts)):
                        outbound=await pending
                        if k==0:log.info('call=%s gen=%d phase=first_audio_ready since_asr_done_s=%.2f',call_id,this_generation,time.monotonic()-t_asr)
                        pending=asyncio.create_task(tts.synthesize(parts[k+1])) if k+1<len(parts) else None
                        if this_generation!=generation:return
                        for i in range(0,len(outbound),160):
                            if generation!=this_generation:return
                            payload=base64.b64encode(outbound[i:i+160]).decode()
                            await send({'event':'media','streamSid':stream_id,'media':{'payload':payload}})
                            await asyncio.sleep(0.02)
                finally:
                    if pending and not pending.done():pending.cancel()
                if this_generation==generation:await send({'event':'mark','streamSid':stream_id,'mark':{'name':f'answer-{this_generation}'}})
            except asyncio.CancelledError:raise
            except Exception:
                if this_generation==generation:
                    # Avoid lying about booking success if ASR/TTS fails.
                    await send({'event':'mark','streamSid':stream_id,'mark':{'name':'processing-error'}})
        try:
            while True:
                # Untrusted input: anything that isn't a JSON object is a policy close, never a crash.
                try:evt=await ws.receive_json()
                except ValueError:await ws.close(code=1008);break
                if not isinstance(evt,dict):await ws.close(code=1008);break
                event=evt.get('event')
                if event=='connected':continue
                if event=='start':
                    start=evt.get('start')
                    if (stream_id or not isinstance(start,dict) or not isinstance(evt.get('streamSid'),str) or not evt['streamSid']
                            or not isinstance(start.get('callSid'),str) or not start['callSid']):
                        await ws.close(code=1008);break
                    stream_id=evt['streamSid'];call_id=start['callSid'];dialogue=Dialogue(store,require_caller_id=settings.require_caller_id)
                    custom=start.get('customParameters')
                    caller_id=caller_number(custom.get('callerId')) if isinstance(custom,dict) else None
                elif event=='media':
                    if not stream_id or evt.get('streamSid')!=stream_id:await ws.close(code=1008);break
                    body=evt.get('media')
                    if not isinstance(body,dict):await ws.close(code=1008);break
                    payload=body.get('payload','')
                    if not isinstance(payload,str) or len(payload)>20000:await ws.close(code=1009);break
                    try:data=base64.b64decode(payload,validate=True)
                    except (binascii.Error,ValueError):await ws.close(code=1008);break
                    if not data:continue
                    # Simple VAD. Runs on speech+silence, leaving heavier ASR on a background task.
                    rms=rms_mulaw(data)
                    ms=1000*len(data)/8000
                    if rms>650:
                        if not heard_speech:
                            generation+=1
                            for task in list(jobs):task.cancel()
                            if stream_id:await send({'event':'clear','streamSid':stream_id})
                        heard_speech=True;silence_ms=0;spoken_ms+=ms
                        if len(audio)>160000:await ws.close(code=1009);break
                        audio.extend(data)
                    elif heard_speech:
                        silence_ms+=ms
                        if silence_ms>=300 and spoken_ms>=80:
                            snapshot=bytes(audio);ticket=generation
                            audio.clear();heard_speech=False;silence_ms=0;spoken_ms=0
                            task=asyncio.create_task(process(snapshot,ticket))
                            jobs.add(task);task.add_done_callback(jobs.discard)
                elif event=='mark':continue
                elif event=='stop':break
                else:await ws.close(code=1008);break
        except (WebSocketDisconnect,ValueError,RuntimeError):pass
        finally:
            for task in list(jobs):task.cancel()
            if jobs:await asyncio.gather(*jobs,return_exceptions=True)
    return app

app=create_app()
