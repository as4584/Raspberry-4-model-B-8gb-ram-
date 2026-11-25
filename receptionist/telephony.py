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
VOICE_MIN_RMS = 650            # fixed floor for speech (u-law RMS)
NOISY_FLOOR_RMS = 1500         # background this loud makes short words hard to hear (15 dB test line: ~1800)
HARD_TO_HEAR = ("repeat", "ask_again", "confirm_again", "unknown")
NOISE_NOTICE = ("Sorry, there's a lot of background noise on your end, and I'm having a hard time hearing you. "
                "Could you move somewhere quieter, or speak a little closer to the phone?")
# With a voice detector (Silero): speech must be loud AND human. Plain background noise lets the
# loudness margin relax (Silero tells voice from noise); background people talking keep it strict.
VAD_ON, VAD_OFF = 0.5, 0.35         # start / keep-going speech probability
LOOSE_MARGIN = 1.5
BACKGROUND_SPEECH = 0.3             # average probability between turns that means 'people talking nearby'
TURN_CAP_BYTES = 64000              # 8 s: background voices can't hold a turn open
NOISE_MARGIN = 2.5             # speech must be this many times the call's noise floor
CALIBRATION_FRAMES = 25        # first 0.5 s of a greeted call: measure line noise
FILLER = "One moment."
FILLER_AFTER_S = 0.4          # speak FILLER if the brain hasn't answered by then
PREROLL_BYTES = 2400           # 300 ms kept from before the VAD trigger (soft onsets)


def caller_number(value: object) -> str | None:
    return value if isinstance(value, str) and CALLER_RE.fullmatch(value) else None


def review_key(number: str | None) -> str | None:
    """Last 10 digits of a US number, so '+19736530992', '19736530992' and '9736530992' match."""
    digits = re.sub(r'\D', '', number or '')
    return digits[-10:] if len(digits) >= 10 else None


