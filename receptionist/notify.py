"""Lead alerts: when the receptionist saves a message, tell the owner right away.

NOTIFY_URL (the owner's own endpoint, e.g. his Agent Hub) gets a POST of
{name, callback, urgent, language}: who to call back, never what they said.
NOTIFY_TOKEN, if set, goes in the Authorization header; it lives in Doppler, never in git.
Sent on a background thread with a short timeout: a down endpoint never slows or breaks a call."""
from __future__ import annotations

import json
import logging
import threading
import urllib.request

log = logging.getLogger("receptionist.notify")
FIELDS = ("name", "callback", "urgent", "language")


def webhook_notifier(env, timeout: float = 5.0):
    url = env.get("NOTIFY_URL")
    if not url:
        return None
    token = env.get("NOTIFY_TOKEN")

    def send(lead: dict) -> None:
        body = json.dumps({k: lead.get(k) for k in FIELDS}).encode()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        def post():
            try:
                urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers, method="POST"),
                                       timeout=timeout).close()
                log.info("lead alert sent")
            except Exception as exc:              # the URL and token are never logged
                log.warning("lead alert failed: %s", type(exc).__name__)

        threading.Thread(target=post, daemon=True).start()

    return send
