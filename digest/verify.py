"""Step 3a: check the model's output before anything reaches the calendar or inbox.

Pure functions with no network access, so they are easy to test.

Checks:
  - every item cites sources that exist (unsourced items are dropped)
  - the quoted evidence really appears in the cited source text
  - the date is a real date, inside a sensible window
  - the weekday or year in the evidence agrees with the date
  - repeated reminders about the same event are merged
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
SPECIAL_CHILDREN = {"Both", "District"}
VALID_TYPES = {"test", "deadline", "event", "closure", "early_dismissal", "info"}


def normalize(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("’", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    text = re.sub(r"[^a-z0-9/:'\- ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split(" ") if t]


def evidence_found(evidence: str, source_text: str) -> bool:
    """True when the quoted evidence appears in the source, allowing small differences."""
    ev, src = normalize(evidence), normalize(source_text)
    if not ev or not src:
        return False
    if ev in src:
        return True
    ev_tokens = tokens(evidence)
    if len(ev_tokens) < 3:
        return False
    src_tokens = set(tokens(source_text))
    hits = sum(1 for t in ev_tokens if t in src_tokens)
    return hits / len(ev_tokens) >= 0.85


def parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def date_issues(item: dict, today: date) -> list[str]:
    issues = []
    d = parse_date(item.get("date"))
    if item.get("date") and d is None:
        return [f"date '{item.get('date')}' is not a real date"]
    if d is None:
        return issues
    if d < today - timedelta(days=14):
        issues.append(f"date {d:%b %-d, %Y} is well in the past")
    if d > today + timedelta(days=200):
        issues.append(f"date {d:%b %-d, %Y} is more than six months away")
    ev = normalize(item.get("evidence", ""))
    named_days = [w for w in WEEKDAYS if re.search(rf"\b{w}\b", ev)]
    if len(named_days) == 1 and not item.get("end_date"):
        if WEEKDAYS[d.weekday()] != named_days[0]:
            issues.append(
                f"the email says {named_days[0].title()} but {d:%b %-d} is a {d:%A}"
            )
    years = {int(y) for y in re.findall(r"\b(20\d\d)\b", ev)}
    # A school year like "2026-2027" or "2026-27" covers both calendar years.
    for start, end in re.findall(r"\b(20\d\d)\s*-\s*(20\d\d|\d\d)\b", ev):
        years |= {int(start), int(start) + 1}
    if years and d.year not in years:
        issues.append(f"the email mentions {', '.join(map(str, sorted(years)))} but the date is in {d.year}")
    return issues


def title_similarity(a: str, b: str) -> float:
    ta, tb = set(tokens(a)) - {"the", "a", "an", "and", "of", "for", "day"}, set(tokens(b)) - {
        "the", "a", "an", "and", "of", "for", "day"}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def dedupe(items: list[dict]) -> tuple[list[dict], int]:
    """Merge items for the same child and date with similar titles."""
    merged: list[dict] = []
    count = 0
    for item in items:
        match = None
        for m in merged:
            same_day = m.get("date") == item.get("date") and m.get("date")
            same_child = m.get("child") == item.get("child")
            if same_day and same_child and title_similarity(m["title"], item["title"]) >= 0.5:
                match = m
                break
        if match:
            match["sources"] = list(dict.fromkeys(match["sources"] + item["sources"]))
            match["action"] = match.get("action") or item.get("action")
            match["deadline"] = match.get("deadline") or item.get("deadline")
            match["time"] = match.get("time") or item.get("time")
            match["issues"] = list(dict.fromkeys(match["issues"] + item["issues"]))
            match["verified"] = match["verified"] or item["verified"]
            count += 1
        else:
            merged.append(item)
    return merged, count


@dataclass
class VerifyResult:
    items: list[dict] = field(default_factory=list)
    unreadable: list[dict] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def verify(extracted: dict, sources: dict[str, dict], source_texts: dict[str, str],
           child_names: list[str], today: date) -> VerifyResult:
    """sources: manifest entries keyed by id. source_texts: text of each source file."""
    res = VerifyResult()
    allowed_children = set(child_names) | SPECIAL_CHILDREN
    raw_items = extracted.get("items", []) or []
    dropped_unsourced = 0
    evidence_checked = evidence_ok = 0

    kept = []
    for raw in raw_items:
        if not isinstance(raw, dict) or not raw.get("title"):
            continue
        cited = [s for s in raw.get("sources", []) or [] if s in sources]
        if not cited:
            dropped_unsourced += 1
            continue
        item = {
            "child": raw.get("child") if raw.get("child") in allowed_children else "Both",
            "type": raw.get("type") if raw.get("type") in VALID_TYPES else "info",
            "title": str(raw["title"])[:120],
            "date": raw.get("date") if parse_date(raw.get("date")) else None,
            "end_date": raw.get("end_date") if parse_date(raw.get("end_date")) else None,
            "time": raw.get("time") if re.fullmatch(r"\d{1,2}:\d{2}", str(raw.get("time") or "")) else None,
            "end_time": raw.get("end_time") if re.fullmatch(r"\d{1,2}:\d{2}", str(raw.get("end_time") or "")) else None,
            "action": raw.get("action") or None,
            "deadline": raw.get("deadline") if parse_date(raw.get("deadline")) else None,
            "sources": cited,
            "evidence": str(raw.get("evidence") or ""),
            "confidence": raw.get("confidence") if raw.get("confidence") in {"high", "medium", "low"} else "medium",
            "issues": [],
            "verified": False,
            "from_image": False,
        }
        if raw.get("child") and raw.get("child") not in allowed_children:
            item["issues"].append(f"unknown child '{raw.get('child')}'")
        item["issues"] += date_issues({**raw, "evidence": item["evidence"]}, today)

        if item["date"] or item["deadline"]:
            evidence_checked += 1
            text = "\n".join(source_texts.get(s, "") for s in cited)
            if evidence_found(item["evidence"], text):
                item["verified"] = True
                evidence_ok += 1
            elif any(sources[s].get("images") for s in cited):
                item["from_image"] = True  # read from a picture: cannot be checked as text
            else:
                item["issues"].append("the quoted date wording was not found in the source email")
        kept.append(item)

    items, merged = dedupe(kept)
    items.sort(key=lambda i: (i.get("date") or i.get("deadline") or "9999", i["child"], i["title"]))
    res.items = items
    res.unreadable = [u for u in extracted.get("unreadable", []) or [] if isinstance(u, dict)]
    # Sources Python itself could not read, even if the model did not mention them.
    listed = {u.get("source") for u in res.unreadable}
    for sid, s in sources.items():
        if not s.get("readable", True) and sid not in listed:
            res.unreadable.append({"source": sid, "reason": s.get("note") or "could not be read"})
    res.flags = [f for f in extracted.get("flags", []) or [] if isinstance(f, dict)]
    res.corrections = [str(c) for c in extracted.get("corrections_applied", []) or []]
    res.metrics = {
        "items_proposed": len(raw_items),
        "items_dropped_unsourced": dropped_unsourced,
        "items_merged": merged,
        "items_final": len(items),
        "items_with_issues": sum(1 for i in items if i["issues"]),
        "evidence_checked": evidence_checked,
        "evidence_verified": evidence_ok,
        "evidence_from_image": sum(1 for i in items if i["from_image"]),
        "model_flags": len(res.flags),
        "unreadable": len(res.unreadable),
        "corrections_applied": len(res.corrections),
    }
    return res


def calendar_ready(item: dict) -> bool:
    """Only clean, dated items go on the calendar."""
    if not item.get("date") or item["issues"]:
        return False
    if item["type"] == "info":
        return False
    return item["verified"] or (item["from_image"] and item["confidence"] != "low")
