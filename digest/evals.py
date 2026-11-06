"""Evaluation log: one row of counts per run, committed to the repo.

Only aggregate numbers are recorded, never names, dates or email content, so the
log is safe in a public repository. Over time it shows:
  - evidence_verified / evidence_checked: how often the model's quoted date
    wording is really in the source (a hallucination check)
  - items_with_issues and items_dropped_unsourced: how often output fails checks
  - unreadable: how much content the agent still cannot open
  - corrections_applied: how often the parent had to correct a digest (misses)
"""

from __future__ import annotations

import csv
from pathlib import Path

FIELDS = [
    "run_date", "mode",
    "emails", "older_emails", "attachments_read", "attachments_unreadable",
    "links_read", "links_unreadable", "feedback_replies",
    "items_proposed", "items_dropped_unsourced", "items_merged", "items_final",
    "items_with_issues", "evidence_checked", "evidence_verified", "evidence_from_image",
    "model_flags", "unreadable", "corrections_applied",
    "calendar_added", "calendar_skipped_existing",
]


def append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in FIELDS})
