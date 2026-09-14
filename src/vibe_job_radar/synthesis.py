from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit
from .models import Requirement
from .metrics import format_metric, validate_metric
from .utils import load_json, parse_time

SCOPES = {"synthetic": "合成演示", "offline": "离线验证", "shadow": "影子验证", "production": "生产观察"}


def load_candidate(path: str | Path | None, config: dict) -> dict:
    if not path:
        return {"name": "待填写", "evidence": []}
    path = Path(path)
    data = load_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("evidence", []), list):
        raise ValueError("candidate must be an object containing an evidence list")
    seen = set()
    for e in data.get("evidence", []):
        if not isinstance(e, dict) or not e.get("evidence_id") or e["evidence_id"] in seen:
            raise ValueError("candidate evidence needs unique evidence_id")
        seen.add(e["evidence_id"])
        if e.get("review_status", "draft") not in {"draft", "approved"} or e.get("scope") not in SCOPES:
            raise ValueError("invalid candidate review_status/scope")
        if not isinstance(e.get("capabilities", []), list) or set(e.get("capabilities", [])) - set(config["capabilities"]):
            raise ValueError("unknown candidate capabilities")
        if not isinstance(e.get("requirement_ids", []), list) or not all(isinstance(x, str) for x in e.get("requirement_ids", [])):
            raise ValueError("requirement_ids must be a list of exact IDs")
        e["metrics"] = [validate_metric(m) for m in e.get("metrics", [])]
        e["integrity_status"] = "not_checked"
        if e.get("review_status") == "approved":
            for key in ("project", "evidence_ref", "reviewer", "reviewed_at"):
                if not e.get(key):
                    raise ValueError(f"approved evidence requires {key}")
            parse_time(e["reviewed_at"])
            ref = e["evidence_ref"]
            if urlsplit(ref).scheme in {"https", "http"}:
                e["integrity_status"] = "external_reference_not_fetched"
            else:
                evidence_file = (path.parent / ref).resolve()
                # An explicit candidate file can reference any local evidence file; never execute it.
                if not evidence_file.is_file():
                    raise ValueError(f"evidence file missing: {ref}")
                h = hashlib.sha256()
                with evidence_file.open("rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(chunk)
                expected = e.get("sha256")
                if not expected or expected != h.hexdigest():
                    raise ValueError(f"approved local evidence requires a matching sha256: {ref}")
                e["integrity_status"] = "file_hash_verified_content_not_audited"
    return data


def aggregate(requirements: list[Requirement], config: dict) -> list[dict]:
    accepted = [r for r in requirements if r.accepted and r.positive]
    scopes = ["all", *config["roles"]]
    rows = []
    for scope in scopes:
        scoped = [r for r in accepted if scope == "all" or scope in r.roles]
        jobs = {r.job_group_id for r in scoped}
        denominator = len(jobs)
        for cap, definition in config["capabilities"].items():
            matching = [r for r in scoped if r.capability == cap]
            cap_jobs = {r.job_group_id for r in matching}
            if not cap_jobs:
                continue
            ratio = len(cap_jobs) / denominator if denominator else 0.0
            rows.append({"scope": scope, "capability": cap, "label": definition["label"],
                         "job_count": len(cap_jobs), "denominator_jobs": denominator, "sample_frequency": ratio,
                         "employer_count_known": len({r.company for r in matching if r.company}),
                         "required_job_count": len({r.job_group_id for r in matching if r.strength == "required"}),
                         "preferred_job_count": len({r.job_group_id for r in matching if r.strength == "preferred"}),
                         "bucket": "intersection" if ratio == 1 else ("common" if ratio >= config["common_threshold"] else "specialized"),
                         "requirement_ids": sorted({r.requirement_id for r in matching}),
                         "source_urls": sorted({u for r in matching for u in r.source_urls})})
    return sorted(rows, key=lambda x: (x["scope"], -x["job_count"], x["capability"]))


def evidence_matrix(requirements: list[Requirement], candidate: dict, config: dict, *, demo_mode: bool) -> list[dict]:
    approved = [e for e in candidate.get("evidence", []) if e.get("review_status") == "approved"
                and (demo_mode or e["scope"] != "synthetic")]
    rows = []
    for r in requirements:
        exact = [e for e in approved if r.requirement_id in e.get("requirement_ids", [])
                 and r.capability in e.get("capabilities", [])]
        potential = [e for e in approved if r.capability in e.get("capabilities", [])]
        scope_mismatch = bool(exact and re.search(r"生产环境|生产系统|生产级|线上|上线", r.quote)
                              and not any(e["scope"] == "production" for e in exact))
        if scope_mismatch:
            exact = []
        if not r.positive:
            status = "constraint_review"
        elif not r.accepted:
            status = "requirement_review"
        elif scope_mismatch:
            status = "scope_mismatch_needs_review"
        elif exact:
            status = "user_attested_exact"
        elif potential:
            status = "capability_only_not_satisfied"
        else:
            status = "evidence_missing"
        rows.append({"requirement_id": r.requirement_id, "job_group_id": r.job_group_id,
                     "title": r.title, "capability": r.capability, "quote": r.quote, "url": r.url,
                     "strength": r.strength, "status": status,
                     "weight": config["weights"].get(r.strength, 1) if r.accepted and r.positive else 0,
                     "evidence_ids": [e["evidence_id"] for e in exact],
                     "potential_evidence_ids": [e["evidence_id"] for e in potential],
                     "note": "Exact means a human-declared requirement-to-evidence mapping, NOT verified competence or hiring probability."})
    return rows


def job_coverage(matrix: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in matrix:
        groups[row["job_group_id"]].append(row)
    rows = []
    for group, items in groups.items():
        total = sum(x["weight"] for x in items)
        covered = sum(x["weight"] for x in items if x["status"] == "user_attested_exact")
        rows.append({"job_group_id": group, "title": items[0]["title"], "url": items[0]["url"],
                     "eligible_weight": total, "user_attested_weight": covered,
                     "evidence_mapping_coverage": covered / total if total else None,
                     "constraint_or_review_rows": sum(x["weight"] == 0 for x in items),
                     "note": "Only mapping completeness within accepted AI-coding requirements; excludes hard qualifications. Not a fit score."})
    return rows


def descriptions(summary: list[dict], requirements: list[Requirement], matrix: list[dict], candidate: dict,
                 config: dict, *, demo_mode: bool) -> tuple[str, str, str]:
    banner = "合成样例演示：不是实际职位调研或个人成果" if demo_mode else "基于当前采集样本；不是全市场普查"
    header = f"# 可举证的岗位描述\n\n> {banner}。没有证据的内容只能作为待填写模板。\n\n"
    text = header + "## 表述框架\n\n场景与范围 → 自己承担的动作 → 工程控制机制 → 同口径指标 → 可追溯证据。\n\n"
    text += "原文要求、岗位硬条件与限制条款保留在 CSV 中；以下措辞不能代替真实经历，也不保证完全胜任。\n\n"
    global_rows = [r for r in summary if r["scope"] == "all"]
    common = [r for r in global_rows if r["bucket"] in {"intersection", "common"}]
    text += "## 通用底座：待填模板\n\n"
    if common:
        wording = "；".join(config["capabilities"][r["capability"]]["description"] for r in common)
        text += f"在【项目/业务场景及本人职责】中，{wording}。在【同口径观察窗口】下，以【基线→当前值、样本量、质量约束】记录结果，并提供【提交/测试/发布/监控证据】。\n\n"
    else:
        text += "尚无足够的已接收正文证据，未生成通用能力主张。先补正文或处理 review_queue。\n\n"
    text += "## 样本要求并集：逐项措辞\n\n"
    for row in global_rows:
        cap = config["capabilities"][row["capability"]]
        text += f"### {cap['label']}\n\n【待举证】{cap['description']}。量化字段：" + "、".join(cap["metric_ids"]) + ".\n\n"
        text += "对应要求：" + ", ".join(row["requirement_ids"]) + "。\n\n"
    text += "## 已经人工确认的候选人证据\n\n"
    evidence = [e for e in candidate.get("evidence", []) if e.get("review_status") == "approved"
                and (demo_mode or e["scope"] != "synthetic")]
    if not evidence:
        text += "尚未提供经人工确认的个人证据；本次不生成带真实成果数字的个人履历。\n\n"
    for e in evidence:
        text += f"### {e['project']} / {SCOPES[e['scope']]}\n\n"
        text += f"来源：{e['evidence_ref']}；人工确认人：{e['reviewer']}；证据状态：{e['integrity_status']}。\n\n"
        for m in e.get("metrics", []):
            text += format_metric(m) + "。\n\n"
        text += "这些是输入方确认的观测值；文件哈希校验仅证明文件一致性，不证明内容真实，也不证明变化由 AI 单独导致。\n\n"
    role_text = "# 分岗位描述模板\n\n> " + banner + "；所有【】字段须填写真实信息。\n\n"
    for key, role in config["roles"].items():
        rows = [r for r in summary if r["scope"] == key]
        role_text += f"## {role['label']}\n\n"
        if not rows:
            role_text += "当前没有该岗位的已接收正文要求；不自动借用其他岗位的要求。\n\n"
            continue
        role_text += "在【领域/系统/预测任务】中，" + "；".join(config["capabilities"][r["capability"]]["description"] for r in rows) + "。\n\n"
        role_text += "证据句：在【样本/负载/业务时域】与【观察窗口】下，【指标】从【基线】变化为【当前值】；样本量【N】，验证阶段【离线/影子/生产】，证据【路径或链接】。\n\n"
        role_text += "岗位特有工具、年限、学历及禁止项须逐条对照 requirements.csv 和 hard_constraints.csv，不可被通用段落替代。\n\n"
    missing = [x for x in matrix if x["status"] != "user_attested_exact"]
    gaps = "# 证据与适配缺口\n\n" + f"{banner}。本次共有 {len(missing)} 条映射仍须补证或复核。\n\n"
    gaps += "capability_only_not_satisfied 仅表示能力类别相近，不能据此宣称满足特定工具、经验或成果要求。\n\n"
    for x in missing:
        gaps += f"- {x['requirement_id']} / {x['title']} / {x['capability']}：{x['status']}。\n"
    return text, role_text, gaps
