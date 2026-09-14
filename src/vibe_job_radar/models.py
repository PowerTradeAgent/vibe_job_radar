from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from .utils import canonical_url, compact, digest, parse_time, utc_now

LEVELS = {"full_text", "snippet"}
MODES = {"manual", "public_fetch", "search_api", "synthetic", "authorized_feed", "browser_fetch"}
STRENGTHS = {"required", "expected", "preferred", "not_required", "prohibited", "unspecified"}
RELATIONS = {"direct", "contextual", "role_related"}


@dataclass(frozen=True)
class JobRecord:
    title: str
    text: str
    platform: str = "manual"
    url: str = ""
    company: str = ""
    location: str = ""
    evidence_level: str = "full_text"
    source_mode: str = "manual"
    is_synthetic: bool = False
    collected_at: str = field(default_factory=utc_now)
    published_at: str = ""
    expires_at: str = ""
    rights_note: str = ""
    source_ref: str = ""
    parser: str = "import"
    raw_sha256: str = ""
    record_id: str = ""

    def __post_init__(self) -> None:
        for name in ("title", "text", "platform", "url", "company", "location", "evidence_level", "source_mode",
                     "collected_at", "published_at", "expires_at", "rights_note", "source_ref", "parser", "raw_sha256", "record_id"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be a string")
        if type(self.is_synthetic) is not bool:
            raise ValueError("is_synthetic must be a JSON boolean")
        if not self.title.strip() or not self.text.strip():
            raise ValueError("title and text must not be empty")
        if len(self.text) > 150_000:
            raise ValueError("JD exceeds 150000 characters; split/inspect input rather than silently truncate")
        if self.evidence_level not in LEVELS or self.source_mode not in MODES:
            raise ValueError("invalid evidence_level or source_mode")
        if (self.source_mode == "synthetic") != self.is_synthetic:
            raise ValueError("synthetic source_mode and is_synthetic must agree")
        if self.source_mode == "search_api" and self.evidence_level != "snippet":
            raise ValueError("search API results must remain snippets")
        parse_time(self.collected_at)
        if self.expires_at:
            parse_time(self.expires_at)
        object.__setattr__(self, "url", canonical_url(self.url))
        object.__setattr__(self, "text", self.text.replace("\r\n", "\n").replace("\r", "\n"))
        identity = self.url or (self.platform + "|" + self.source_ref + "|" + self.company + "|" + self.title + "|" + self.location)
        rid = "j_" + digest(identity + "|" + self.evidence_level + "|" + str(self.is_synthetic) + "|" + self.text)[:24]
        if self.record_id and self.record_id != rid:
            raise ValueError("record_id/content mismatch")
        object.__setattr__(self, "record_id", rid)

    @property
    def identity(self) -> str:
        # Separate snippet/full text and synthetic/real namespaces.
        base = self.url or "|".join((self.platform, self.source_ref, self.company, self.title, self.location))
        return digest(base + "|" + self.evidence_level + "|" + str(self.is_synthetic))

    @property
    def fingerprint(self) -> str:
        # Unknown employers are NEVER merged across unrelated URLs.
        employer = compact(self.company) if self.company.strip() else (self.url or self.identity)
        return digest("|".join((employer, compact(self.title), compact(self.location), compact(self.text),
                                self.evidence_level, str(self.is_synthetic))))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobRecord":
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown JobRecord fields: {sorted(unknown)}")
        return cls(**data)


@dataclass
class Requirement:
    requirement_id: str
    record_id: str
    job_group_id: str
    capability: str
    quote: str
    start: int
    end: int
    relation: str
    strength: str
    tools: list[str]
    rule_score: float
    extraction_method: str
    review_status: str
    roles: list[str]
    evidence_level: str
    is_synthetic: bool
    platform: str
    title: str
    company: str
    url: str
    source_record_ids: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    context_quote: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def positive(self) -> bool:
        return self.strength not in {"prohibited", "not_required"}

    @property
    def accepted(self) -> bool:
        return (self.review_status in {"rule_accepted", "approved"} and self.evidence_level == "full_text"
                and self.relation in {"direct", "contextual"})
