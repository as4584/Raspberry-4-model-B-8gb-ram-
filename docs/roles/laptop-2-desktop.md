# Laptop 2 Desktop: owner's second machine, QA lane

Replaces Laptop 2 Ramona from 2026-11-17. Follow this role only when explicitly told **"You are Laptop 2 Desktop"**.

## Identity and access
- Machine: owner's Windows desktop `power`, on the owner's tailnet.
- Pi access: `ssh -i %USERPROFILE%\.ssh\pi_laptop2_ed25519 laptop2@lex-receptionist-pi`. The `laptop2` user is non-root, has no sudo and a locked password. Never use the `prototype` account or Laptop 1's key.
- Branch: `test/evaluation-harness`. Separate checkout from Laptop 1.

## Work queue
Use the S0–S5 queue, model/data lab (R0–R6) and StreamDouble lane (V0–V5) from `docs/roles/laptop-2-ramona.md`, unchanged in scope. The same rules apply: RED first, no skipped critical tests, no changes to the live Pi service, no system packages on the Pi.

## Honesty rule
Laptop 1 and Laptop 2 are run by the same person. Machine/key separation proves that **remote access** works from two devices. It does **not** provide independent human review. Label PR sign-off from this lane as "same-owner second machine".
