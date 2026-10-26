"""Shared data model. Every scraper returns list[Job]."""
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value):
    """Turn '2026-10-08', '2026-06-23 16:48:41' or an ISO string into an
    aware UTC datetime. Naive values are assumed to be UTC. Returns None if
    the value is empty or cannot be parsed."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def posted_ago(dt, now=None):
    """'Today', '3 days ago', '2 weeks ago', '5 months ago'."""
    if dt is None:
        return None
    days = ((now or utcnow()) - dt).days
    if days < 1:
        return "Today"
    if days < 7:
        return f"{days} day{'s' if days > 1 else ''} ago"
    if days < 30:
        w = days // 7
        return f"{w} week{'s' if w > 1 else ''} ago"
    if days < 365:
        m = days // 30
        return f"{m} month{'s' if m > 1 else ''} ago"
    y = days // 365
    return f"{y} year{'s' if y > 1 else ''} ago"


@dataclass
class Job:
    source: str                      # "itpro" | "linkedin" | "rooster" | "rootcode"
    source_id: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    link: str | None = None
    description: str | None = None
    posted_at: datetime | None = None    # UTC, None if the source gives no date
    scraped_at: datetime = field(default_factory=utcnow)

    @property
    def key(self) -> str:
        """Unique across sources, e.g. 'linkedin:4447346769'."""
        return f"{self.source}:{self.source_id}"

    @classmethod
    def from_dict(cls, source, d):
        """Build from the dict format used by the itpro / linkedin scrapers."""
        return cls(
            source=source,
            source_id=str(d["id"]),
            title=d.get("title"),
            company=d.get("company"),
            location=d.get("location"),
            link=d.get("link"),
            description=d.get("description"),
            posted_at=parse_dt(d.get("datetime")),
        )

    def to_dict(self):
        """The JSON shape used so far (fields A-Z) plus 'source'."""
        return {
            "company": self.company,
            "datetime": self.posted_at.date().isoformat() if self.posted_at else None,
            "description": self.description,
            "id": self.source_id,
            "link": self.link,
            "location": self.location,
            "posted_ago": posted_ago(self.posted_at),
            "source": self.source,
            "title": self.title,
        }
