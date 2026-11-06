"""Load the run configuration.

The real configuration holds children's names and schools, so it is read from the
DIGEST_CONFIG environment variable (a GitHub secret). A local file can be used for
development with DIGEST_CONFIG_FILE.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

WORK_DIR = Path(os.environ.get("DIGEST_WORK_DIR", "work"))


@dataclass
class Child:
    name: str
    aliases: list[str] = field(default_factory=list)
    school: str = ""
    grade: str = ""
    group: str = ""


@dataclass
class Config:
    recipient: str
    timezone: str
    calendar_id: str
    lookahead_days: int
    children: list[Child]
    senders: list[str]
    keywords: list[str]
    recent_days: int
    older_days: int
    follow_domains: list[str]
    links_per_email: int
    links_total: int

    @property
    def child_names(self) -> list[str]:
        return [c.name for c in self.children]


def _load_raw() -> dict:
    text = os.environ.get("DIGEST_CONFIG")
    if not text:
        path = os.environ.get("DIGEST_CONFIG_FILE", "config.yaml")
        if not Path(path).exists():
            raise SystemExit(
                "No configuration found. Set the DIGEST_CONFIG secret "
                "(see config.example.yaml)."
            )
        text = Path(path).read_text()
    return yaml.safe_load(text) or {}


def load_config() -> Config:
    raw = _load_raw()
    gmail = raw.get("gmail", {}) or {}
    links = raw.get("links", {}) or {}
    children = [
        Child(
            name=str(c["name"]),
            aliases=[str(a) for a in c.get("aliases", []) or []],
            school=str(c.get("school", "")),
            grade=str(c.get("grade", "")),
            group=str(c.get("group", "")),
        )
        for c in raw.get("children", []) or []
    ]
    if not raw.get("recipient"):
        raise SystemExit("Configuration is missing 'recipient'.")
    if not children:
        raise SystemExit("Configuration lists no children.")
    return Config(
        recipient=str(raw["recipient"]),
        timezone=str(raw.get("timezone", "America/New_York")),
        calendar_id=str(raw.get("calendar_id", "primary")),
        lookahead_days=int(raw.get("lookahead_days", 21)),
        children=children,
        senders=[str(s) for s in gmail.get("senders", []) or []],
        keywords=[str(k) for k in gmail.get("keywords", []) or []],
        recent_days=int(gmail.get("recent_days", 8)),
        older_days=int(gmail.get("older_days", 30)),
        follow_domains=[str(d).lower() for d in links.get("follow_domains", []) or []],
        links_per_email=int(links.get("max_per_email", 5)),
        links_total=int(links.get("max_total", 25)),
    )


def is_dry_run() -> bool:
    return os.environ.get("DRY_RUN", "false").strip().lower() in {"1", "true", "yes"}
