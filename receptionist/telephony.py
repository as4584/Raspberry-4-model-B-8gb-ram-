"""Provider-independent call handling (docs/TELEPHONY.md, T1).

A transport (Asterisk chan_websocket, legacy Twilio Media Streams, or a test fake)
turns its wire protocol into CallStart / AudioIn events and offers send_frame, flush,
mark, hangup and transfer. CallEngine owns everything else: VAD, ASR, the dialogue,
sentence-pipelined TTS and barge-in. It never sees a provider.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from receptionist.audio import rms_mulaw
from receptionist.booking import BookingStore
from receptionist.dialogue import GREETING, Dialogue

log = logging.getLogger('receptionist.call')

CALLER_RE = re.compile(r'\+?[0-9]{5,15}')
MAX_UTTERANCE_BYTES = 160000   # 20 s of 8 kHz u-law per transcription chunk
FRAME_BYTES = 160              # 20 ms of 8 kHz u-law
TRANSFER_SPEECH = "Okay, let me put you through to someone now."
PREROLL_BYTES = 2400           # 300 ms kept from before the VAD trigger (soft onsets)


def caller_number(value: object) -> str | None:
    return value if isinstance(value, str) and CALLER_RE.fullmatch(value) else None


def split_sentences(text: str) -> list[str]:
    # Split only where punctuation is followed by whitespace, so "1.2.3" and times stay whole.
    return [p for p in re.split(r'(?<=[.!?])\s+', text.strip()) if p]


@dataclass(frozen=True)
class CallStart:
    call_id: str
    caller_id: str | None = None


@dataclass(frozen=True)
class AudioIn:
    data: bytes          # 8 kHz u-law, any length (usually one 20 ms frame)


class CallTransport(Protocol):
    def events(self) -> AsyncIterator[CallStart | AudioIn]: ...
    async def send_frame(self, frame: bytes) -> None: ...   # transport paces/buffers
    async def flush(self) -> None: ...                       # barge-in: drop queued audio
    async def mark(self, name: str) -> None: ...
    async def hangup(self) -> None: ...
    async def transfer(self, target: str) -> None: ...


class CallEngine:
    """One instance per service; run() once per call."""

    def __init__(self, store: BookingStore, asr, tts, require_caller_id: bool = False, greet: bool = False,
                 transfer_to: str | None = None):
        self.store, self.asr, self.tts, self.require_caller_id = store, asr, tts, require_caller_id
        self.transfer_to = transfer_to   # a human's number; None -> offer a callback instead
        self.greet = greet           # answer like a person; the legacy Twilio path keeps its old behaviour

    async def run(self, transport: CallTransport) -> None:
        asr, tts = self.asr, self.tts
        call_id: str | None = None
        dialogue: Dialogue | None = None
        caller_id: str | None = None
        generation = 0
        audio = bytearray(); heard_speech = False; silence_ms = 0.0; spoken_ms = 0.0
        preroll = bytearray()
        # Serialises this call's dialogue across worker threads: a barge-in cancels the
        # awaiting task, but its thread keeps running until handle() returns.
        dialogue_lock = threading.Lock()
        jobs: set[asyncio.Task] = set()

        def handle_locked(text: str, session: str):
            with dialogue_lock:
                return dialogue.handle(text, session, caller_id=caller_id)

        async def speak(words: str, this_generation: int, mark_name: str, t_ref: float):
            # Synthesise sentence k+1 while sentence k streams: the caller hears the first
            # sentence without waiting for the whole reply.
            parts = split_sentences(words) or [words]
            pending = asyncio.create_task(tts.synthesize(parts[0]))
            try:
                for k in range(len(parts)):
                    outbound = await pending
                    if k == 0:
                        log.info('call=%s gen=%d phase=first_audio_ready since_asr_done_s=%.2f', call_id,
                                 this_generation, time.monotonic() - t_ref)
                    pending = asyncio.create_task(tts.synthesize(parts[k + 1])) if k + 1 < len(parts) else None
                    if this_generation != generation:
                        return
                    for i in range(0, len(outbound), FRAME_BYTES):
                        if generation != this_generation:
                            return
                        await transport.send_frame(outbound[i:i + FRAME_BYTES])
            finally:
                if pending and not pending.done():
                    pending.cancel()
            if this_generation == generation:
                await transport.mark(mark_name)

        async def greet(this_generation: int):
            try:
                await speak(GREETING, this_generation, 'greeting', time.monotonic())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning('call=%s greeting failed', call_id, exc_info=True)

        async def process(packet: bytes, this_generation: int):
            try:
                t0 = time.monotonic()
                utterance = await asr.transcribe(packet)
                t_asr = time.monotonic()
                log.info('call=%s gen=%d phase=asr audio_s=%.2f took_s=%.2f stale=%s', call_id, this_generation,
                         len(packet) / 8000, t_asr - t0, this_generation != generation)
                if this_generation != generation or not utterance or not dialogue:
                    return
                # Booking does blocking SQLite I/O (busy waits up to 15 s); keep it off the
                # event loop so other calls keep streaming.
                reply = await asyncio.to_thread(handle_locked, utterance, call_id or 'invalid')
                # The action name (never the caller's words) is safe to log and explains outcomes.
                log.info('call=%s gen=%d phase=dialogue action=%s words=%d', call_id, this_generation,
                         reply.action, len(utterance.split()))
                if os.getenv('LOG_TRANSCRIPTS') == '1':      # test runs only: never on by default
                    log.info('call=%s gen=%d heard=%r', call_id, this_generation, utterance)
                words = reply.speech or reply.text   # phone wording when the dialogue provides it (#38)
                transfer = reply.action == 'handoff' and self.transfer_to
                if transfer:
                    words = TRANSFER_SPEECH
                await speak(words, this_generation, f'answer-{this_generation}', t_asr)
                if transfer and this_generation == generation:
                    await transport.transfer(self.transfer_to)
            except asyncio.CancelledError:
                raise
            except Exception:
                if this_generation == generation:
                    # Avoid lying about booking success if ASR/TTS fails.
                    await transport.mark('processing-error')

        def spawn(packet: bytes, ticket: int):
            task = asyncio.create_task(process(packet, ticket))
            jobs.add(task); task.add_done_callback(jobs.discard)

        try:
            async for event in transport.events():
                if isinstance(event, CallStart):
                    call_id, caller_id = event.call_id, event.caller_id
                    dialogue = Dialogue(self.store, require_caller_id=self.require_caller_id)
                    if self.greet:
                        task = asyncio.create_task(greet(generation))   # barge-in cancels it like any reply
                        jobs.add(task); task.add_done_callback(jobs.discard)
                    continue
                if not isinstance(event, AudioIn) or call_id is None or not event.data:
                    continue
                data = event.data
                # Simple VAD. Runs on speech+silence, leaving heavier ASR on a background task.
                ms = 1000 * len(data) / 8000
                if rms_mulaw(data) > 650:
                    if not heard_speech:
                        generation += 1
                        for task in list(jobs):
                            task.cancel()
                        await transport.flush()
                        audio.extend(preroll); preroll.clear()
                    heard_speech = True; silence_ms = 0; spoken_ms += ms
                    if len(audio) + len(data) > MAX_UTTERANCE_BYTES:
                        # A long unbroken talker is a normal caller: transcribe what we
                        # have (bounded memory) and keep listening instead of hanging up.
                        spawn(bytes(audio), generation)
                        audio.clear()
                    audio.extend(data)
                elif not heard_speech:
                    # Soft onsets ("b" in "book") sit below the trigger: keep the last 300 ms.
                    preroll.extend(data)
                    del preroll[:max(0, len(preroll) - PREROLL_BYTES)]
                elif heard_speech:
                    # Keep quiet frames once speech started: soft consonants and short
                    # gaps between words are part of the phrase (dropping them made whisper
                    # hear choppy audio on the Pi). The trailing <300 ms of silence is harmless.
                    if len(audio) + len(data) <= MAX_UTTERANCE_BYTES:
                        audio.extend(data)
                    silence_ms += ms
                    if silence_ms >= 300 and spoken_ms >= 80:
                        snapshot, ticket = bytes(audio), generation
                        audio.clear(); heard_speech = False; silence_ms = 0; spoken_ms = 0
                        spawn(snapshot, ticket)
        finally:
            for task in list(jobs):
                task.cancel()
            if jobs:
                await asyncio.gather(*jobs, return_exceptions=True)
