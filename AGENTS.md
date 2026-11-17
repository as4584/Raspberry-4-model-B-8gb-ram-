# Agents: Start here — two-laptop Raspberry Pi receptionist project

You are working in the **Raspberry Pi 4 Model B 8 GB Local AI Receptionist** repository. **The repository is the canonical, executable source of truth.** The Google Doc is the research/long-form specification: https://docs.google.com/document/d/1pw6pMB9rPBjglflAK-l1i4pcpTMXr65xeBihhxzsJ6M/edit

## ZERO-AMBIGUITY BOOT SEQUENCE — every new agent/session
1. Ask for or use the user's explicit designation **"Laptop 1 Alex"** or **"Laptop 2 Ramona"**. **Do not infer the identity from OS username, machine hostname, or signed-in account.** If unspecified, ask which of the two you are.
2. Read, in this order: `AGENTS.md`, `docs/MASTER_PLAN.md`, `docs/CURRENT_STATE.md`, `docs/contracts.md`, `docs/PI_TAILSCALE_RUNBOOK.md`, then only your own role file:
   - Laptop 1 Alex → `docs/roles/laptop-1-alex.md`
   - Laptop 2 Ramona → `docs/roles/laptop-2-ramona.md`
3. Confirm the role, branch, repo remote, latest upstream SHA, current sprint/gate and next single testable task. Read the relevant Google Doc tabs if your account has access. A pasted Docs URL does **not** establish agent access; the in-repo specification works offline.
4. Run `git status --short` first. **Do not discard or overwrite uncommitted work.** Fetch `origin` and check out the role branch:
   - Alex: `feat/receptionist-core`
   - Ramona: `test/evaluation-harness`
   Use `git switch --track origin/<branch>` if branch exists remotely but not locally.
5. Before doing new work: sync your branch with updated `origin/main` using a **non-destructive, deliberate merge or rebase**, after checking for changes. Resolve conflicts; never force-push shared work.
6. Perform the **next unlocked task in your role plan** using RED → GREEN → REFACTOR. Put a reproducing failing test in the appropriate PR or show the precise test failing first. Run existing tests; do not skip/mislabel failures.
7. End with: role, branch+SHA, sprint, exact tests/results, Pi-vs-laptop evidence, PR URL, blockers, next action. Update `docs/CURRENT_STATE.md` in a reviewed PR; do **not** silently mark a gate passed.

## Team ownership
**Laptop 1 Alex = implementation owner.** Owns `receptionist/`, production config, deployment, Pi runtime, Twilio integration, live audio, local LLM and booking behavior. Never use Ramona's GitHub login or AI account.
**Laptop 2 Ramona = independent QA owner.** Owns `tests/`, `fixtures/`, evaluation datasets, adversarial tests, CI gates and benchmark harness; may access Pi for authorized, bounded testing but must not change the live service or install system packages independently.
**Same repo, different branches; separate PRs; review each other's changes.** Before changing a shared interface, create a contract-change proposal and update `docs/contracts.md` with both owners' approval.
**No agent may auto-merge a safety-critical PR or quietly change test expectations to green.**

## TDD lock policy
A sprint is **LOCKED** unless its own acceptance criteria are proven and dependencies passed. Always attach: initial failing test/reproduction, final command with output, CI run link when available, linked commits, reproducible fixture and measured raw data when claiming performance.
Never treat Python mocks, laptop timings, simulator audio, or a status claim as proof that a Raspberry Pi or live Twilio works. Existing 9 local tests are a prototype baseline, not full-system verification.
Mandatory invariants: no false booking confirmation; no double-booking; no cross-call state/audio; no unauthorized cancellation; no disabled authentication on any public ingress; no secrets committed.
If a task needs a person (Pi boot, auth browser, Twilio number, ACL authorization), stop only that task, report the exact action, and advance independent tests.

## Safety boundaries
Never paste or commit a Twilio auth token, Tailscale auth key, voice recording, customer phone number, Google credential, or private key. Keep `.env` out of git. Do not run arbitrary downloaded shell scripts; consult the official Tailscale package instructions and inspect changes. Avoid sudo/firmware/overclock/production deploy without Alex's explicit approval. NEVER expose `DEV_ALLOW_UNSIGNED=1` on a public IP/WSS endpoint. Use loopback + SSH port forwarding for developer calls.
Pi target: **Raspberry Pi 4 Model B, 8 GB, 64-bit Linux**; verified metrics must actually run on this unit. Working connection over Tailscale is a prerequisite for remote testing, not evidence of inference speed. Twilio uses **one bidirectional WebSocket per call**, plus two distinct sockets to test simultaneous callers.


## Required MODEL/DATA research stream (Laptop 2 Ramona)
Ramona is ALSO the primary model-discovery, data-curation and **sample-efficiency experiment** agent, not only QA. BEFORE selecting a small model or training any model read `docs/MODEL_DATA_LAB.md`; pursue its R0-R6 experiments in parallel with Alex's S1 Pi/Tailscale setup. Use `docs/models/registry.csv` and `docs/data/sources.csv` for provenance and legal reuse gates. Research compares diverse, difficult, dependable samples against a controlled random-data baseline—do not claim proprietary Arlequin AI architecture was reproduced. Ramona gets QA/tests and data-lab tasks; Alex remains owner of Pi/LLM integration, validates the choice on real Pi and holds the **sealed final evaluation** outside training agent access. `docs/MASTER_PLAN.md` and role files give the full order.
