# Telephony: Telnyx SIP + Asterisk (replaces Twilio)

Decision (owner, 2026-11-18): calls come in over a **Telnyx SIP trunk** to **Asterisk on the
Pi**. Audio reaches the Python pipeline over **`chan_websocket`**, and **ARI** controls the
call. Twilio is removed once the Asterisk path passes the T-sprint gates below. Until then the
Twilio adapter stays, so `main` keeps working.

## Architecture

```
PSTN caller ──► Telnyx ──SIP/RTP──► Asterisk (Pi) ──chan_websocket──► receptionist (127.0.0.1:8000)
                                       │   binary: raw μ-law 20 ms frames  ◄──►  VAD ► whisper-daemon
                                       │   text:   JSON events / commands        ► Dialogue/BookingStore
                                       └── ARI (127.0.0.1:8088) ◄── call control  ► Piper (resident, cached)
SIPp (local, free) ─SIP/RTP─► Asterisk          (same path; no provider, no minutes)
```

- **Provider-independent seam.** `receptionist/telephony.py` defines `CallTransport`, which
  covers incoming audio, caller number and hangup events, plus outgoing `send_audio`,
  `flush` (barge-in), `mark`, `hangup` and `transfer`. The call engine (VAD, ASR, dialogue,
  TTS and barge-in) talks only to that interface.
  - Adapters: `AsteriskWebSocketTransport` (the product) and `TwilioMediaStreamTransport`
    (legacy, deleted in T7).
  - Swapping Telnyx for another SIP provider is an Asterisk `pjsip.conf` change only; Python
    never sees the provider.
- **Mapping from Twilio:**

  | Twilio | `chan_websocket` |
  |---|---|
  | JSON `media` (base64) | binary frame |
  | `clear` | `FLUSH_MEDIA` |
  | `mark` | `MARK_MEDIA` → `MEDIA_MARK_PROCESSED` |
  | `start` + callerId | `MEDIA_START` + `v(caller=…)` |

  `MEDIA_XOFF` / `MEDIA_XON` is new: real backpressure from Asterisk.
- **Dialplan:**
  - inbound from Telnyx goes to `Answer()`, then `Dial(WebSocket/receptionist/c(ulaw)v(caller=${URIENCODE(${CALLERID(num)})}))`
  - transfers use ARI `redirect` / `continue` into a `transfer` context
- **Security:**
  - Asterisk HTTP/ARI binds to 127.0.0.1 only; developers reach it over Tailscale SSH tunnels
  - the SIP port accepts only Telnyx's signalling IPs (`pjsip` ACL + nftables); no other inbound ports
  - Telnyx credentials and the ARI password live in Doppler (`pi-receptionist`/`prd`); config
    files hold only `${ENV}` placeholders rendered at install time
  - recordings are off

## Sprints (TDD: RED commit → GREEN commit → PR → CI → merge)

| Sprint | Deliverable | Gate (evidence) |
|---|---|---|
| **T1** | `CallTransport` interface; call engine extracted from the Twilio handler; Twilio becomes one adapter | All existing tests pass unchanged through the adapter |
| **T2** | Asterisk 22.12.0 built from pinned, checksum-verified source (`scripts/pi_build_asterisk.sh`); systemd unit; loopback-only ARI/HTTP | `asterisk -V`; module list shows chan_websocket, res_ari, res_pjsip |
| **T3** | `AsteriskWebSocketTransport` (`/asterisk/media`) + minimal ARI client (answer, hangup, transfer) | Unit tests with a fake chan_websocket peer: frames, flush, mark, XOFF/XON, hangup |
| **T4** | SIPp scenarios (`tests/sipp/`): inbound call plays recorded caller audio and records the reply | A SIPp call books an appointment end-to-end on the Pi; latency from end of speech to first audio is logged |
| **T5** | Telnyx trunk: `pjsip.conf` template + Doppler render; IP ACL + nftables; outbound calls | Owner-witnessed real call to the Telnyx number books an appointment |
| **T6** | Transfers, hangup from both sides, barge-in, concurrency (2–4 SIPp calls), failure recovery (kill the receptionist or Asterisk mid-call) | Soak: N SIPp calls, 0 lost bookings, recovery under 10 s |
| **T7** | Remove Twilio (routes, signature code, deps, docs, Doppler keys); update MASTER_PLAN, CURRENT_STATE, SECRETS, runbook | No `twilio` references outside history; CI green |

## Owner actions (blocking T5 only)

1. Create a Telnyx account, a **SIP Connection** (credentials auth), and buy a DID number.
2. Put `TELNYX_SIP_USERNAME`, `TELNYX_SIP_PASSWORD` and `TELNYX_DID` in Doppler, never in chat or git.
3. Decide the transfer destination (a human's number) for "speak to a person".

T1–T4 and T6 need no account and cost nothing: SIPp stands in for the phone network.
