# Stable MVP interfaces — contract version 0.1

- `BookingStore(path).book(resource, starts: datetime, ends: datetime, idem_key: str) -> dict`
- `BookingStore.cancel(reference: str) -> bool`; `active() -> list[dict]`
- `Dialogue(store).handle(text: str, session_id: str) -> Reply(text,action,committed_id)`
- `create_app(Settings(...), asr_factory: Callable[[], ASR], tts: TTS) -> FastAPI`
- `ASR.transcribe(mulaw: bytes) -> awaitable[str]`
- `TTS.synthesize(text: str) -> awaitable[bytes]` emits raw 8k μ-law.
- `/simulate/text` accepts `{session_id,text}` and is available only in dev mode.
- `/twilio/voice` POST signed form webhook, returns TwiML `<Connect><Stream>`.
- `/twilio/media` WS receives JSON connected/start/media/mark/stop; sends media/mark/clear.
- Booking text baseline `book YYYY-MM-DD HH:MM [15|30|60]`, `yes`, `no`, `cancel UUID`.
- Auth: bearer UUID reference for cancellation is a temporary MVP design; stronger caller verification is a required pre-production gate.
- `DEV_ALLOW_UNSIGNED=1` is isolated dev only, NEVER public production.
- Changes require contract test update + PR review by both accounts.
