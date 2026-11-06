"""Step 3: verify the extracted items, update the calendar, email the digest, log metrics.

In a test run (DRY_RUN=true) nothing is added to the calendar, the email subject
starts with "[Test run]", and no evaluation row is written.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

from . import calendar_sync, evals
from .config import WORK_DIR, is_dry_run, load_config
from .gather import week_monday
from .google_auth import calendar_service, credentials, gmail_service
from .render import render
from .verify import calendar_ready, verify

EVAL_LOG = Path("evals/runs.csv")


def load_sources(work: Path) -> tuple[dict, dict]:
    manifest = json.loads((work / "manifest.json").read_text())
    sources = {s["id"]: s for s in manifest["sources"]}
    texts = {}
    for sid, s in sources.items():
        p = Path(s["path"])
        raw = p.read_text() if p.exists() else ""
        texts[sid] = raw.split("\n---\n", 1)[1] if "\n---\n" in raw else raw
    return sources, texts


def send_email(svc, to: str, subject: str, plain: str, html_body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["From"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()


def main() -> None:
    cfg = load_config()
    dry = is_dry_run()
    now = datetime.now(ZoneInfo(cfg.timezone))
    today = now.date()
    monday = week_monday(now).date()
    creds = credentials()
    gmail = gmail_service(creds)

    extracted_path = WORK_DIR / "extracted.json"
    try:
        extracted = json.loads(extracted_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        send_email(
            gmail, cfg.recipient,
            f"{'[Test run] ' if dry else ''}School digest: this week's run failed",
            "The extraction step did not produce a readable result, so no digest was built.\n"
            "Check the latest run under the repository's Actions tab.",
            "<p>The extraction step did not produce a readable result, so no digest was built. "
            "Check the latest run under the repository's <b>Actions</b> tab.</p>",
        )
        raise SystemExit(f"extracted.json missing or invalid ({type(exc).__name__})")

    sources, texts = load_sources(WORK_DIR)
    res = verify(extracted, sources, texts, cfg.child_names, today)

    horizon = monday + timedelta(days=cfg.lookahead_days - 1)
    to_add = [i for i in res.items if calendar_ready(i) and today <= datetime.fromisoformat(i["date"]).date() <= horizon]
    cal = calendar_sync.sync(calendar_service(creds), to_add, cfg.calendar_id, cfg.timezone, sources, dry)

    subject, plain, html_body = render(res, sources, monday, cal["added"])
    if dry:
        subject = "[Test run] " + subject
        plain = "Test run: nothing was added to your calendar.\n\n" + plain
        html_body = "<p><i>Test run: nothing was added to your calendar.</i></p>" + html_body
    send_email(gmail, cfg.recipient, subject, plain, html_body)

    stats_path = WORK_DIR / "gather_stats.json"
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}
    row = {"run_date": today.isoformat(), "mode": "test" if dry else "weekly", **stats, **res.metrics,
           "calendar_added": len(cal["added"]), "calendar_skipped_existing": cal["skipped_existing"]}
    if not dry:
        evals.append_row(EVAL_LOG, row)
    print("Digest sent.", ", ".join(f"{k}={v}" for k, v in row.items() if k not in {"run_date"}))


if __name__ == "__main__":
    main()
