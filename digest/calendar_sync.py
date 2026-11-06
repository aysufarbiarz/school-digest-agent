"""Step 3b: add new events to Google Calendar, never touching existing ones."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .verify import normalize, title_similarity

MARKER = "Added by weekly school digest"


def event_key(item: dict) -> str:
    raw = f"{item['child']}|{item['date']}|{normalize(item['title'])}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def prefix(item: dict) -> str:
    return "[School] " if item["child"] in {"Both", "District"} else f"[{item['child']}] "


def strip_prefix(summary: str) -> str:
    return summary.split("] ", 1)[1] if summary.startswith("[") and "] " in summary else summary


def build_event(item: dict, tz: str, links: list[str]) -> dict:
    d = date.fromisoformat(item["date"])
    end_d = date.fromisoformat(item["end_date"]) if item.get("end_date") else d
    summary = prefix(item) + item["title"]
    lines = [item["title"]]
    if item.get("action"):
        lines.append(f"To do: {item['action']}")
    lines += [f"Source: {u}" for u in links]
    lines.append(MARKER)
    event = {
        "summary": summary,
        "description": "\n".join(lines),
        "extendedProperties": {"private": {"digest_key": event_key(item)}},
        "reminders": {"useDefault": True},
    }
    all_day = item["type"] == "closure" or not item.get("time") or item.get("end_date")
    if all_day:
        event["start"] = {"date": d.isoformat()}
        event["end"] = {"date": (end_d + timedelta(days=1)).isoformat()}
    else:
        zone = ZoneInfo(tz)
        h, m = map(int, item["time"].split(":"))
        start = datetime.combine(d, time(h, m), zone)
        if item.get("end_time"):
            eh, em = map(int, item["end_time"].split(":"))
            end = datetime.combine(d, time(eh, em), zone)
            if end <= start:
                end = start + timedelta(hours=1)
        else:
            end = start + timedelta(hours=1)
        event["start"] = {"dateTime": start.isoformat(), "timeZone": tz}
        event["end"] = {"dateTime": end.isoformat(), "timeZone": tz}
    return event


def already_on_calendar(item: dict, existing: list[dict]) -> bool:
    key = event_key(item)
    for ev in existing:
        if (ev.get("extendedProperties", {}).get("private", {}) or {}).get("digest_key") == key:
            return True
        if title_similarity(strip_prefix(ev.get("summary", "")), item["title"]) >= 0.4:
            return True
    # Hand-made events such as "School closed" or "Early dismissal" use different words.
    kind_words = {"closure": ["closed", "closure", "no school"],
                  "early_dismissal": ["early dismissal", "half day", "early release"]}
    for word in kind_words.get(item["type"], []):
        if any(word in normalize(ev.get("summary", "")) for ev in existing):
            return True
    return False


def events_on_day(svc, calendar_id: str, day: date, tz: str) -> list[dict]:
    zone = ZoneInfo(tz)
    start = datetime.combine(day, time(0, 0), zone)
    end = start + timedelta(days=1)
    resp = (
        svc.events()
        .list(calendarId=calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(),
              singleEvents=True, maxResults=100)
        .execute()
    )
    return resp.get("items", [])


def sync(svc, items: list[dict], calendar_id: str, tz: str, sources: dict, dry_run: bool) -> dict:
    """Returns {"added": [...], "skipped_existing": n}."""
    added, skipped = [], 0
    for item in items:
        existing = events_on_day(svc, calendar_id, date.fromisoformat(item["date"]), tz)
        if already_on_calendar(item, existing):
            skipped += 1
            continue
        links = list(dict.fromkeys(
            sources[s].get("url") or sources[s]["gmail_url"] for s in item["sources"]
        ))
        event = build_event(item, tz, links)
        if not dry_run:
            svc.events().insert(calendarId=calendar_id, body=event, sendUpdates="none").execute()
        added.append(item)
    return {"added": added, "skipped_existing": skipped}
