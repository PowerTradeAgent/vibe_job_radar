from __future__ import annotations

import re
from dataclasses import dataclass
from .models import JobRecord, Requirement
from .utils import digest


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    text: str
    line: int
    section: str
    vibe_section: bool


def spans(text: str):
    section = ""
    vibe_section = False
    pattern = r"[^\n。！？；;]+(?:[。！？；;]|(?=\n|$))"
    # Split a comma only where a new obligation/negation begins, not arbitrary noun lists.
    contrast = re.compile(r"[，,](?=\s*(?:但(?:是)?|然而|不过|同时|且|并)?\s*(?:必须|需要|应当|禁止|不得|严禁|不能|不允许|不要求|不必|不强制|无需))")
    for match in re.finditer(pattern, text):
        bounds = [0, *(m.end() for m in contrast.finditer(match.group())), len(match.group())]
        for lo, hi in zip(bounds, bounds[1:]):
            raw = match.group()[lo:hi]
            left = len(raw) - len(raw.lstrip())
            prefix = re.match(r"(?:[-*•·]\s*|[（(]?\d{1,2}[）)、.)]\s*)", raw[left:])
            if prefix:
                left += len(prefix.group())
            right = len(raw.rstrip())
            start, end = match.start() + lo + left, match.start() + lo + right
            quote = text[start:end]
            if not quote:
                continue
            heading = quote.rstrip("：:。 ")
            if len(heading) <= 30 and re.fullmatch(r"(?:岗位职责|工作职责|职位描述|任职要求|岗位要求|基本要求|加分项|优先条件|福利待遇|公司介绍|招聘要求|AI\s*编程要求|Vibe\s*Coding要求)", heading, re.I):
                section = heading
                vibe_section = bool(re.search(r"AI|Vibe", heading, re.I))
                continue
            yield Span(start, end, quote, text.count("\n", 0, start), section, vibe_section)


def strength(text: str, section: str = "") -> str:
    if re.search(r"禁止|严禁|不得|不允许|不能(?:使用|上传|发送)|must not|do not (?:use|upload|send)", text, re.I):
        return "prohibited"
    if re.search(r"无需|不要求|不需要|不必|不强制|not required|no .{0,40}experience.{0,20}required", text, re.I):
        return "not_required"
    if re.search(r"优先|加分|有更好|更佳|nice.to.have|preferred", text + section, re.I):
        return "preferred"
    if re.search(r"必须|必备|要求|至少|精通|熟练|具备|能够|独立|熟悉|掌握|\bmust\b|\brequired\b", text, re.I):
        return "required"
    if re.search(r"负责|使用|应用|推动|参与|建立|构建|维护|设计|完成|开发", text):
        return "expected"
    return "unspecified"


