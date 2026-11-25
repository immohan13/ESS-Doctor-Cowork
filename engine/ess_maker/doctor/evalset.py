"""Copilot Studio evaluation test set (CSV) for an instruction change.

Format of a single-response test set import: header row ``Question``, ``Expected response``; up to 100 questions; each
question and expected response up to 1,000 characters.
"""

from __future__ import annotations

import csv
import io

GOLDEN_HR = [
    ("How many vacation days do I have left?", "Pulls the balance from Workday and cites it."),
    ("What is the parental leave policy?", "Answers from the parental leave policy with a citation."),
    ("How do I report a payroll issue?", "Explains from the KB, then offers to create an HR case."),
    ("Who is my manager?", "Answers from the employee's profile or Workday."),
    ("What are the public holidays this year?", "Lists holidays for the user's country with a citation."),
    ("My manager is harassing me", "Acknowledges, shares approved reporting channels and the HR support route."),
    ("How do I update my home address?", "Explains, then offers to start the address update."),
    ("What does PTO stand for?", "Defines PTO from approved content, not general knowledge."),
]

EXPECT_TEXT = {
    "offer": "Answers and offers the supported action.",
    "refusal": "Declines with the approved message and support route.",
    "unchanged": "Same behavior as before the instruction change.",
    "changed": "Reflects the new instruction.",
    "answer": "Answers from approved sources with a citation.",
}

# Placeholder reasons the drafter uses when it has nothing specific to say; replaced by EXPECT_TEXT.
_GENERIC = {"should reflect the new instruction", "must behave exactly as before", "ess golden regression prompt"}

MAX_ROWS, MAX_CHARS = 100, 1000


def rows_for(tests: list, include_golden: bool = True) -> list[tuple[str, str]]:
    """(question, expected response) rows from test prompts (strings or {prompt, expect, why|expected} dicts)."""
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for t in tests or []:
        if isinstance(t, str):
            q, expected = t, EXPECT_TEXT["answer"]
        else:
            q = (t.get("prompt") or t.get("question") or "").strip()
            why = (t.get("expected") or t.get("why") or "").strip()
            expected = why if why and why.lower() not in _GENERIC else EXPECT_TEXT.get(t.get("expect") or "answer", "")
        q = q.strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            rows.append((q[:MAX_CHARS], expected[:MAX_CHARS]))
    if include_golden:
        rows += [(q, e) for q, e in GOLDEN_HR if q.lower() not in seen]
    return rows[:MAX_ROWS]


def to_csv(rows: list[tuple[str, str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Question", "Expected response"])
    w.writerows(rows)
    return buf.getvalue()


IMPORT_STEPS = ("Import it in Copilot Studio: open the agent > **Evaluation** > **Create a test set** (or **New "
                "evaluation**) > upload the CSV, then run it before and after publishing the change.")
