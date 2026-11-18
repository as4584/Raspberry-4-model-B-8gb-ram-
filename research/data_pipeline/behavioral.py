"""R2 behavioral testing — CheckList MFT / INV / DIR. Ramona (Laptop 2).

Behavioral testing in the sense of Ribeiro et al., ACL 2020
(https://aclanthology.org/2020.acl-main.442/). Three test types, each a
capability-level contract on the NLU layer:

  MFT  Minimum Functionality — a clean canonical utterance must produce the
       known command/action (one per capability / slot type).
  INV  Invariance — a label-PRESERVING perturbation (filler, punctuation,
       casing, connector, code-switch) must NOT change the booking. This is the
       same guarantee our augmentation relies on, so it doubles as the
       augmentation safety net.
  DIR  Directional Expectation — a perturbation that CHANGES a slot (date /
       time / duration) MUST change the output in the known direction. This
       proves the parser actually reads the slot rather than keying on surface
       cues — the property compositional generalization depends on.

The runner grades any `normalize_fn` by driving it through the real Dialogue,
so results reflect end-to-end behavior, not just string shuffling.
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field

from receptionist.booking import BookingStore
from receptionist.dialogue import Dialogue

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}")


def _signature(normalize_fn, utterance: str) -> tuple[str, str | None]:
    """End-to-end (action, booked_slot) signature of an utterance."""
    command = normalize_fn(utterance)
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = f.name
    d = Dialogue(BookingStore(db_path), timezone_name="America/New_York")
    reply = d.handle(command, "behavioral")
    m = _ISO.search(command)
    return reply.action, (m.group(0) if m else None)


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

@dataclass
class BehavioralResult:
    mft_total: int = 0
    mft_pass: int = 0
    inv_total: int = 0
    inv_pass: int = 0
    dir_total: int = 0
    dir_pass: int = 0
    failures: list = field(default_factory=list)

    @property
    def all_pass(self) -> bool:
        return (self.mft_pass == self.mft_total
                and self.inv_pass == self.inv_total
                and self.dir_pass == self.dir_total)

    def as_dict(self) -> dict:
        return {
            "mft": f"{self.mft_pass}/{self.mft_total}",
            "inv": f"{self.inv_pass}/{self.inv_total}",
            "dir": f"{self.dir_pass}/{self.dir_total}",
            "all_pass": self.all_pass,
            "failures": self.failures,
        }


# MFT: (capability, utterance, expected_action, expected_slot)
_MFT = [
    ("book_en", "book 2027-12-10 14:00", "confirm_required", "2027-12-10 14:00"),
    ("book_es", "reservar 2028-01-05 09:30", "confirm_required", "2028-01-05 09:30"),
    ("book_12h", "book 2027-12-10 at 2pm", "confirm_required", "2027-12-10 14:00"),
    ("affirm", "yes", "reply", None),
    ("deny", "no", "reply", None),
    ("handoff", "operator", "handoff", None),
    ("help", "what are your hours", "help", None),
    ("cancel", "cancel 00000000-0000-0000-0000-000000000000", "not_found", None),
]

# Label-preserving perturbation operators (INV).
def _inv_ops_booking(s: str) -> list[tuple[str, str]]:
    date_time = _ISO.search(s)
    ops = [
        ("trailing_punct", s + "."),
        ("filler", "um, so " + s),
        ("casing", s.upper()),
        ("please", s + " please"),
    ]
    if date_time:
        d, t = date_time.group(0).split()
        ops.append(("connector_at", s.replace(f"{d} {t}", f"{d} at {t}")))
        ops.append(("code_switch", s.replace(f"{d} {t}", f"{d} a las {t}")))
    return ops


def _inv_ops_keyword(s: str) -> list[tuple[str, str]]:
    return [
        ("trailing_punct", s + "!"),
        ("casing", s.upper()),
    ]


_INV_BOOKING_SEEDS = [
    "book 2027-12-10 14:00",
    "reservar 2028-01-05 09:30",
]
_INV_KEYWORD_SEEDS = ["yes", "no", "operator", "help"]

# DIR: a booking seed, then slot edits with the expected new slot.
_DIR_SEEDS = [
    ("book 2027-12-10 14:00", "2027-12-10 14:00",
     [("book 2027-12-11 14:00", "2027-12-11 14:00"),     # move date
      ("book 2027-12-10 09:00", "2027-12-10 09:00"),     # move time
      ("book 2028-01-05 16:00", "2028-01-05 16:00")]),   # move both
    ("reservar 2028-01-05 09:30", "2028-01-05 09:30",
     [("reservar 2028-01-06 09:30", "2028-01-06 09:30"),
      ("reservar 2028-01-05 11:00", "2028-01-05 11:00")]),
]


def run_behavioral(normalize_fn) -> BehavioralResult:
    r = BehavioralResult()

    # MFT
    for cap, utt, exp_action, exp_slot in _MFT:
        r.mft_total += 1
        action, slot = _signature(normalize_fn, utt)
        ok = action == exp_action and (exp_slot is None or slot == exp_slot)
        if ok:
            r.mft_pass += 1
        else:
            r.failures.append(f"MFT[{cap}] {utt!r} -> ({action},{slot}) "
                              f"want ({exp_action},{exp_slot})")

    # INV — output must match the seed's signature
    for seed in _INV_BOOKING_SEEDS:
        base = _signature(normalize_fn, seed)
        for name, variant in _inv_ops_booking(seed):
            r.inv_total += 1
            sig = _signature(normalize_fn, variant)
            if sig == base:
                r.inv_pass += 1
            else:
                r.failures.append(f"INV[{name}] {variant!r} -> {sig} != seed {base}")
    for seed in _INV_KEYWORD_SEEDS:
        base = _signature(normalize_fn, seed)
        for name, variant in _inv_ops_keyword(seed):
            r.inv_total += 1
            sig = _signature(normalize_fn, variant)
            if sig == base:
                r.inv_pass += 1
            else:
                r.failures.append(f"INV[{name}] {variant!r} -> {sig} != seed {base}")

    # DIR — changing the slot must change the booked slot to the new value
    for seed, seed_slot, edits in _DIR_SEEDS:
        for variant, new_slot in edits:
            r.dir_total += 1
            action, slot = _signature(normalize_fn, variant)
            if action == "confirm_required" and slot == new_slot and slot != seed_slot:
                r.dir_pass += 1
            else:
                r.failures.append(f"DIR {variant!r} -> ({action},{slot}) "
                                  f"want confirm @ {new_slot} (!= {seed_slot})")

    return r