class RuleExtractor:
    version = "rules-0.1.0"

    def __init__(self, config: dict):
        self.config = config
        self.direct_patterns = [re.compile(p, re.I) for p in config["direct_patterns"]]
        self.tool_patterns = {name: re.compile(p, re.I) for name, p in config["tools"].items()}
        self.cap_patterns = {name: [re.compile(p, re.I) for p in cap["patterns"]]
                             for name, cap in config["capabilities"].items()}

    def tools_in(self, text: str) -> list[str]:
        out = []
        for name, pattern in self.tool_patterns.items():
            matches = list(pattern.finditer(text))
            if not matches:
                continue
            if name == "Cursor":
                matches = [m for m in matches if not re.match(r"\s*\.(?:execute|fetch|close|rowcount)", text[m.end():], re.I)]
                if re.search(r"数据库游标|SQL\s*(?:数据库)?游标|database\s+cursor", text, re.I):
                    matches = []
            if name == "GitHub Copilot" and not re.search(r"github|代码|编程|编码|开发", text, re.I) and re.search(r"office|ppt|excel|word|办公", text, re.I):
                matches = []
            if matches:
                out.append(name)
        return out

    def direct(self, text: str) -> bool:
        return bool(self.tools_in(text) or any(p.search(text) for p in self.direct_patterns))

    def extract(self, job: JobRecord, roles: list[str], *, group_id: str | None = None) -> list[Requirement]:
        units = list(spans(job.text))
        anchors = [s for s in units if self.direct(s.text) and strength(s.text, s.section) not in {"not_required", "prohibited"}
                   and s.section not in {"福利待遇", "公司介绍"}]
        group_id = group_id or "g_" + job.fingerprint[:24]
        rows = []
        for s in units:
            if s.section in {"福利待遇", "公司介绍"}:
                continue
            tools = self.tools_in(s.text)
            is_direct = self.direct(s.text)
            nearby = next((a for a in anchors if a.line == s.line and a != s and abs(a.start - s.start) <= 250), None)
            if is_direct:
                relation, score = "direct", 0.95
            elif s.vibe_section or nearby:
                relation, score = "contextual", 0.80
            elif anchors:
                relation, score = "role_related", 0.55
            else:
                continue
            capabilities = {k for k, ps in self.cap_patterns.items() if any(p.search(s.text) for p in ps)}
            if is_direct:
                capabilities.add("ai_coding")
            if tools:
                capabilities.add("tool_fluency")
            if not capabilities:
                continue
            priority = strength(s.text, s.section)
            mixed = bool(re.search(r"(?:无需|不要求|禁止|不得).{0,100}(?:但|同时|不过).{0,100}(?:必须|要求|熟练|需要)", s.text))
            ambiguous = bool(re.search(r"不接受只会|不能只|不依赖|不能依赖", s.text))
            for cap in sorted(capabilities):
                rid = "r_" + digest(group_id + f"|{s.start}|{s.end}|{cap}")[:24]
                review = "needs_review" if job.evidence_level == "snippet" or mixed or ambiguous or relation != "direct" else "rule_accepted"
                notes = []
                if relation == "role_related":
                    notes.append("same-JD supporting capability; NOT evidence that this is a Vibe Coding requirement")
                if mixed or ambiguous:
                    notes.append("mixed/ambiguous polarity; human review required")
                if job.evidence_level == "snippet":
                    notes.append("search snippet; excluded from full-text statistics even if approved")
                rows.append(Requirement(
                    requirement_id=rid, record_id=job.record_id, job_group_id=group_id, capability=cap,
                    quote=s.text, start=s.start, end=s.end, relation=relation, strength=priority,
                    tools=tools, rule_score=score, extraction_method=self.version, review_status=review,
                    roles=roles, evidence_level=job.evidence_level, is_synthetic=job.is_synthetic,
                    platform=job.platform, title=job.title, company=job.company, url=job.url,
                    source_record_ids=[job.record_id], source_urls=[job.url] if job.url else [],
                    context_quote=nearby.text if nearby else (s.section if s.vibe_section else ""), notes="; ".join(notes)))
        return rows


def hard_constraints(job: JobRecord, group_id: str, roles: list[str]) -> list[dict]:
    patterns = {
        "education": r"本科|硕士|博士|学士|大专|学历|学位",
        "experience": r"\d+\s*[-~至到]?\s*\d*\s*年.{0,10}经验|经验.{0,10}\d+\s*年",
        "work_mode": r"驻场|到岗|坐班|远程|出差|工作地点",
        "credential": r"资格证|资格认证|职称|\bPMP\b",
    }
    rows = []
    for s in spans(job.text):
        for category, pattern in patterns.items():
            if re.search(pattern, s.text, re.I):
                rows.append({"constraint_id": "h_" + digest(group_id + str(s.start) + category)[:20],
                             "job_group_id": group_id, "record_id": job.record_id, "roles": roles,
                             "category": category, "quote": s.text, "start": s.start, "end": s.end,
                             "strength": strength(s.text, s.section), "url": job.url,
                             "evidence_level": job.evidence_level, "is_synthetic": job.is_synthetic,
                             "note": "Do not infer satisfaction from polished wording; verify separately."})
    return rows


def apply_reviews(requirements: list[Requirement], reviews: dict) -> list[str]:
    unknown = sorted(set(reviews) - {r.requirement_id for r in requirements})
    for r in requirements:
        item = reviews.get(r.requirement_id)
        if item is None or (isinstance(item, dict) and item.get("decision") == "pending"):
            continue
        if not isinstance(item, dict) or item.get("decision") not in {"approve", "reject"} or not item.get("reviewer") or not item.get("reason"):
            raise ValueError(f"review {r.requirement_id} needs decision, reviewer, reason")
        r.review_status = "approved" if item["decision"] == "approve" else "rejected"
        r.notes += " | human review: " + str(item["reason"])
    return unknown
