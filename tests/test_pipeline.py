"""Offline tests with made-up data. No Google or Claude access needed: run `pytest`."""

import base64
from datetime import date

import pymupdf as fitz

from digest import calendar_sync, documents, gather, links, render, verify
from digest.config import Child, Config

TODAY = date(2026, 11, 8)  # a Sunday
MONDAY = date(2026, 11, 9)

CFG = Config(
    recipient="parent@example.com", timezone="America/New_York", calendar_id="primary",
    lookahead_days=21,
    children=[Child("Maya", school="Oakwood Elementary School", grade="3rd grade"),
              Child("Leo", school="Lakeview Middle School", grade="7th grade", group="Team Blue")],
    senders=["example-district.org"], keywords=["PTA", "field trip"],
    recent_days=8, older_days=30, follow_domains=["schoolmessenger.com", "example-district.org"],
    links_per_email=5, links_total=25,
)

SOURCES = {
    "S1": {"id": "S1", "kind": "email", "sender": "Ms. Park <park@example-district.org>",
           "date": "2026-11-06T09:00:00", "gmail_url": "https://mail.example/S1", "images": [],
           "readable": True, "subject": "Week ahead"},
    "S2": {"id": "S2", "kind": "attachment", "sender": "Ms. Park <park@example-district.org>",
           "date": "2026-11-06T09:00:00", "gmail_url": "https://mail.example/S1", "title": "news.jpg",
           "images": ["work/sources/S2.jpg"], "readable": True, "subject": "Week ahead"},
    "S3": {"id": "S3", "kind": "link", "sender": "District <news@example-district.org>",
           "date": "2026-11-05T09:00:00", "gmail_url": "https://mail.example/S3",
           "url": "https://schoolmessenger.com/m/abc", "images": [], "readable": False,
           "note": "page needs a sign-in", "subject": "District news"},
}
TEXTS = {
    "S1": "Reminder: the Unit 3 math test is on Thursday, November 12. "
          "Field trip permission slips are due Tuesday, November 10.",
    "S2": "",
    "S3": "",
}


def item(**kw):
    base = {"child": "Maya", "type": "event", "title": "Something", "date": None, "sources": ["S1"],
            "evidence": "", "confidence": "high"}
    base.update(kw)
    return base


# ------------------------------------------------------------------ verify

def test_evidence_found_and_not_found():
    assert verify.evidence_found("math test is on Thursday, November 12", TEXTS["S1"])
    assert not verify.evidence_found("science fair on Friday, November 13", TEXTS["S1"])


def test_unsourced_items_are_dropped_and_evidence_checked():
    extracted = {"items": [
        item(type="test", title="Unit 3 math test", date="2026-11-12",
             evidence="the Unit 3 math test is on Thursday, November 12"),
        item(title="Made-up event", date="2026-11-13", sources=["S99"]),
        item(title="Bake sale", date="2026-11-13", evidence="bake sale Friday the 13th"),
    ]}
    res = verify.verify(extracted, SOURCES, TEXTS, CFG.child_names, TODAY)
    titles = [i["title"] for i in res.items]
    assert "Made-up event" not in titles
    assert res.metrics["items_dropped_unsourced"] == 1
    test = next(i for i in res.items if i["title"] == "Unit 3 math test")
    assert test["verified"] and not test["issues"] and verify.calendar_ready(test)
    bake = next(i for i in res.items if i["title"] == "Bake sale")
    assert bake["issues"] and not verify.calendar_ready(bake)


def test_weekday_mismatch_is_flagged():
    issues = verify.date_issues(
        {"date": "2026-11-11", "evidence": "test on Thursday, November 11"}, TODAY)
    assert any("Thursday" in i for i in issues)


def test_wrong_year_is_flagged_but_school_year_is_fine():
    assert verify.date_issues({"date": "2026-11-12", "evidence": "Nov 12, 2025 assembly"}, TODAY)
    assert not verify.date_issues(
        {"date": "2027-01-15", "evidence": "the 2026-2027 calendar lists Friday, January 15"}, TODAY)


def test_duplicates_are_merged():
    extracted = {"items": [
        item(title="Picture day", date="2026-11-17", evidence="picture day"),
        item(title="Picture Day reminder", date="2026-11-17", sources=["S2"], evidence="picture day"),
    ]}
    res = verify.verify(extracted, SOURCES, TEXTS, CFG.child_names, TODAY)
    assert len(res.items) == 1 and res.items[0]["sources"] == ["S1", "S2"]


