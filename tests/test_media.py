import base64
import concurrent.futures
import json
import time
from fastapi.testclient import TestClient
from receptionist.server import Settings,create_app
from receptionist.audio import MockASR,MockTTS,pcm_to_ulaw,ulaw_to_pcm,mulaw_from_pcm16,pcm16_from_mulaw

def make_app(tmp_path):
    return create_app(Settings(db_path=str(tmp_path/'test.sqlite'),dev_allow_unsigned=True,wss_url='wss://localhost/twilio/media'),asr_factory=lambda:MockASR(['book 2027-12-10 14:00','yes']),tts=MockTTS())

def test_health_simulator_twiML(tmp_path):
    c=TestClient(make_app(tmp_path))
    assert c.get('/healthz').status_code==200
    xml=c.post('/twilio/voice',data={'CallSid':'A'}).text
    assert '<Connect><Stream' in xml and 'wss://' in xml
    assert c.post('/simulate/text',json={'session_id':'test','text':'book 2027-12-10 14:00'}).json()['action']=='confirm_required'
    assert c.post('/simulate/text',json={'session_id':'test','text':'yes'}).json()['action']=='booked'

def inbound(payload, sid):return {'event':'media','streamSid':sid,'media':{'payload':base64.b64encode(payload).decode()}}

def test_twilio_duplex_media_and_mark(tmp_path):
    app=make_app(tmp_path)
    with TestClient(app) as c:
        with c.websocket_connect('/twilio/media') as ws:
            ws.send_json({'event':'connected'})
            ws.send_json({'event':'start','streamSid':'M1','start':{'callSid':'C1'}})
            # 5 x 20ms loud speech then 16x20ms silence to trigger endpointing
            for _ in range(5):ws.send_json(inbound(bytes([0]*160),'M1'))
            for _ in range(16):ws.send_json(inbound(bytes([255]*160),'M1'))
            clear=ws.receive_json()
            assert clear['event']=='clear' and clear['streamSid']=='M1'
            media=ws.receive_json()
            assert media['event']=='media' and media['streamSid']=='M1'
            assert base64.b64decode(media['media']['payload'])==bytes([255]*160)
            msg=ws.receive_json()
            assert msg['event']=='media'
            mark=ws.receive_json()
            assert mark['event']=='mark' and mark['streamSid']=='M1'
            ws.send_json({'event':'stop','streamSid':'M1'})

def test_codec_boundary():
    assert len(pcm16_from_mulaw(bytes([0,127,255])))==6
    assert len(mulaw_from_pcm16(b'\0\0'*160))==160
    assert all(0<=pcm_to_ulaw(v)<=255 for v in (-32000,-1000,0,1000,32000))
    assert abs(ulaw_to_pcm(pcm_to_ulaw(1000))-1000)<80

def test_two_calls_separate_and_same_slot_races(tmp_path):
    # Two real threads and two independent WebSockets, shared database.
    app=make_app(tmp_path)
    def call(i):
        client=TestClient(app)
        with client.websocket_connect('/twilio/media') as ws:
            sid=f'M{i}'
            ws.send_json({'event':'start','streamSid':sid,'start':{'callSid':f'C{i}'}})
            for _ in range(5):ws.send_json(inbound(bytes([0]*160),sid))
            for _ in range(16):ws.send_json(inbound(bytes([255]*160),sid))
            assert ws.receive_json()['streamSid']==sid # clear
            output=ws.receive_json()
            assert output['event']=='media' and output['streamSid']==sid
            assert ws.receive_json()['streamSid']==sid # second media
            assert ws.receive_json()['event']=='mark'
            ws.send_json({'event':'stop'})
        return True
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(call,range(2)))==[True,True]

def test_prod_webhook_requires_signature(tmp_path):
    app=create_app(Settings(db_path=str(tmp_path/'db.sqlite'),dev_allow_unsigned=False,auth_token='secret',public_url='https://example.com',wss_url='wss://example.com/twilio/media'))
    c=TestClient(app)
    assert c.post('/twilio/voice',data={'CallSid':'X'}).status_code==403
    assert c.post('/simulate/text',json={'session_id':'x','text':'yes'}).status_code==404
