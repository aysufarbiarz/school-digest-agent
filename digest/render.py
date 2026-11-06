"""Step 3c: turn verified items into the digest email (plain text and HTML)."""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta

from .verify import VerifyResult


def _d(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def fmt_day(d: date) -> str:
    return f"{d:%a %b} {d.day}"


def fmt_time(t: str | None) -> str:
    if not t:
        return ""
    h, m = map(int, t.split(":"))
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}" if m else f"{h12} {suffix}"


def sender_name(sender: str) -> str:
    m = re.match(r'\s*"?([^"<]+?)"?\s*<', sender or "")
    return m.group(1).strip() if m else (sender or "").strip()


class Line:
    """One item rendered in both formats."""

    def __init__(self, text: str, links: list[tuple[str, str]], warn: str = ""):
        self.text, self.links, self.warn = text, links, warn

    def plain(self) -> str:
        out = f"- {self.text}"
        if self.warn:
            out += f"\n  ! Check: {self.warn}"
        for label, url in self.links:
            out += f"\n  {label}: {url}"
        return out

    def html(self) -> str:
        links = " · ".join(f'<a href="{html.escape(u)}">{html.escape(l)}</a>' for l, u in self.links)
        warn = f'<br><span style="color:#b45309">Check: {html.escape(self.warn)}</span>' if self.warn else ""
        src = f'<br><span style="color:#6b7280;font-size:13px">{links}</span>' if links else ""
        return f"<li style=\"margin-bottom:10px\">{html.escape(self.text)}{warn}{src}</li>"


def source_links(item: dict, sources: dict) -> list[tuple[str, str]]:
    out, seen = [], set()
    for sid in item["sources"][:3]:
        s = sources[sid]
        email_date = _d(s.get("date", "")[:10])
        label = sender_name(s.get("sender", ""))
        if email_date:
            label += f", {email_date:%b} {email_date.day}"
        if s.get("kind") == "attachment" and s.get("title"):
            label += f" ({s['title']})"
        url = s.get("url") if s.get("kind") == "link" and s.get("url") else s["gmail_url"]
        if url not in seen:
            seen.add(url)
            out.append((label, url))
    return out


def item_text(item: dict, with_date: bool = True) -> str:
    parts = [f"[{item['child']}] {item['title']}"]
    d = _d(item.get("date"))
    if with_date and d:
        when = fmt_day(d)
        end = _d(item.get("end_date"))
        if end and end != d:
            when += f" to {fmt_day(end)}"
        if item.get("time"):
            when += f", {fmt_time(item['time'])}"
        parts.append(when)
    elif not with_date and item.get("time"):
        parts.append(fmt_time(item["time"]))
    return " — ".join(parts)


def line(item: dict, sources: dict, with_date: bool = True, action: bool = False) -> Line:
    text = item_text(item, with_date)
    if action and item.get("action"):
        text += f". To do: {item['action']}"
        dl = _d(item.get("deadline"))
        if dl:
            text += f" (by {fmt_day(dl)})"
    return Line(text, source_links(item, sources), "; ".join(item["issues"]))


def build_sections(res: VerifyResult, sources: dict, monday: date, added: list[dict]) -> list[tuple[str, list]]:
    week_end = monday + timedelta(days=6)
    later_end = monday + timedelta(days=20)
    items = res.items
    sections: list[tuple[str, list]] = []

    actions = sorted(
        [i for i in items if i.get("action")],
        key=lambda i: i.get("deadline") or i.get("date") or "9999",
    )
    sections.append(("Action needed", [line(i, sources, action=True) for i in actions]))

    tests = [i for i in items if i["type"] == "test"]
    sections.append(("Tests and quizzes", [line(i, sources) for i in
                                           sorted(tests, key=lambda i: (i["child"], i.get("date") or ""))]))

    this_week: list = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        todays = [i for i in items if i["type"] != "test" and _d(i.get("date")) == day]
        if todays:
            this_week.append(("day", fmt_day(day)))
            this_week += [line(i, sources, with_date=False) for i in todays]
    sections.append(("This week", this_week))

    upcoming = [i for i in items if i["type"] != "test" and _d(i.get("date"))
                and week_end < _d(i["date"]) <= later_end]
    sections.append(("Coming up", [line(i, sources) for i in upcoming]))

    undated = [i for i in items if not i.get("date") and not i.get("action")]
    sections.append(("Good to know", [line(i, sources) for i in undated]))

    sections.append(("Calendar events added", [Line(item_text(i), []) for i in added]))

    unreadable = []
    for u in res.unreadable:
        s = sources.get(u.get("source", ""))
        if not s:
            continue
        name = s.get("title") or s.get("subject", "")
        url = s.get("url") or s["gmail_url"]
        unreadable.append(Line(f"{name}: {u.get('reason', 'could not be read')}",
                               [(sender_name(s.get("sender", "")), url)]))
    sections.append(("Couldn't read: please check", unreadable))

    flags = []
    for f in res.flags:
        links = []
        for sid in f.get("sources", [])[:3]:
            if sid in sources:
                links.append((sender_name(sources[sid].get("sender", "")), sources[sid]["gmail_url"]))
        flags.append(Line(str(f.get("issue", "")), links))
    sections.append(("Flags", flags))
    return [(t, rows) for t, rows in sections if rows]


def render(res: VerifyResult, sources: dict, monday: date, added: list[dict]) -> tuple[str, str, str]:
    subject = f"School digest: week of {monday:%B} {monday.day}"
    sections = build_sections(res, sources, monday, added)
    closing = "Reply to this email with anything I missed or got wrong."

    plain, body = [], []
    if not sections:
        plain.append("No school items found for this week or the next two.")
        body.append("<p>No school items found for this week or the next two.</p>")
    for title, rows in sections:
        plain.append(title.upper())
        body.append(f'<h3 style="margin:22px 0 8px;font-size:16px">{html.escape(title)}</h3><ul style="padding-left:18px;margin:0">')
        for row in rows:
            if isinstance(row, tuple):
                plain.append(f"{row[1]}")
                body.append(f'</ul><p style="margin:12px 0 4px;font-weight:600">{html.escape(row[1])}</p><ul style="padding-left:18px;margin:0">')
            else:
                plain.append(row.plain())
                body.append(row.html())
        body.append("</ul>")
        plain.append("")
    if res.corrections:
        plain.append("Corrections from last week:")
        plain += [f"- {c}" for c in res.corrections]
        body.append('<h3 style="margin:22px 0 8px;font-size:16px">Corrections from last week</h3><ul style="padding-left:18px;margin:0">')
        body += [f"<li>{html.escape(c)}</li>" for c in res.corrections]
        body.append("</ul>")
    plain.append(closing)
    body.append(f'<p style="margin-top:24px;color:#374151">{closing}</p>')
    html_body = (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        'font-size:15px;line-height:1.45;color:#111827;max-width:640px">'
        + "".join(body) + "</div>"
    )
    return subject, "\n".join(plain), html_body
