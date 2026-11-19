"""Minimal ARI client (loopback only) for call control the media socket can't do:
setting TRANSFER_TO on the caller's channel. Standard library only."""
from __future__ import annotations

import asyncio
import base64
import urllib.parse
import urllib.request


class ARIClient:
    def __init__(self, password: str, user: str = 'receptionist', base: str = 'http://127.0.0.1:8088/ari'):
        if not base.startswith('http://127.0.0.1:'):
            raise ValueError('ARI must stay on loopback')
        self.base = base
        self._auth = 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()

    def _post(self, path: str, params: dict[str, str]) -> int:
        url = f'{self.base}{path}?{urllib.parse.urlencode(params)}'
        req = urllib.request.Request(url, method='POST', headers={'Authorization': self._auth})
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status

    async def set_variable(self, channel: str, name: str, value: str) -> None:
        path = f'/channels/{urllib.parse.quote(channel, safe="")}/variable'
        await asyncio.to_thread(self._post, path, {'variable': name, 'value': value})
