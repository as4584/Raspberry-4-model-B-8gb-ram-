"""Asterisk chan_websocket transport (docs/TELEPHONY.md, T3).

Asterisk 22.12 dials WebSocket/receptionist/c(ulaw)v(caller=...) and connects here.
Binary frames are raw 8 kHz u-law; text frames are JSON (control_message_format=json):
events MEDIA_START / MEDIA_XOFF / MEDIA_XON / MEDIA_MARK_PROCESSED from Asterisk, and our
commands FLUSH_MEDIA / MARK_MEDIA / HANGUP. Asterisk re-times the audio we send, so we
don't pace it ourselves; we only stop while it signals MEDIA_XOFF (its queue is full).
"""
from __future__ import annotations

import asyncio
import json

from fastapi import WebSocket

from receptionist.telephony import AudioIn, CallStart, caller_number

MAX_FRAME_BYTES = 8000          # 1 s of u-law; Asterisk sends ~160-byte frames


class AsteriskWebSocketTransport:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.channel_id: str | None = None
        self._xon = asyncio.Event(); self._xon.set()
        self._send_lock = asyncio.Lock()

    async def _command(self, command: str, **fields):
        async with self._send_lock:
            await self.ws.send_text(json.dumps({'command': command, **fields}))

    async def events(self):
        ws = self.ws
        caller = caller_number(ws.query_params.get('caller'))
        while True:
            msg = await ws.receive()
            if msg.get('type') == 'websocket.disconnect':
                return
            if msg.get('bytes') is not None:
                data = msg['bytes']
                if len(data) > MAX_FRAME_BYTES:
                    await ws.close(code=1009); return
                if self.channel_id:
                    yield AudioIn(data)
                continue
            try:
                evt = json.loads(msg.get('text') or '')
            except ValueError:
                await ws.close(code=1008); return
            if not isinstance(evt, dict):
                await ws.close(code=1008); return
            kind = evt.get('event')
            if kind == 'MEDIA_START':
                cid = evt.get('channel_id')
                if self.channel_id or not isinstance(cid, str) or not cid:
                    await ws.close(code=1008); return
                self.channel_id = cid
                yield CallStart(cid, caller)
            elif kind == 'MEDIA_XOFF':
                self._xon.clear()
            elif kind == 'MEDIA_XON':
                self._xon.set()
            # MEDIA_MARK_PROCESSED, QUEUE_DRAINED, DTMF_END, STATUS ...: informational

    async def send_frame(self, frame: bytes):
        await self._xon.wait()
        async with self._send_lock:
            await self.ws.send_bytes(frame)

    async def flush(self):
        await self._command('FLUSH_MEDIA')

    async def mark(self, name: str):
        await self._command('MARK_MEDIA', correlation_id=name)

    async def hangup(self):
        await self._command('HANGUP')

    async def transfer(self, target: str):
        raise NotImplementedError('transfer goes through ARI (T6)')
