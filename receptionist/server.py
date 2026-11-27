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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from receptionist import health
from receptionist.booking import BookingStore
from receptionist.dialogue import Dialogue, OPENERS
from receptionist.telephony import CALLER_RE, MAX_UTTERANCE_BYTES, CallEngine, caller_number, review_key, split_sentences  # noqa: F401
from receptionist.twilio_transport import TwilioMediaStreamTransport
from receptionist.asterisk_transport import AsteriskWebSocketTransport
from receptionist.ari import ARIClient
from receptionist.audio import MockASR, MockTTS, WhisperCppASR, WhisperDaemonASR, PiperTTS, PiperDaemonTTS, CachingTTS, rms_mulaw

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

log=logging.getLogger('receptionist.call')

def _enable_call_timing_logs():
    # uvicorn configures only its own loggers; give the per-phase latency lines a
    # handler (stderr -> journal) once, without duplicating a caller's own setup.
    if not log.handlers:
        h=logging.StreamHandler()
        h.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
        log.addHandler(h)
    log.setLevel(logging.INFO);log.propagate=False

def valid_signature(url:str, params:dict[str,str], actual:str|None, token:str) -> bool:
    if not token or not actual:return False
    message=url+''.join(k+params[k] for k in sorted(params))
    sig=base64.b64encode(hmac.new(token.encode(),message.encode(),hashlib.sha1).digest()).decode()
    return hmac.compare_digest(sig,actual)

MAX_SIM_SESSIONS=1000
CHECKPOINT_EVERY_S=300   # background WAL checkpoint for the bookings DB

def select_asr_factory(env) -> tuple[Callable[[],object],str]:
    """ASR per environment: one shared resident whisper (WHISPER_DAEMON, ~0.7 s per turn on
    the Pi, #28), else a fresh whisper-cli per call (~7 s), else the mock."""
    if env.get('VOICE_BACKEND')!='local' or not env.get('WHISPER_MODEL'):
        return (lambda: MockASR()),'mock'
    if env.get('WHISPER_DAEMON'):
        shared=WhisperDaemonASR([env['WHISPER_DAEMON'],env['WHISPER_MODEL'],env.get('WHISPER_THREADS','4')])
        shared.prompt_short=bool(env.get('WD_PROMPT_EN') or env.get('WD_PROMPT_ES'))
        return (lambda: shared),'daemon'
    return (lambda: WhisperCppASR(env.get('WHISPER_CLI','whisper-cli'),env['WHISPER_MODEL'])),'cli'

PARADEE_VOICES=('af_heart','am_puck','bf_isabella','bm_fable')
def select_vad(env):
    """VAD_ENGINE=silero: a per-call Silero stream factory (model loads once); else None (loudness only)."""
    if env.get('VAD_ENGINE')!='silero':return None
    if not env.get('SILERO_VAD_MODEL'):raise ValueError('SILERO_VAD_MODEL required for VAD_ENGINE=silero')
    from receptionist import vad as vad_mod
    model=vad_mod.SileroVAD(env['SILERO_VAD_MODEL'])
    return model.stream


def select_tts(env) -> tuple[object,str]:
    """TTS per environment: resident Piper behind a phrase cache prewarmed with the
    dialogue's openers (PIPER_RESIDENT=1), else per-call Piper, else the mock."""
    if env.get('VOICE_BACKEND')=='local' and env.get('TTS_ENGINE')=='paradee':
        # Paradee: 8 MB voice distilled from Kokoro; ~2x faster than Piper on the Pi 4 and far less robotic.
        voice=env.get('PARADEE_VOICE','af_heart')
        if voice not in PARADEE_VOICES:raise ValueError('PARADEE_VOICE not allowed')
        threads=int(env.get('PARADEE_THREADS','2'))
        if not 1<=threads<=4:raise ValueError('PARADEE_THREADS out of range')
        speed=float(env.get('PARADEE_SPEED','1.0'))
        if not 0.5<=speed<=2.0:raise ValueError('PARADEE_SPEED out of range')
        script=str(Path(__file__).resolve().parents[1]/'scripts'/'paradee_daemon.py')
        args=[env['PARADEE_PYTHON'],script,'--voice',voice,'--threads',str(threads),'--speed',str(speed)]
        tts=CachingTTS(PiperDaemonTTS(args,env['PARADEE_MODEL']))
        tts.prewarm_phrases=OPENERS
        return tts,'paradee'
    if env.get('VOICE_BACKEND')!='local' or not env.get('PIPER_MODEL'):return MockTTS(),'mock'
    if env.get('PIPER_RESIDENT')=='1':
        # Expressiveness ('change pitch like a human'): intonation variation, rhythm variation, pace.
        args=[env.get('PIPER_CLI','piper')]
        for key,flag in (('PIPER_NOISE_SCALE','--noise_scale'),('PIPER_NOISE_W','--noise_w'),('PIPER_LENGTH_SCALE','--length_scale')):
            if env.get(key):
                value=float(env[key])               # ValueError on anything that isn't a number
                if not 0.1<=value<=2.0:raise ValueError(f'{key} out of range')
                args+=[flag,str(value)]
        tts=CachingTTS(PiperDaemonTTS(args,env['PIPER_MODEL']))
        tts.prewarm_phrases=OPENERS
        return tts,'resident'
    return PiperTTS(env.get('PIPER_CLI','piper'),env['PIPER_MODEL']),'cli'

