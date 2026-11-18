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
    # -nf + short window: whisper may restart the sentence and get cut off
    # ("Book March 15 at 1 p.m. Book March 15 at"). A tail of 2+ words that is a
    # strict prefix of the first sentence is that restart, not new speech.
    if len(out)>=2:
        words=lambda s:re.sub(r'[^\w\s]','',s.lower()).split()
        first,tail=words(out[0]),words(' '.join(out[1:]))
        if 2<=len(tail)<len(first) and first[:len(tail)]==tail:out=out[:1]
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
    def __init__(self, executable: str, model: str, timeout: int=40, threads: int=4, adaptive_ctx: bool=False,
                 greedy: bool=True):
        self.executable,self.model,self.timeout,self.threads=executable,model,timeout,threads
        self.adaptive_ctx,self.greedy=adaptive_ctx,greedy
    def args(self, wav: str, seconds: float|None=None) -> list[str]:
        a=[self.executable,'-m',self.model,'-f',wav,'-t',str(self.threads),'-l','en','-nt','-np']
        if self.adaptive_ctx and seconds is not None:a+=['-ac',str(audio_ctx_for(seconds))]
        # #28 sweep: greedy matched beam-5 accuracy on every model, 0 runaways, and is faster.
        if self.greedy:a+=['-bs','1','-bo','1']
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

