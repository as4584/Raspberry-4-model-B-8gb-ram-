"""Legacy Twilio Media Streams transport (removed in T7, docs/TELEPHONY.md).
Untrusted input: anything malformed is a policy close (1008), oversized media 1009."""
from __future__ import annotations

import asyncio
import base64
import binascii

from fastapi import WebSocket

from receptionist.telephony import AudioIn, CallStart, caller_number


class TwilioMediaStreamTransport:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.stream_id: str | None = None
        self._send_lock = asyncio.Lock()

    async def _send(self, event: dict):
        async with self._send_lock:
            await self.ws.send_json(event)

    async def events(self):
        ws = self.ws
        while True:
            try:
                evt = await ws.receive_json()
            except ValueError:
                await ws.close(code=1008); return
            if not isinstance(evt, dict):
                await ws.close(code=1008); return
            event = evt.get('event')
            if event == 'connected':
                continue
            if event == 'start':
                start = evt.get('start')
                if (self.stream_id or not isinstance(start, dict) or not isinstance(evt.get('streamSid'), str)
                        or not evt['streamSid'] or not isinstance(start.get('callSid'), str) or not start['callSid']):
                    await ws.close(code=1008); return
                self.stream_id = evt['streamSid']
                custom = start.get('customParameters')
                yield CallStart(start['callSid'], caller_number(custom.get('callerId')) if isinstance(custom, dict) else None)
            elif event == 'media':
                if not self.stream_id or evt.get('streamSid') != self.stream_id:
                    await ws.close(code=1008); return
                body = evt.get('media')
                if not isinstance(body, dict):
                    await ws.close(code=1008); return
                payload = body.get('payload', '')
                if not isinstance(payload, str) or len(payload) > 20000:
                    await ws.close(code=1009); return
                try:
                    data = base64.b64decode(payload, validate=True)
                except (binascii.Error, ValueError):
                    await ws.close(code=1008); return
                yield AudioIn(data)
            elif event == 'mark':
                continue
            elif event == 'stop':
                return
            else:
                await ws.close(code=1008); return

    async def send_frame(self, frame: bytes):
        await self._send({'event': 'media', 'streamSid': self.stream_id,
                          'media': {'payload': base64.b64encode(frame).decode()}})
        await asyncio.sleep(0.02)          # Twilio plays what it gets; we pace in real time

    async def flush(self):
        if self.stream_id:
            await self._send({'event': 'clear', 'streamSid': self.stream_id})

    async def mark(self, name: str):
        await self._send({'event': 'mark', 'streamSid': self.stream_id, 'mark': {'name': name}})

    async def hangup(self):
        await self.ws.close(code=1000)

    async def transfer(self, target: str):
        raise NotImplementedError('transfers are an Asterisk/ARI feature (T6)')
