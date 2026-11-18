# Stable MVP interfaces — contract version 0.3

- `BookingStore(path).book(resource, starts: datetime, ends: datetime, idem_key: str, caller_id: str | None = None) -> dict`
- `BookingStore.cancel(reference: str, caller_id: str | None = None) -> bool`; `active() -> list[dict]`; `reschedule(..., caller_id=None)`
- `BookingStore.reschedule(reference, starts, ends, idem_key) -> dict` (added 2026-11-17, owner-approved; Ramona has left). One transaction: the old row becomes `status='rescheduled'` and the new active row is inserted, or nothing changes. Raises `SlotTaken` (the original stays active), `InvalidBooking` (unknown or inactive reference), `KeyConflict` (key reused with a different target). Replay with the same key returns the same row.
- `Dialogue.handle(text, session_id)`: pending confirmation and turn counter are **per session_id**. One shared `Dialogue` may serve many calls without cross-call leakage.
- `Dialogue(store, timezone_name='America/New_York', hours=DEFAULT_HOURS, durations=(15,30,60), default_duration=30, require_caller_id=False).handle(text: str, session_id: str, caller_id: str | None = None) -> Reply(text,action,committed_id)`
- **v0.3 (2026-11-17, S2 / issue #8):**
  - Business hours: `hours` maps local weekday (Mon=0) to `(open, close)` `datetime.time`; default Mon-Fri 09:00-17:00 in `timezone_name`. A slot must start at/after open and end at/before close on the same local day, else action `closed` with the opening hours in the reply. A duration not in `durations` gives action `invalid`. Durations are real elapsed minutes (end computed in UTC, so DST-safe).
  - Reschedule: `reschedule <ref> YYYY-MM-DD HH:MM [dur]` gives `confirm_required`; `yes` calls `BookingStore.reschedule` and returns action `rescheduled` with the new reference; `no` replies "not changed". Unknown or foreign reference gives `not_found`.
  - Caller verification: bookings store `caller_id` (the caller's phone number, e.g. Twilio `From`). `cancel`/`reschedule` match only when the supplied `caller_id` equals the stored one (SQL `IS`, so legacy rows with NULL owner only match a NULL caller). A mismatch is indistinguishable from an unknown reference (`False`, `InvalidBooking("No active booking with that reference")`, action `not_found`). The rescheduled row inherits the owner. With `require_caller_id=True`, `book`/`cancel`/`reschedule` without a caller_id give action `verification_required`. Production must set `require_caller_id=True` and pass the caller number; wiring Twilio `From` into `/twilio/media` is still open.
  - Migration: opening a `BookingStore` on an existing DB adds `bookings.caller_id TEXT` via `ALTER TABLE ... ADD COLUMN` if missing (idempotent; existing rows keep NULL).
  - Dialogue idempotency key is `session:turn:<op>@<utc start>-<utc end>` (op = `book` or the reschedule reference): an identical retry returns the original row, while a rebuilt session (restart or server LRU eviction) reusing a turn number for a different request books normally instead of failing with KeyConflict.
- `create_app(Settings(...), asr_factory: Callable[[], ASR], tts: TTS) -> FastAPI`
- `ASR.transcribe(mulaw: bytes) -> awaitable[str]`
- `TTS.synthesize(text: str) -> awaitable[bytes]` emits raw 8k μ-law.
- `/simulate/text` accepts `{session_id,text}` and is available only in dev mode.
- `/twilio/voice` POST signed form webhook, returns TwiML `<Connect><Stream>`.
- `/twilio/media` WS receives JSON connected/start/media/mark/stop; sends media/mark/clear.
- Booking text baseline `book YYYY-MM-DD HH:MM [15|30|60]`, `reschedule UUID YYYY-MM-DD HH:MM [15|30|60]`, `yes`, `no`, `cancel UUID`.
- Auth: since v0.3 cancel/reschedule require the booking's `caller_id` in addition to the reference. Caller ID can be spoofed; stronger verification (e.g. SMS code) remains a pre-production decision.
- `DEV_ALLOW_UNSIGNED=1` is isolated dev only, NEVER public production.
- Changes require contract test update + PR review by both accounts.

## v0.4 (2026-11-18): spoken booking code (#38)
- `book(..., caller_id=X)` rows carry `code`: 4 random digits, unique among caller X's **active** bookings, chosen inside the write transaction. Anonymous bookings get `code=None`.
- `reschedule` keeps the code on the new row.
- `BookingStore.cancel_by_code(code, caller_id) -> bool`: requires the matching caller number; the code alone never authorises.
- Dialogue: the booked reply speaks "Your booking code is four seven two nine." "cancel <code>" / "cancel booking <code>" (digits or digit words) without a caller number returns `verification_required`. UUID cancel is unchanged.
- Migration: existing DB files gain the `code` column in place (NULL for old rows).
