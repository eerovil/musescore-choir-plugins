#!/usr/bin/env python3
"""
Terminal adapter for per-system assignment.

One of the two ways a human names each staff's voices per printed system (the other
is the song app's assignment grid). It only renders the layout `per_system` hands it
and returns the answers `per_system` rebuilds from — it knows nothing about systems,
declarations, or the score itself.
"""

from __future__ import annotations

import sys
from typing import Callable, Dict, List, Optional

from .per_system import CLEARED, Answers, SystemLayout


def prompt_for_answers(
    layouts: List[SystemLayout],
    ask: Optional[Callable[[str], str]] = None,
    out=None,
) -> Answers:
    """Ask, system by system, what each staff holds. Returns the answers.

    Each staff's default (shown in [brackets], reused by pressing Enter) is the answer
    recorded for that cell, or failing that the answer just given for the same staff in
    an earlier system — layouts usually change at only a few systems. '-' says the staff
    holds nothing from here on.
    """
    ask = ask or input
    out = out or sys.stderr
    answers: Answers = {}
    last_answer: Dict[int, str] = {}  # per staff id; Enter reuses it
    print(
        "\nPer-system re-voicing: for each system, name each staff's voices "
        "(comma per voice).\n"
        "   Enter reuses the previous answer (shown in [brackets]); "
        "'-' clears/skips a staff.",
        file=out,
    )
    for layout in layouts:
        print(f"\n— System {layout.index + 1}: measures {layout.start}-{layout.end} —", file=out)
        for row in layout.staves:
            print(f"   staff {row.staff_id}: {row.voices} voice(s) — {row.summary}", file=out)
        for row in layout.staves:
            default = row.answer or last_answer.get(row.staff_id, "")
            hint = f" [{default}]" if default else ""
            raw = ask(f"   staff {row.staff_id} ({row.voices} voice(s)){hint} > ").strip()
            if raw == "":
                chosen = default          # reuse default for this staff
            elif raw == CLEARED:
                chosen = CLEARED          # explicit skip / clear
            else:
                chosen = raw
            last_answer[row.staff_id] = chosen
            answers.setdefault(layout.index, {})[row.staff_id] = chosen
    return answers