class WhisperDaemonASR:
    """Resident whisper (native/whisper_daemon.cpp): model loaded once, short encoder
    window, greedy, no fallback, decode capped at ~8 tokens per second of audio + 10.
    Pi (#28): 32/40 exact, p50 0.72 s, max 0.89 s vs 7.1 s for whisper-cli.
    One utterance at a time; a cancelled (barged-in) decode kills the process and
    the next call respawns it (~0.25 s), so a stale answer can never be read."""
    def __init__(self, argv: list[str], timeout: float=10.0, tokens_per_s: int=8, extra_tokens: int=10):
        self.argv,self.timeout=list(argv),timeout
        self.tokens_per_s,self.extra_tokens=tokens_per_s,extra_tokens
        self._proc=None;self._lock=None
    @property
    def pid(self):return self._proc.pid if self._proc else None
    async def _ensure(self):
        if self._proc and self._proc.returncode is None:return self._proc
        self._proc=await asyncio.create_subprocess_exec(*self.argv,stdin=asyncio.subprocess.PIPE,
                                                        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        ready=await asyncio.wait_for(self._proc.stdout.readline(),self.timeout)
        if ready.strip()!=b'READY':await self._kill();raise RuntimeError('whisper daemon did not start')
        return self._proc
    async def _kill(self):
        proc,self._proc=self._proc,None
        if proc and proc.returncode is None:
            proc.kill();await asyncio.shield(proc.wait())
        if proc and proc.stdin and not proc.stdin.is_closing():proc.stdin.close()
    async def aclose(self):
        """Stop the resident process (server shutdown)."""
        await self._kill()
    async def transcribe(self, mulaw: bytes) -> str:
        if not mulaw:return ''
        self._lock=self._lock or asyncio.Lock()
        seconds=len(mulaw)/8000
        async with self._lock:
            with tempfile.TemporaryDirectory(prefix='receptionist-asr-') as folder:
                raw=Path(folder)/'utterance.raw'
                raw.write_bytes(resample_pcm16(pcm16_from_mulaw(mulaw),8000,16000))
                cap=math.ceil(seconds*self.tokens_per_s)+self.extra_tokens
                try:
                    proc=await self._ensure()
                    proc.stdin.write(f'{raw}\t{audio_ctx_for(seconds)}\t{cap}\n'.encode())
                    await proc.stdin.drain()
                    line=await asyncio.wait_for(proc.stdout.readline(),self.timeout)
                except BaseException:
                    await self._kill()
                    raise
            text=line.decode('utf-8','replace').strip()
            if not line or text.startswith('ERR'):
                await self._kill();raise RuntimeError(f'whisper daemon failed: {text or "exited"}')
            return clean_whisper_output(text)

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

def _wav_to_phone(path: Path) -> bytes:
    with wave.open(str(path),'rb') as f:
        if f.getnchannels()!=1 or f.getsampwidth()!=2:raise ValueError('Piper output must be mono PCM16')
        raw=f.readframes(f.getnframes()); rate=f.getframerate()
    out=mulaw_from_pcm16(resample_pcm16(raw,rate,8000))
    if rms_mulaw(out)<AUDIBLE_RMS:raise ValueError('Piper output is silent')
    return out

class PiperDaemonTTS:
    """Resident Piper (--output_dir: one stdin line -> one wav path on stdout). The voice
    model loads once (~1.2 s on the Pi), so a reply sentence costs only its synthesis.
    A barge-in cancel does NOT kill Piper (a reload costs more than finishing): the
    sentence in flight completes in the background and its audio is discarded."""
    def __init__(self, argv: list[str], model: str, timeout: float=35.0, extra_args: list[str]|None=None):
        self.argv,self.model,self.timeout,self.extra_args=list(argv),model,timeout,list(extra_args or [])
        self._proc=None;self._lock=None;self._dir=None
    @property
    def pid(self):return self._proc.pid if self._proc else None
    async def _ensure(self):
        if self._proc and self._proc.returncode is None:return self._proc
        if not Path(self.model).is_file():raise FileNotFoundError('PIPER_MODEL missing')
        self._dir=self._dir or tempfile.TemporaryDirectory(prefix='receptionist-tts-')
        self._proc=await asyncio.create_subprocess_exec(*self.argv,'--model',self.model,'--output_dir',self._dir.name,
                                                        *self.extra_args,stdin=asyncio.subprocess.PIPE,
                                                        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
        return self._proc
    async def _one(self, line: str) -> bytes:
        async with self._lock:
            try:
                proc=await self._ensure()
                proc.stdin.write((line+'\n').encode('utf-8'));await proc.stdin.drain()
                out=await asyncio.wait_for(proc.stdout.readline(),self.timeout)
            except BaseException:
                await self.aclose();raise
            if not out:await self.aclose();raise RuntimeError('piper exited')
            path=Path(out.decode('utf-8','replace').strip())
            try:return await asyncio.to_thread(_wav_to_phone,path)
            finally:path.unlink(missing_ok=True)
    async def synthesize(self, text: str) -> bytes:
        line=' '.join(text.split())
        if not line:raise ValueError('empty TTS text')
        self._lock=self._lock or asyncio.Lock()
        # Shield: a cancelled caller abandons the result, but the request/response pair on
        # Piper's pipe still completes, so the next sentence can never read a stale path.
        return await asyncio.shield(asyncio.ensure_future(self._one(line)))
    async def aclose(self):
        proc,self._proc=self._proc,None
        if proc and proc.returncode is None:
            proc.kill();await asyncio.shield(proc.wait())
        if proc and proc.stdin and not proc.stdin.is_closing():proc.stdin.close()

class CachingTTS:
    """Short fixed phrases ("Okay.", "Say yes or no.") synthesise once and then play
    instantly; variable or long text (dates, booking codes) always goes to the voice."""
    def __init__(self, inner, max_items: int=64, max_chars: int=40):
        from collections import OrderedDict
        self.inner,self.max_items,self.max_chars=inner,max_items,max_chars
        self._cache:OrderedDict[str,bytes]=OrderedDict()
    async def synthesize(self, text: str) -> bytes:
        key=' '.join(text.split())
        if key in self._cache:
            self._cache.move_to_end(key);return self._cache[key]
        audio=await self.inner.synthesize(text)
        if len(key)<=self.max_chars and not any(c.isdigit() for c in key):
            self._cache[key]=audio
            while len(self._cache)>self.max_items:self._cache.popitem(last=False)
        return audio
    async def prewarm(self, phrases):
        for p in phrases:await self.synthesize(p)
    async def aclose(self):
        if hasattr(self.inner,'aclose'):await self.inner.aclose()