class ReviewLog:
    """Owner-approved review mode: one JSONL file per call, root-only (600)."""

    def __init__(self, folder: str, call_id: str):
        os.makedirs(folder, mode=0o700, exist_ok=True)
        self.path = os.path.join(folder, f"{time.strftime('%Y%m%d-%H%M%S')}-{call_id}.jsonl")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self._f = os.fdopen(fd, 'a', encoding='utf-8')

    def write(self, **row):
        import json
        self._f.write(json.dumps({"t": round(time.time(), 3), **row}) + "\n"); self._f.flush()

    def close(self):
        self._f.close()


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
                 transfer_to: str | None = None, review_dir: str | None = None,
                 review_callers: set[str] | None = None, dialogue_factory=None,
                 greeting: str | None = None, vad_factory=None):
        self.store, self.asr, self.tts, self.require_caller_id = store, asr, tts, require_caller_id
        self.review_dir, self.review_callers = review_dir, set(review_callers or ())
        # One conversation object per call (rule Dialogue, or the tiny-model Brain).
        self.dialogue_factory = dialogue_factory or (lambda: Dialogue(store, require_caller_id=require_caller_id))
        self.transfer_to = transfer_to   # a human's number; None -> offer a callback instead
        self.greet = greet           # answer like a person; the legacy Twilio path keeps its old behaviour
        self.greeting = greeting or GREETING
        self.vad_factory = vad_factory   # per-call voice detector stream (Silero), or None

    async def run(self, transport: CallTransport) -> None:
        asr, tts = self.asr, self.tts
        call_id: str | None = None
        dialogue: Dialogue | None = None
        caller_id: str | None = None
        generation = 0
        audio = bytearray(); heard_speech = False; silence_ms = 0.0; spoken_ms = 0.0
        preroll = bytearray()
        # Audio of utterances whose reply hasn't started playing yet. If the caller speaks
        # again first, it is carried into the next utterance instead of being lost.
        unanswered: dict[int, bytes] = {}
        # Words of a turn the caller talked over, waiting to be heard with what they say next.
        # Carrying text (not audio) keeps every transcription short: re-transcribing merged audio
        # grew to 16 s on the owner's call and the receptionist went silent for 30 s.
        carried: dict[int, str] = {}
        hearing: dict[int, asyncio.Future] = {}   # transcriptions still running, by turn
        review: ReviewLog | None = None
        last_voiced = time.monotonic()
        speech_end: dict[int, float] = {}       # generation -> time of its last voiced frame
        noise_floor = 0.0      # this call's line-noise level (u-law RMS), learned from non-speech
        frames_seen = 0
        vad = self.vad_factory() if self.vad_factory else None
        background_speech = 0.0   # how much the between-turns background sounds like people
        # Serialises this call's dialogue across worker threads: a barge-in cancels the
        # awaiting task, but its thread keeps running until handle() returns.
        dialogue_lock = threading.Lock()
        noise_told = [False]   # say it once per call, like a person would
        jobs: set[asyncio.Task] = set()

        def handle_locked(text: str, session: str):
            with dialogue_lock:
                return dialogue.handle(text, session, caller_id=caller_id)

        async def speak(words: str, this_generation: int, mark_name: str, t_ref: float):
            # Synthesise sentence k+1 while sentence k streams: the caller hears the first
            # sentence without waiting for the whole reply.
            parts = split_sentences(words) or [words]
            t_tts = time.monotonic()
            pending = asyncio.create_task(tts.synthesize(parts[0]))
            try:
                for k in range(len(parts)):
                    outbound = await pending
                    if k == 0:
                        log.info('call=%s gen=%d phase=first_audio_ready tts_first_s=%.2f since_asr_done_s=%.2f',
                                 call_id, this_generation, time.monotonic() - t_tts, time.monotonic() - t_ref)
                    pending = asyncio.create_task(tts.synthesize(parts[k + 1])) if k + 1 < len(parts) else None
                    if this_generation != generation:
                        return
                    for i in range(0, len(outbound), FRAME_BYTES):
                        if generation != this_generation:
                            return
                        unanswered.pop(this_generation, None)     # the caller is hearing a reply
                        if this_generation in speech_end:
                            latency = time.monotonic() - speech_end.pop(this_generation)
                            log.info('call=%s gen=%d phase=reply_latency end_of_speech_to_audio_s=%.2f', call_id,
                                     this_generation, latency)
                            if review:
                                review.write(type="latency", gen=this_generation, end_of_speech_to_audio_s=round(latency, 2))
                        await transport.send_frame(outbound[i:i + FRAME_BYTES])
            finally:
                if pending and not pending.done():
                    pending.cancel()
            if this_generation == generation:
                await transport.mark(mark_name)

        async def greet(this_generation: int):
            try:
                await speak(self.greeting, this_generation, 'greeting', time.monotonic())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning('call=%s greeting failed', call_id, exc_info=True)

        async def process(packet: bytes, this_generation: int):
            try:
                t0 = time.monotonic()
                unanswered[this_generation] = packet
                heard = asyncio.ensure_future(asr.transcribe(packet))
                # Talking over a turn must not throw its words away: transcription finishes on its own
                # and the words wait for the caller's next turn.
                def keep(t, g=this_generation):
                    if g != generation and not t.cancelled() and t.exception() is None and t.result():
                        carried[g] = t.result()
                heard.add_done_callback(keep)
                hearing[this_generation] = heard
                heard.add_done_callback(lambda _t, g=this_generation: hearing.pop(g, None))
                utterance = await asyncio.shield(heard)
                unanswered.pop(this_generation, None)
                t_asr = time.monotonic()
                log.info('call=%s gen=%d phase=asr audio_s=%.2f took_s=%.2f stale=%s', call_id, this_generation,
                         len(packet) / 8000, t_asr - t0, this_generation != generation)
                if this_generation != generation:
                    if utterance:
                        carried[this_generation] = utterance
                    return
                # An earlier turn may still be transcribing (slow): its words come first.
                still = [t for g, t in hearing.items() if g < this_generation]
                if still:
                    await asyncio.wait(still, timeout=3.0)
                if this_generation != generation:
                    if utterance:
                        carried[this_generation] = utterance
                    return
                earlier = [carried.pop(g) for g in sorted(carried) if g < this_generation]
                utterance = " ".join(earlier + ([utterance] if utterance else [])).strip()
                if not utterance or not dialogue:
                    return
                # Booking does blocking SQLite I/O (busy waits up to 15 s); keep it off the
                # event loop so other calls keep streaming.
                t_d = time.monotonic()
                work = asyncio.ensure_future(asyncio.to_thread(handle_locked, utterance, call_id or 'invalid'))
                done, _ = await asyncio.wait({work}, timeout=FILLER_AFTER_S)
                if not done:
                    # A human says "one moment" instead of leaving dead air (Pi: bookings sometimes
                    # took 2-5 s on the slow USB stick). Honest: it claims nothing.
                    filler = await tts.synthesize(FILLER)
                    if this_generation == generation:
                        for i in range(0, len(filler), FRAME_BYTES):
                            if generation != this_generation:
                                break
                            await transport.send_frame(filler[i:i + FRAME_BYTES])
                reply = await work
                # The action name (never the caller's words) is safe to log and explains outcomes.
                log.info('call=%s gen=%d phase=dialogue action=%s words=%d dialogue_s=%.2f', call_id, this_generation,
                         reply.action, len(utterance.split()), time.monotonic() - t_d)
                if os.getenv('LOG_TRANSCRIPTS') == '1':      # test runs only: never on by default
                    log.info('call=%s gen=%d heard=%r', call_id, this_generation, utterance)
                words = reply.speech or reply.text   # phone wording when the dialogue provides it (#38)
                if reply.action in HARD_TO_HEAR and noise_floor >= NOISY_FLOOR_RMS and not noise_told[0]:
                    # It misheard on a loud line: say why, instead of a bare "didn't catch that".
                    noise_told[0] = True
                    words = NOISE_NOTICE if reply.action == "repeat" else f"{NOISE_NOTICE} {words}"
                    log.info('call=%s gen=%d phase=noise_notice floor=%.0f', call_id, this_generation, noise_floor)
                if review:
                    review.write(type="turn", gen=this_generation, heard=utterance, action=reply.action,
                                 reply=words, audio_s=round(len(packet) / 8000, 2), asr_s=round(t_asr - t0, 2))
                transfer = reply.action == 'handoff' and self.transfer_to
                if transfer:
                    words = TRANSFER_SPEECH
                await speak(words, this_generation, f'answer-{this_generation}', t_asr)
                if transfer and this_generation == generation:
                    await transport.transfer(self.transfer_to)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning('call=%s gen=%d phase=error %s: %s', call_id, this_generation, type(exc).__name__, exc)
                if this_generation == generation:
                    # Avoid lying about booking success if ASR/TTS fails.
                    await transport.mark('processing-error')

        def spawn(packet: bytes, ticket: int):
            task = asyncio.create_task(process(packet, ticket))
            jobs.add(task); task.add_done_callback(jobs.discard)

        def end_turn():
            nonlocal heard_speech, silence_ms, spoken_ms
            snapshot, ticket = bytes(audio), generation
            speech_end[ticket] = last_voiced
            audio.clear(); heard_speech = False; silence_ms = 0; spoken_ms = 0
            spawn(snapshot, ticket)

        last_audio = time.monotonic()

        async def silence_watchdog():
            # Networks with silence suppression (DTX) -- and SIPp -- send no packets while
            # the caller is quiet, so counting quiet frames alone can leave a turn open
            # forever. 300 ms with no audio at all after speech also ends the turn.
            while True:
                await asyncio.sleep(0.05)
                if heard_speech and spoken_ms >= 80 and time.monotonic() - last_audio >= 0.3:
                    end_turn()

        watchdog = asyncio.create_task(silence_watchdog())
        try:
            async for event in transport.events():
                if isinstance(event, CallStart):
                    call_id, caller_id = event.call_id, event.caller_id
                    if self.review_dir and review_key(caller_id) in self.review_callers:
                        review = ReviewLog(self.review_dir, call_id)
                        review.write(type="start", call=call_id)
                    dialogue = self.dialogue_factory()
                    if self.greet:
                        task = asyncio.create_task(greet(generation))   # barge-in cancels it like any reply
                        jobs.add(task); task.add_done_callback(jobs.discard)
                    continue
                if not isinstance(event, AudioIn) or call_id is None or not event.data:
                    continue
                data = event.data
                last_audio = time.monotonic()
                # Simple VAD. Runs on speech+silence, leaving heavier ASR on a background task.
                ms = 1000 * len(data) / 8000
                # Speech = clearly above this call's noise floor, never below the old fixed
                # trigger. Real Telnyx line noise sat above the fixed 650 and kept turns open
                # (a 1-word turn reached whisper as 3.5 s); a silent line behaves as before.
                level = rms_mulaw(data)
                frames_seen += 1
                p = vad.feed(data) if vad else 1.0
                if self.greet and frames_seen <= CALIBRATION_FRAMES:
                    # Caller is listening to the greeting: learn the line's noise, never trigger.
                    noise_floor = level if frames_seen == 1 else 0.8 * noise_floor + 0.2 * level
                    background_speech = p if frames_seen == 1 else 0.8 * background_speech + 0.2 * p
                    continue
                margin = NOISE_MARGIN
                if vad and background_speech <= BACKGROUND_SPEECH:
                    margin = LOOSE_MARGIN
                voiced = level > max(VOICE_MIN_RMS, margin * noise_floor)
                if vad:
                    voiced = voiced and p >= (VAD_OFF if heard_speech else VAD_ON)
                if not voiced and not heard_speech:
                    noise_floor = 0.95 * noise_floor + 0.05 * level
                    if vad:
                        background_speech = 0.98 * background_speech + 0.02 * p
                if voiced:
                    last_voiced = time.monotonic()
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
                    if vad and len(audio) >= TURN_CAP_BYTES:
                        end_turn()             # background voices held it open: answer what we have
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
                        end_turn()
        finally:
            watchdog.cancel()
            if review:
                review.write(type="end"); review.close()
            for task in list(jobs):
                task.cancel()
            if jobs:
                await asyncio.gather(*jobs, return_exceptions=True)