def select_tts_es(env):
    """Spanish voice (PIPER_MODEL_ES), resident like the English one; None leaves calls English-only."""
    if env.get('VOICE_BACKEND')!='local' or not env.get('PIPER_MODEL_ES'):return None
    tts=CachingTTS(PiperDaemonTTS([env.get('PIPER_CLI','piper')],env['PIPER_MODEL_ES']))
    return tts

def load_business_profile(env) -> tuple[dict|None,str]:
    """(profile, greeting): the owner's facts file, or (None, generic greeting) if absent."""
    from receptionist.profile import greeting_for, load_profile
    path=env.get('BUSINESS_PROFILE',str(Path(__file__).resolve().parents[1]/'deploy'/'business_profile.json'))
    try:prof=load_profile(path)
    except (OSError,ValueError):prof=None
    return prof,greeting_for(prof)

def answer_phrases(env,profile) -> list[str]:
    """Every sentence the brain can answer with (business facts, checked answers, fixed lines), so
    the cache holds them before the first call: an uncached first sentence took 1-2.5 s on the Pi."""
    texts=[t for t in ((profile or {}).get('faq') or {}).values() if t]
    path=env.get('KNOWLEDGE',str(Path(__file__).resolve().parents[1]/'deploy'/'knowledge.json'))
    try:
        with open(path,encoding='utf-8') as f:texts+=[e['a'] for e in json.load(f)['entries']]
    except (OSError,ValueError,KeyError):pass
    try:
        from receptionist import brain
        texts+=[brain.REAL_PERSON,brain.UNKNOWN_SPEECH,*brain.REPHRASE.values(),*brain.FILLER_REPLIES,brain.PRESENCE_REPLY,brain.PITCH,brain.LOST_SPEECH]
    except Exception:pass
    out=[]
    for t in texts:
        for sentence in split_sentences(t):
            if sentence not in out:out.append(sentence)
    return out

def load_knowledge(env,intents):
    """Checked answers (KNOWLEDGE, default deploy/knowledge.json), embedded once at startup with the
    brain's encoder. None when the model can't embed or the file is missing: the brain still works."""
    if not hasattr(intents,'embed'):return None
    from receptionist.knowledge import Knowledge
    path=env.get('KNOWLEDGE',str(Path(__file__).resolve().parents[1]/'deploy'/'knowledge.json'))
    try:
        threshold=float(env.get('KNOWLEDGE_THRESHOLD','0.45'))
        return Knowledge.from_file(path,intents.embed,threshold=threshold)
    except (OSError,ValueError,KeyError):
        log.warning('knowledge unavailable',exc_info=True)
        return None

def select_dialogue_factory(env, store, require_caller_id: bool, load_intents=None, profile=None) -> tuple[Callable[[],object],str]:
    """The tiny-model Brain when BRAIN_DIR holds a loadable bundle; otherwise the rule Dialogue."""
    if env.get('BRAIN_DIR'):
        try:
            if load_intents is None:
                from receptionist.intent_model import load as load_intents
            intents=load_intents(env['BRAIN_DIR'])
            from receptionist.brain import Brain
            knowledge=load_knowledge(env,intents)
            from receptionist.notify import webhook_notifier
            notify=webhook_notifier(env)
            return (lambda: Brain(store,intents=intents,require_caller_id=require_caller_id,profile=profile,
                                  knowledge=knowledge,on_message=notify)),'brain'
        except Exception:
            log.warning('brain unavailable, using rule dialogue',exc_info=True)
    return (lambda: Dialogue(store,require_caller_id=require_caller_id)),'rules'

