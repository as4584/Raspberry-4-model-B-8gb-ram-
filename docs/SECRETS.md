# Secrets: Doppler only

Real credentials (Twilio, later others) live **only** in Doppler project `pi-receptionist`, config `prd`. They never go into git, `.env.example`, Google Docs, chat or logs. `tests/test_no_secrets.py` scans every tracked file.

## Owner setup (once)
1. Doppler dashboard: create project `pi-receptionist`. In config `prd`, add `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and `TWILIO_PHONE_NUMBER`, copied from the Twilio Console.
2. `prd` → Access → **Generate service token**, read-only. It starts with `dp.st.prd.`.
3. From a laptop: `ssh -t prototype@lex-receptionist-pi bash /opt/pi-receptionist-kit/pi_doppler_token.sh`, then paste the token. It isn't echoed. It's saved 0600 to `~/.config/pi-receptionist/doppler.env` and verified by listing secret **names** only.

## Using secrets
`bash scripts/with_secrets.sh <command>` runs `doppler run` with the token passed only through the environment. The Doppler CLI on the Pi comes from Doppler's signed apt repo (key `…DE2A7741A397C129`, CLI v3.77.1, installed 2026-11-17 with owner approval).

## Rotation
If a value leaks: rotate it in Twilio first, update Doppler, then restart the service. Revoke a leaked service token in Doppler → Access.