def test_image_sourced_item_can_reach_calendar():
    extracted = {"items": [item(title="Book fair", date="2026-11-16", sources=["S2"],
                                evidence="Book Fair Monday Nov 16")]}
    res = verify.verify(extracted, SOURCES, TEXTS, CFG.child_names, TODAY)
    assert res.items[0]["from_image"] and verify.calendar_ready(res.items[0])


def test_unreadable_sources_are_reported_even_if_model_forgets():
    res = verify.verify({"items": []}, SOURCES, TEXTS, CFG.child_names, TODAY)
    assert [u["source"] for u in res.unreadable] == ["S3"]


# ------------------------------------------------------------------ render

def test_render_has_sections_and_closing():
    extracted = {"items": [
        item(type="test", title="Unit 3 math test", date="2026-11-12",
             evidence="the Unit 3 math test is on Thursday, November 12"),
        item(type="deadline", title="Field trip permission slip", date="2026-11-10",
             action="Sign and return the slip", deadline="2026-11-10",
             evidence="permission slips are due Tuesday, November 10"),
    ], "flags": [{"sources": ["S1"], "issue": "two dates given"}]}
    res = verify.verify(extracted, SOURCES, TEXTS, CFG.child_names, TODAY)
    subject, plain, html_body = render.render(res, SOURCES, MONDAY, [])
    assert subject == "School digest: week of November 9"
    for heading in ["ACTION NEEDED", "TESTS AND QUIZZES", "THIS WEEK", "COULDN'T READ", "FLAGS"]:
        assert heading in plain
    assert "by Tue Nov 10" in plain
    assert plain.strip().endswith("Reply to this email with anything I missed or got wrong.")
    assert "<h3" in html_body


def test_render_empty_week():
    res = verify.VerifyResult()
    _, plain, _ = render.render(res, {}, MONDAY, [])
    assert "No school items found" in plain


# ------------------------------------------------------------------ calendar

def test_build_event_timed_and_all_day():
    timed = calendar_sync.build_event(
        {"child": "Leo", "type": "event", "title": "Concert", "date": "2026-11-19", "time": "18:30",
         "end_time": None, "end_date": None, "action": None}, "America/New_York", ["https://x"])
    assert timed["summary"] == "[Leo] Concert"
    assert timed["start"]["dateTime"].startswith("2026-11-19T18:30")
    assert calendar_sync.MARKER in timed["description"]
    closed = calendar_sync.build_event(
        {"child": "District", "type": "closure", "title": "Schools closed", "date": "2026-11-26",
         "end_date": "2026-11-27", "time": None, "action": None}, "America/New_York", [])
    assert closed["summary"].startswith("[School] ")
    assert closed["start"] == {"date": "2026-11-26"} and closed["end"] == {"date": "2026-11-28"}


def test_existing_hand_made_events_are_respected():
    it = {"child": "District", "type": "early_dismissal", "title": "Early dismissal 1:15",
          "date": "2026-11-25"}
    assert calendar_sync.already_on_calendar(it, [{"summary": "Early dismissal"}])
    it2 = {"child": "Maya", "type": "event", "title": "Pajama day", "date": "2026-11-20"}
    assert not calendar_sync.already_on_calendar(it2, [{"summary": "Dentist"}])


# ------------------------------------------------------------------ gather

def _b64(s: bytes) -> str:
    return base64.urlsafe_b64encode(s).decode().rstrip("=")


def test_parse_message_finds_body_and_attachments():
    msg = {"id": "m1", "threadId": "t1", "payload": {
        "headers": [{"name": "Subject", "value": "Newsletter"}, {"name": "From", "value": "a@b.org"},
                    {"name": "Date", "value": "Fri, 06 Nov 2026 10:00:00 -0500"}],
        "mimeType": "multipart/mixed", "parts": [
            {"mimeType": "multipart/alternative", "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(b"hello")}},
                {"mimeType": "text/html", "body": {"data": _b64(b"<p>hello <a href='https://x.org'>link</a></p>")}},
            ]},
            {"mimeType": "application/pdf", "filename": "news.pdf", "body": {"attachmentId": "A1", "size": 50000}},
            {"mimeType": "image/png", "filename": "", "body": {"attachmentId": "A2", "size": 400}},  # logo
            {"mimeType": "image/jpeg", "filename": "", "body": {"attachmentId": "A3", "size": 90000}},
        ]}}
    pm = gather.parse_message(msg)
    assert pm.subject == "Newsletter" and pm.plain == "hello"
    assert [a["attachment_id"] for a in pm.attachments] == ["A1", "A3"]
    assert "[https://x.org]" in gather.body_text(pm)


def test_build_query_and_reply_stripping():
    q = gather.build_query(CFG, 8)
    assert "from:(example-district.org)" in q and '"field trip"' in q and "newer_than:8d" in q
    assert '-subject:"School digest"' in q
    assert gather.strip_quoted_reply("You missed band.\n\nOn Sun, Nov 1 you wrote:\n> old") == "You missed band."


