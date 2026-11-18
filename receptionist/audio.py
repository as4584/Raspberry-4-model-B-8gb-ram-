"""Local audio codecs and replaceable ASR/TTS adapters (no cloud inference)."""
from __future__ import annotations
from pathlib import Path
import asyncio
import math
import re
import struct
import subprocess
import sys
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

# Lookup tables built once from the scalar reference above: bit-identical
# output, but per-frame work becomes C-speed table lookups (hot path on a Pi).
_ULAW_PCM16=[struct.pack('<h',ulaw_to_pcm(i)) for i in range(256)]
_ULAW_SQ=[ulaw_to_pcm(i)**2 for i in range(256)]
_PCM_ULAW:bytes|None=None  # 65,536 entries indexed by the unsigned 16-bit sample

def _pcm_ulaw_table() -> bytes:
    global _PCM_ULAW
    if _PCM_ULAW is None:
        _PCM_ULAW=bytes(pcm_to_ulaw(v-65536 if v>=32768 else v) for v in range(65536))
    return _PCM_ULAW

def pcm16_from_mulaw(data: bytes) -> bytes:
    return b''.join(map(_ULAW_PCM16.__getitem__,data))

def mulaw_from_pcm16(data: bytes) -> bytes:
    samples=memoryview(data[:len(data)//2*2]).cast('B').cast('H') if len(data)>=2 else []
    if sys.byteorder!='little' and samples:
        samples=struct.unpack(f'<{len(data)//2}H',data[:len(data)//2*2])
    return bytes(map(_pcm_ulaw_table().__getitem__,samples))

def resample_pcm16(data: bytes, from_hz: int, to_hz: int) -> bytes:
    if from_hz<=0 or to_hz<=0: raise ValueError('positive sample rates required')
    raw=[v for (v,) in struct.iter_unpack('<h',data[:len(data)//2*2])]
    if not raw:return b''
    if from_hz==to_hz:return data
    if to_hz==2*from_hz:
        # 8 kHz -> 16 kHz for whisper: same values as the generic path (midpoints
        # of integers are exact in float, round() is identical), without per-sample float math.
        nxt=raw[1:]+raw[-1:]
        out=[v for a,b in zip(raw,nxt) for v in (a,round((a+b)/2))]
        return struct.pack('<'+'h'*len(out),*out)
    out=[]
    for j in range(round(len(raw)*to_hz/from_hz)):
        x=j*from_hz/to_hz;left=min(int(x),len(raw)-1);right=min(left+1,len(raw)-1)
        v=round(raw[left]+(raw[right]-raw[left])*(x-left))
        out.append(max(-32768,min(32767,v)))
    return struct.pack('<'+'h'*len(out),*out)

def rms_mulaw(data: bytes) -> float:
    if not data:return 0.0
    return math.sqrt(sum(map(_ULAW_SQ.__getitem__,data))/len(data))

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
    return _collapse_repeats(' '.join(words))

def _collapse_repeats(text: str) -> str:
    """Drop whisper's repetition hallucination (seen on the PI4 with a short
    encoder window): consecutive identical sentences, or a word sequence that
    is the same phrase repeated 2-4 times, collapse to one copy."""
    key=lambda s:' '.join(s.lower().split())
    out=[]
    for sentence in re.split(r'(?<=[.!?])\s+',text.strip()):
        if sentence and (not out or key(sentence)!=key(out[-1])):out.append(sentence)
    text=' '.join(out)
    w=text.split()
    for k in (4,3,2):
        if w and len(w)%k==0:
            n=len(w)//k
            if all(key(' '.join(w[i*n:(i+1)*n]))==key(' '.join(w[:n])) for i in range(k)):
                return ' '.join(w[:n])
    return text

def audio_ctx_for(seconds: float) -> int:
    """whisper encoder context for an utterance: 50 frames/s + 64 margin, 128..1500.
    Opt-in only (WhisperCppASR(adaptive_ctx=True)): it cuts encode time ~10x, but
    the PI4 sweep (#28, 320 runs) showed decoding runaways on 13-45% of phrases
    with it and none with the full 1500 window."""
    return min(1500,max(128,math.ceil(seconds*50)+64))

class WhisperCppASR:
    """whisper.cpp `whisper-cli` (pinned v1.9.5): -m model -f wav -t N -l en -nt -np."""
    def __init__(self, executable: str, model: str, timeout: int=40, threads: int=4, adaptive_ctx: bool=False):
        self.executable,self.model,self.timeout,self.threads=executable,model,timeout,threads
        self.adaptive_ctx=adaptive_ctx
    def args(self, wav: str, seconds: float|None=None) -> list[str]:
        a=[self.executable,'-m',self.model,'-f',wav,'-t',str(self.threads),'-l','en','-nt','-np']
        if self.adaptive_ctx and seconds is not None:a+=['-ac',str(audio_ctx_for(seconds))]
        return a
    async def transcribe(self, mulaw: bytes) -> str:
        if not Path(self.model).is_file():raise FileNotFoundError('WHISPER_MODEL missing')
        if not mulaw:return ''
        with tempfile.TemporaryDirectory(prefix='receptionist-asr-') as folder:
            path=Path(folder)/'utterance.wav'
            with wave.open(str(path),'wb') as f:
                f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
                f.writeframes(resample_pcm16(pcm16_from_mulaw(mulaw),8000,16000))
            argv=self.args(str(path),len(mulaw)/8000)
            proc=await asyncio.create_subprocess_exec(*argv,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            try:
                out,err=await asyncio.wait_for(proc.communicate(),self.timeout)
            except BaseException:
                # Barge-in cancel or timeout: kill whisper now, or stale 4-thread decodes
                # pile up on the Pi's 4 cores and slow every later reply (S5).
                if proc.returncode is None:
                    proc.kill()
                    await asyncio.shield(proc.wait())
                raise
            if proc.returncode:raise subprocess.CalledProcessError(proc.returncode,argv,out,err)
            return clean_whisper_output(out.decode('utf-8','replace'))

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
                subprocess.run([self.executable,'--model',self.model,'--output_file',str(out)],input=(line+'\n').encode('utf-8'),capture_output=True,timeout=self.timeout,check=True)
                with wave.open(str(out),'rb') as f:
                    if f.getnchannels()!=1 or f.getsampwidth()!=2:raise ValueError('Piper output must be mono PCM16')
                    raw=f.readframes(f.getnframes()); rate=f.getframerate()
                out_mulaw=mulaw_from_pcm16(resample_pcm16(raw,rate,8000))
                if rms_mulaw(out_mulaw)<AUDIBLE_RMS:raise ValueError('Piper output is silent')
                return out_mulaw
        return await asyncio.to_thread(run)
