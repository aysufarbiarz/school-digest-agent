"""Step 1: collect school emails, their attachments and linked pages into work/.

Writes:
  work/manifest.json   one entry per source (email, attachment or link)
  work/sources/S*.md   readable text of each source, plus image files to look at
  work/context.md      children, dates and the reading window for the extractor
  work/feedback.md     replies to earlier digests, if any

Only counts are printed: on a public repository the Actions log is public.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from . import documents, links
from .config import WORK_DIR, Config, load_config
from .google_auth import gmail_service

MAX_RECENT = 60
MAX_OLDER = 40
MIN_INLINE_IMAGE_BYTES = 15_000  # skip logos and spacer images
MAX_BODY_CHARS = 20_000


@dataclass
class Source:
    id: str
    kind: str  # email | older_email | attachment | link
    email_id: str
    thread_id: str
    subject: str
    sender: str
    date: str
    gmail_url: str
    title: str = ""
    url: str = ""
    path: str = ""
    images: list[str] = field(default_factory=list)
    readable: bool = True
    note: str = ""
    parent: str = ""


# ---------------------------------------------------------------- queries

def _quote(term: str) -> str:
    return f'"{term}"' if " " in term else term


def build_query(cfg: Config, newer_days: int, older_days: int | None = None) -> str:
    parts = []
    if cfg.senders:
        parts.append("from:(" + " OR ".join(cfg.senders) + ")")
    names = [c.school for c in cfg.children if c.school]
    terms = cfg.keywords + names
    if terms:
        parts.append("(" + " OR ".join(_quote(t) for t in terms) + ")")
    q = "(" + " OR ".join(parts) + ")" if parts else ""
    q += f" newer_than:{newer_days}d"
    if older_days:
        q += f" older_than:{older_days}d"
    q += ' -in:sent -in:chats -subject:"School digest"'
    return q.strip()


def list_message_ids(svc, query: str, cap: int) -> list[str]:
    ids: list[str] = []
    token = None
    while len(ids) < cap:
        resp = (
            svc.users()
            .messages()
            .list(userId="me", q=query, maxResults=min(100, cap - len(ids)), pageToken=token)
            .execute()
        )
        ids += [m["id"] for m in resp.get("messages", [])]
        token = resp.get("nextPageToken")
        if not token:
            break
    return ids[:cap]


# ---------------------------------------------------------------- message parsing

def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    for a in soup.find_all("a", href=True):
        if a.get_text(strip=True) and a["href"].startswith("http"):
            a.append(f" [{a['href']}]")
    text = soup.get_text("\n")
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


@dataclass
class ParsedMessage:
    id: str
    thread_id: str
    subject: str
    sender: str
    date: str
    plain: str = ""
    html: str = ""
    attachments: list[dict] = field(default_factory=list)  # filename, mime, attachment_id, size, data


def parse_message(msg: dict) -> ParsedMessage:
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    try:
        date = parsedate_to_datetime(headers.get("date", "")).isoformat()
    except Exception:
        date = datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000).isoformat()
    pm = ParsedMessage(
        id=msg["id"],
        thread_id=msg.get("threadId", ""),
        subject=headers.get("subject", "(no subject)"),
        sender=headers.get("from", ""),
        date=date,
    )

    def walk(part: dict) -> None:
        mime = (part.get("mimeType") or "").lower()
        body = part.get("body", {}) or {}
        filename = part.get("filename") or ""
        if part.get("parts"):
            for p in part["parts"]:
                walk(p)
            return
        is_attachment = bool(filename) or (
            mime.startswith("image/") and body.get("size", 0) >= MIN_INLINE_IMAGE_BYTES
        )
        if is_attachment and (body.get("attachmentId") or body.get("data")):
            if mime.startswith("image/") and body.get("size", 0) < MIN_INLINE_IMAGE_BYTES:
                return
            pm.attachments.append(
                {
                    "filename": filename or f"inline{documents.IMAGE_TYPES.get(mime, '')}",
                    "mime": mime,
                    "attachment_id": body.get("attachmentId"),
                    "data": body.get("data"),
                    "size": body.get("size", 0),
                }
            )
        elif mime == "text/plain" and body.get("data") and not pm.plain:
            pm.plain = _b64(body["data"]).decode("utf-8", errors="replace")
        elif mime == "text/html" and body.get("data") and not pm.html:
            pm.html = _b64(body["data"]).decode("utf-8", errors="replace")

    walk(msg.get("payload", {}))
    return pm


def body_text(pm: ParsedMessage) -> str:
    # Prefer HTML converted to text: it keeps link targets, which plain parts often drop.
    text = html_to_text(pm.html) if pm.html else pm.plain
    return text[:MAX_BODY_CHARS]


def strip_quoted_reply(text: str) -> str:
    out = []
    for line in text.splitlines():
        if line.startswith(">") or re.match(r"^On .+wrote:\s*$", line):
            break
        out.append(line)
    return "\n".join(out).strip()


# ---------------------------------------------------------------- writing sources

class Collector:
    def __init__(self, cfg: Config, work: Path):
        self.cfg = cfg
        self.work = work
        self.src_dir = work / "sources"
        self.src_dir.mkdir(parents=True, exist_ok=True)
        self.sources: list[Source] = []

    def next_id(self) -> str:
        return f"S{len(self.sources) + 1}"

    def gmail_url(self, msg_id: str) -> str:
        return f"https://mail.google.com/mail/?authuser={self.cfg.recipient}#all/{msg_id}"

    def add(self, src: Source, text: str) -> Source:
        path = self.src_dir / f"{src.id}.md"
        header = [
            f"# Source {src.id} ({src.kind})",
            f"Email subject: {src.subject}",
            f"From: {src.sender}",
            f"Email date: {src.date}",
        ]
        if src.title:
            header.append(f"Title: {src.title}")
        if src.url:
            header.append(f"URL: {src.url}")
        if src.parent:
            header.append(f"Part of: {src.parent}")
        if src.note:
            header.append(f"Note: {src.note}")
        if src.images:
            header.append("Images to view (use the Read tool on each): " + ", ".join(src.images))
        if not src.readable:
            header.append("STATUS: could not be read.")
        body = "\n".join(header) + "\n\n---\n\n" + (text or "(no text)")
        path.write_text(body)
        src.path = str(path)
        self.sources.append(src)
        return src


def gather(cfg: Config, svc, work: Path, now: datetime) -> dict:
    col = Collector(cfg, work)
    stats = {"emails": 0, "older_emails": 0, "attachments_read": 0, "attachments_unreadable": 0,
             "links_read": 0, "links_unreadable": 0, "feedback_replies": 0}

    recent_ids = list_message_ids(svc, build_query(cfg, cfg.recent_days), MAX_RECENT)
    older_ids = [
        i
        for i in list_message_ids(svc, build_query(cfg, cfg.older_days, cfg.recent_days), MAX_OLDER)
        if i not in recent_ids
    ]

    pending_links: list[tuple[str, Source]] = []
    for msg_id in recent_ids:
        msg = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
        pm = parse_message(msg)
        email_src = col.add(
            Source(id=col.next_id(), kind="email", email_id=pm.id, thread_id=pm.thread_id,
                   subject=pm.subject, sender=pm.sender, date=pm.date, gmail_url=col.gmail_url(pm.id)),
            body_text(pm),
        )
        stats["emails"] += 1

        for att in pm.attachments:
            data = _b64(att["data"]) if att.get("data") else _b64(
                svc.users().messages().attachments()
                .get(userId="me", messageId=pm.id, id=att["attachment_id"]).execute()["data"]
            )
            sid = col.next_id()
            conv = documents.convert(data, att["mime"], att["filename"], col.src_dir, sid)
            col.add(
                Source(id=sid, kind="attachment", email_id=pm.id, thread_id=pm.thread_id,
                       subject=pm.subject, sender=pm.sender, date=pm.date,
                       gmail_url=email_src.gmail_url, title=att["filename"],
                       images=[str(p) for p in conv.image_paths], readable=conv.readable,
                       note=conv.note, parent=email_src.id),
                conv.text,
            )
            stats["attachments_read" if conv.readable else "attachments_unreadable"] += 1

        for url in links.extract_links(pm.html, cfg.follow_domains, cfg.links_per_email):
            if len(pending_links) < cfg.links_total and all(u != url for u, _ in pending_links):
                pending_links.append((url, email_src))

    # Open links after all emails, in one browser session.
    stems = [(f"L{i + 1}", url, parent) for i, (url, parent) in enumerate(pending_links)]
    fetched = links.fetch_links([(s, u) for s, u, _ in stems], col.src_dir)
    for stem, url, parent in stems:
        res = fetched.get(stem)
        if res is None:
            continue
        for item, is_sub in [(res, False)] + [(s, True) for s in res.subdocs]:
            col.add(
                Source(id=col.next_id(), kind="link", email_id=parent.email_id,
                       thread_id=parent.thread_id, subject=parent.subject, sender=parent.sender,
                       date=parent.date, gmail_url=parent.gmail_url,
                       title=item.title or ("document linked from page" if is_sub else ""),
                       url=item.final_url or item.url,
                       images=[str(p) for p in item.image_paths], readable=item.readable,
                       note=item.note, parent=parent.id),
                item.text,
            )
            stats["links_read" if item.readable else "links_unreadable"] += 1

    for msg_id in older_ids:
        msg = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
        pm = parse_message(msg)
        col.add(
            Source(id=col.next_id(), kind="older_email", email_id=pm.id, thread_id=pm.thread_id,
                   subject=pm.subject, sender=pm.sender, date=pm.date,
                   gmail_url=col.gmail_url(pm.id),
                   note="older email: use only for events in the coming weeks"),
            body_text(pm),
        )
        stats["older_emails"] += 1

    # Replies to earlier digests: the feedback loop.
    feedback = []
    for msg_id in list_message_ids(svc, 'in:sent subject:"School digest" newer_than:21d', 10):
        msg = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
        pm = parse_message(msg)
        reply = strip_quoted_reply(body_text(pm))
        if reply:
            feedback.append(f"## Reply sent {pm.date}\n\n{reply}")
    stats["feedback_replies"] = len(feedback)
    (work / "feedback.md").write_text(
        "# Replies to earlier digests\n\n" + ("\n\n".join(feedback) if feedback else "(none)")
    )

    write_context(cfg, work, now)
    (work / "manifest.json").write_text(
        json.dumps({"generated": now.isoformat(), "sources": [asdict(s) for s in col.sources]}, indent=2)
    )
    return stats


def week_monday(now: datetime) -> datetime:
    """The Monday of the week the digest covers: today if Monday, else the next Monday."""
    days = (7 - now.weekday()) % 7
    return (now + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)


def write_context(cfg: Config, work: Path, now: datetime) -> None:
    monday = week_monday(now)
    lines = [
        "# Run context",
        f"Today: {now.strftime('%A %Y-%m-%d')} ({cfg.timezone})",
        f"Digest week: Monday {monday:%Y-%m-%d} to Sunday {(monday + timedelta(days=6)):%Y-%m-%d}",
        f"Coming up window ends: {(monday + timedelta(days=20)):%Y-%m-%d}",
        "",
        "## Children",
    ]
    for c in cfg.children:
        bits = [f"- {c.name}"]
        if c.aliases:
            bits.append(f"(also called {', '.join(c.aliases)})")
        details = ", ".join(x for x in [c.school, c.grade, c.group] if x)
        if details:
            bits.append(f": {details}")
        lines.append(" ".join(bits))
    (work / "context.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    cfg = load_config()
    now = datetime.now(ZoneInfo(cfg.timezone))
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    stats = gather(cfg, gmail_service(), WORK_DIR, now)
    (WORK_DIR / "gather_stats.json").write_text(json.dumps(stats))
    print("Gathered:", ", ".join(f"{k}={v}" for k, v in stats.items()))


if __name__ == "__main__":
    main()