def test_week_monday():
    from datetime import datetime
    assert gather.week_monday(datetime(2026, 11, 8, 11)).date() == MONDAY
    assert gather.week_monday(datetime(2026, 11, 9, 11)).date() == MONDAY


def test_extract_links_filters_domains_and_junk():
    html = ("<a href='https://www.schoolmessenger.com/m/1'>View</a>"
            "<a href='https://example-district.org/unsubscribe'>Unsub</a>"
            "<a href='https://facebook.com/x'>FB</a>"
            "<a href='https://other.com/page'>Other</a>"
            "<a href='https://www.schoolmessenger.com/m/1#top'>Again</a>")
    assert links.extract_links(html, CFG.follow_domains, 5) == ["https://www.schoolmessenger.com/m/1"]


# ------------------------------------------------------------------ documents

def test_pdf_text_and_scanned_pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(72, 72, 520, 700),
                        "Book fair runs Monday November 16 through Friday November 20. " * 8)
    conv = documents.convert(doc.tobytes(), "application/pdf", "fair.pdf", tmp_path, "S1")
    assert "Book fair" in conv.text and not conv.image_paths

    blank = fitz.open()
    blank.new_page()
    conv2 = documents.convert(blank.tobytes(), "application/pdf", "scan.pdf", tmp_path, "S2")
    assert conv2.image_paths and conv2.image_paths[0].exists()

    bad = documents.convert(b"not a pdf", "application/pdf", "x.pdf", tmp_path, "S3")
    assert not bad.readable


# ------------------------------------------------------------------ end to end (fake Gmail)

class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeGmail:
    """Just enough of the Gmail API for gather(), with made-up messages."""

    def __init__(self, messages, attachments):
        self.messages_by_id = {m["id"]: m for m in messages}
        self.attachment_data = attachments

    def users(self):
        return self

    def messages(self):
        return self

    def attachments(self):
        return self

    def list(self, userId, q, maxResults, pageToken=None):
        if q.startswith("in:sent") or "older_than" in q:
            return _Exec({})
        return _Exec({"messages": [{"id": i} for i in self.messages_by_id]})

    def get(self, userId, id, format=None, messageId=None):
        if messageId:
            return _Exec({"data": self.attachment_data[id]})
        return _Exec(self.messages_by_id[id])


def test_gather_end_to_end(tmp_path):
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo

    doc = fitz.open()
    doc.new_page().insert_textbox(fitz.Rect(72, 72, 520, 700),
                                  "Made-up newsletter. Pajama day is Friday, November 20. " * 8)
    msg = {"id": "m1", "threadId": "t1", "payload": {
        "headers": [{"name": "Subject", "value": "Class newsletter"},
                    {"name": "From", "value": "Ms. Park <park@example-district.org>"},
                    {"name": "Date", "value": "Fri, 06 Nov 2026 10:00:00 -0500"}],
        "mimeType": "multipart/mixed", "parts": [
            {"mimeType": "text/html", "body": {"data": _b64(b"<p>See attached.</p>")}},
            {"mimeType": "application/pdf", "filename": "news.pdf",
             "body": {"attachmentId": "A1", "size": 60000}},
        ]}}
    svc = FakeGmail([msg], {"A1": _b64(doc.tobytes())})
    now = datetime(2026, 11, 8, 11, tzinfo=ZoneInfo("America/New_York"))
    stats = gather.gather(CFG, svc, tmp_path, now)
    assert stats["emails"] == 1 and stats["attachments_read"] == 1

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    ids = [s["id"] for s in manifest["sources"]]
    assert ids == ["S1", "S2"] and manifest["sources"][1]["parent"] == "S1"
    assert "Pajama day" in (tmp_path / "sources" / "S2.md").read_text()
    context = (tmp_path / "context.md").read_text()
    assert "Maya" in context and "2026-11-09" in context

    # Pretend Claude extracted one item, then run the verification on the real files.
    import os
    os.environ["DIGEST_WORK_DIR"] = str(tmp_path)
    from digest.send import load_sources
    sources, texts = load_sources(tmp_path)
    extracted = {"items": [item(title="Pajama day", date="2026-11-20", sources=["S2"],
                                evidence="Pajama day is Friday, November 20")]}
    res = verify.verify(extracted, sources, texts, CFG.child_names, TODAY)
    assert res.items[0]["verified"] and verify.calendar_ready(res.items[0])
    _, plain, _ = render.render(res, sources, MONDAY, res.items)
    assert "Pajama day" in plain and "news.pdf" in plain