def create_app(settings:Settings|None=None, asr_factory:Callable[[],object]|None=None, tts:object|None=None, max_sessions:int=MAX_SIM_SESSIONS) -> FastAPI:
    settings=settings or Settings()
    _enable_call_timing_logs()
    store=BookingStore(settings.db_path)
    profile,greeting=load_business_profile(os.environ)
    dialogue_factory,brain_kind=select_dialogue_factory(os.environ,store,settings.require_caller_id,profile=profile)
    vad_factory=select_vad(os.environ)
    asr_kind='custom'
    if asr_factory is None:
        asr_factory,asr_kind=select_asr_factory(os.environ)
    tts_kind='custom'
    tts_es=select_tts_es(os.environ)
    if tts is None:
        tts,tts_kind=select_tts(os.environ)
    @asynccontextmanager
    async def lifespan(_app):
        # Openers are synthesised before the first call, so the first reply is instant too.
        warm=None
        if getattr(tts,'prewarm_phrases',None):
            async def prewarm():
                try:await tts.prewarm(list(tts.prewarm_phrases)+split_sentences(greeting)+answer_phrases(os.environ,profile))
                except Exception:log.warning('tts prewarm failed',exc_info=True)
            warm=asyncio.create_task(prewarm())
        async def checkpoints():
            # Fold the bookings WAL into the main file off the call path (see BookingStore).
            while True:
                await asyncio.sleep(CHECKPOINT_EVERY_S)
                try:await asyncio.to_thread(store.checkpoint)
                except Exception:log.warning('bookings checkpoint failed',exc_info=True)
        ckpt=asyncio.create_task(checkpoints())
        yield
        ckpt.cancel()
        await asyncio.to_thread(store.close)
        if warm:warm.cancel()
        for closer in (getattr(tts,'aclose',None),getattr(tts_es,'aclose',None)):
            if closer:await closer()
    app=FastAPI(title='Pi Local Receptionist',version='0.1.0',lifespan=lifespan)
    app.state.store=store
    app.state.vad_kind='silero' if vad_factory else 'loudness'
    # Bounded LRU: an unbounded map of session_id -> Dialogue is a memory leak.
    sessions:OrderedDict[str,Dialogue]=OrderedDict()
    app.state.sessions=sessions
    sessions_lock=threading.Lock()  # sync endpoints run on a thread pool

    @app.get('/healthz')
    def healthz():return {'status':'ok','voice_backend':os.getenv('VOICE_BACKEND','mock'),'asr':asr_kind,'tts':tts_kind,'brain':brain_kind,'vad':app.state.vad_kind}

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

    @app.websocket('/asterisk/media')
    async def asterisk_media(ws:WebSocket):
        # Asterisk runs on this host and the service binds 127.0.0.1 only, so the media
        # socket is reachable solely from local processes (Asterisk, SIPp tests).
        # chan_websocket asks for the "media" sub-protocol; echo it or the handshake fails.
        await ws.accept(subprotocol='media' if 'media' in ws.scope.get('subprotocols',[]) else None)
        engine=CallEngine(store,asr=asr_factory(),tts=tts,require_caller_id=settings.require_caller_id,greet=True,
                          dialogue_factory=dialogue_factory,greeting=greeting,vad_factory=vad_factory,tts_es=tts_es,
                          max_call_s=float(os.getenv('MAX_CALL_S','900')) or None,
                          idle_hangup_s=float(os.getenv('IDLE_HANGUP_S','30')) or None,
                          transfer_to=caller_number(os.getenv('TRANSFER_TO')),
                          review_dir=os.getenv('REVIEW_DIR','/var/lib/pi-receptionist/data/review'),
                          review_callers={k for k in (review_key(x) for x in os.getenv('REVIEW_CALLERS','').split(',')) if k})
        ari=ARIClient(os.environ['ARI_PASSWORD'],user=os.getenv('ARI_USERNAME','receptionist')) if os.getenv('ARI_PASSWORD') else None
        try:await engine.run(AsteriskWebSocketTransport(ws,ari=ari))
        except (WebSocketDisconnect,ValueError,RuntimeError):pass

    @app.websocket('/twilio/media')
    async def media(ws:WebSocket):
        if not settings.dev_allow_unsigned and not valid_signature(settings.wss_url,{},ws.headers.get('X-Twilio-Signature'),settings.auth_token):
            await ws.close(code=1008);return
        await ws.accept()
        # One engine per call keeps per-call ASR (whisper-cli) instances independent; the
        # resident daemon factory hands every call the same shared process.
        engine=CallEngine(store,asr=asr_factory(),tts=tts,require_caller_id=settings.require_caller_id)
        try:await engine.run(TwilioMediaStreamTransport(ws))
        except (WebSocketDisconnect,ValueError,RuntimeError):pass
    return app

app=create_app()
