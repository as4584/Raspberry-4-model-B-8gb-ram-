"""Local audio codecs and replaceable ASR/TTS adapters (no cloud inference)."""
from __future__ import annotations
from pathlib import Path
import asyncio
import math
import struct
import subprocess
import tempfile
import wave

# G.711 μ-law, 8 kHz. 160 bytes represents 20ms.
_BIAS=0x84
_CLIP=32635

def pcm_to_ulaw(sample: int) -> int:
    sample=max(-32768,min(32767,int(sample)))
    sign=(sample >> 8) & 0x80
    if sign: sample=-sample
    sample=min(_CLIP,sample)+_BIAS
    exponent=7
    mask=0x4000
    while exponent>0 and not (sample & mask):
        exponent-=1;mask>>=1
    mantissa=(sample >> (exponent+3)) & 0x0f
    return (~(sign | (exponent <<4) | mantissa)) & 0xff

def ulaw_to_pcm(value: int) -> int:
    v=(~value) & 0xff
    sign=v & 0x80
    exponent=(v>>4)&0x07
    mantissa=v & 0x0f
    sample=((mantissa<<3)+_BIAS)<<exponent
    sample-=_BIAS
    return -sample if sign else sample

def pcm16_from_mulaw(data: bytes) -> bytes:
    return b''.join(struct.pack('<h',ulaw_to_pcm(b)) for b in data)

def mulaw_from_pcm16(data: bytes) -> bytes:
    return bytes(pcm_to_ulaw(v) for (v,) in struct.iter_unpack('<h',data[:len(data)//2*2]))

def resample_pcm16(data: bytes, from_hz: int, to_hz: int) -> bytes:
    if from_hz<=0 or to_hz<=0: raise ValueError('positive sample rates required')
    raw=[v for (v,) in struct.iter_unpack('<h',data[:len(data)//2*2])]
    if not raw:return b''
    if from_hz==to_hz:return data
    out=[]
    for j in range(round(len(raw)*to_hz/from_hz)):
        x=j*from_hz/to_hz;left=min(int(x),len(raw)-1);right=min(left+1,len(raw)-1)
        v=round(raw[left]+(raw[right]-raw[left])*(x-left))
        out.append(max(-32768,min(32767,v)))
    return struct.pack('<'+'h'*len(out),*out)

def rms_mulaw(data: bytes) -> float:
    if not data:return 0.0
    return math.sqrt(sum(ulaw_to_pcm(v)**2 for v in data)/len(data))

class MockASR:
    def __init__(self, transcripts: list[str]|None=None): self.transcripts=list(transcripts or [])
    async def transcribe(self, mulaw: bytes) -> str:
        return self.transcripts.pop(0) if self.transcripts else ''

# Below this μ-law RMS (PCM16 units) output counts as silence, never as synthesized speech.
AUDIBLE_RMS=300.0

def clean_whisper_output(stdout: str) -> str:
    """Join whisper-cli stdout lines, dropping [BLANK_AUDIO]-style markers."""
    words=[]
    for line in stdout.splitlines():
        line=line.replace('[BLANK_AUDIO]','').strip()
        if line:words.append(line)
    return ' '.join(words)

class WhisperCppASR:
    """whisper.cpp `whisper-cli` (pinned v1.8.2): -m model -f wav -t N -l en -nt -np."""
    def __init__(self, executable: str, model: str, timeout: int=40, threads: int=4):
        self.executable,self.model,self.timeout,self.threads=executable,model,timeout,threads
    def args(self, wav: str) -> list[str]:
        return [self.executable,'-m',self.model,'-f',wav,'-t',str(self.threads),'-l','en','-nt','-np']
    async def transcribe(self, mulaw: bytes) -> str:
        if not Path(self.model).is_file():raise FileNotFoundError('WHISPER_MODEL missing')
        if not mulaw:return ''
        def run():
            with tempfile.TemporaryDirectory(prefix='receptionist-asr-') as folder:
                path=Path(folder)/'utterance.wav'
                with wave.open(str(path),'wb') as f:
                    f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
                    f.writeframes(resample_pcm16(pcm16_from_mulaw(mulaw),8000,16000))
                result=subprocess.run(self.args(str(path)),capture_output=True,text=True,timeout=self.timeout,check=True)
                return clean_whisper_output(result.stdout)
        return await asyncio.to_thread(run)

class MockTTS:
    async def synthesize(self,text: str) -> bytes:
        # Silence is a transport fixture, not synthesized speech.
        return b'\xff'*320

class PiperTTS:
    """rhasspy/piper 2023.11.14-2: one utterance per stdin line, --model, --output_file."""
    def __init__(self, executable: str, model: str, timeout:int=35):
        self.executable,self.model,self.timeout=executable,model,timeout
    async def synthesize(self,text: str) -> bytes:
        if not Path(self.model).is_file():raise FileNotFoundError('PIPER_MODEL missing')
        line=' '.join(text.split())
        if not line:raise ValueError('empty TTS text')
        def run():
            with tempfile.TemporaryDirectory(prefix='receptionist-tts-') as folder:
                out=Path(folder)/'reply.wav'
                subprocess.run([self.executable,'--model',self.model,'--output_file',str(out)],input=line+'\n',capture_output=True,timeout=self.timeout,check=True)
                with wave.open(str(out),'rb') as f:
                    if f.getnchannels()!=1 or f.getsampwidth()!=2:raise ValueError('Piper output must be mono PCM16')
                    raw=f.readframes(f.getnframes()); rate=f.getframerate()
                out_mulaw=mulaw_from_pcm16(resample_pcm16(raw,rate,8000))
                if rms_mulaw(out_mulaw)<AUDIBLE_RMS:raise ValueError('Piper output is silent')
                return out_mulaw
        return await asyncio.to_thread(run)
