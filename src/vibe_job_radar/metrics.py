from __future__ import annotations

import math
from typing import Any

# These are a proposed measurement dictionary, NOT observed recruiting-market thresholds.
CATALOG = {
    "cycle_time_hours": ("可比任务交付周期", "小时", "lower", "(基线周期-当前周期)/基线周期×100%", "同类、同范围任务，从需求确认到验收通过；基线>0"),
    "accepted_change_rate": ("生成变更验收率", "%", "rate", "验收通过的生成变更数/提交验收的生成变更数×100%", "分母>0；固定变更粒度和验收标准，不等于代码生成比例"),
    "cost_per_accepted_change": ("每项验收变更成本", "元/项", "lower", "同窗口归集成本/验收通过的变更数", "成本范围须注明是否含模型、人力、基础设施；同币种同口径"),
    "first_acceptance_rate": ("需求首次验收通过率", "%", "rate", "首次验收通过任务数/已完成首次验收任务数×100%", "固定任务范围；撤销任务单独记录"),
    "agent_task_success_rate": ("Agent任务验收成功率", "%", "rate", "满足验收且未超权限任务数/全部已评估任务数×100%", "包含失败及中断，不只统计成功调用"),
    "test_pass_rate": ("测试通过率", "%", "rate", "通过测试数/执行测试数×100%", "标注测试集版本；通过率不等于覆盖率或无缺陷保证"),
    "reproducibility_rate": ("实验可复现率", "%", "rate", "按预定义容差成功复现实验数/尝试复现实验数×100%", "固定数据版本、种子、依赖、容差与运行条件"),
    "availability_rate": ("服务可用率", "%", "rate", "符合SLO的有效事件数/全部有效事件数×100%", "必须写清SLI、窗口、排除规则和监控来源"),
    "rollback_minutes": ("回滚恢复时长", "分钟", "lower", "达到恢复验收时刻-触发回滚时刻", "标明演练/影子/生产与统计量；不能把演练当生产事故恢复"),
    "p95_latency_ms": ("P95请求延迟", "毫秒", "lower", "按约定分位数算法计算有效请求耗时P95", "相同负载、并发、硬件、数据规模与统计窗口"),
    "wape_pct": ("WAPE预测误差", "%", "lower", "Σ|真实值-预测值|/Σ|真实值|×100%", "分母为0记为不可计算；预测时点/时域/数据切分相同；不限制误差小于100%"),
    "business_constraint_pass_rate": ("业务约束检查通过率", "%", "rate", "通过业务约束检查数/已执行检查数×100%", "业务约束须版本化；不要把检查通过等同业务收益"),
    "security_check_pass_rate": ("安全检查通过率", "%", "rate", "通过安全检查项数/已执行适用检查项数×100%", "注明扫描/审查范围；不能宣称绝对安全"),
}


def finite_number(value: Any, name: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number, not a boolean")
    return float(value)


def rate(numerator: float, denominator: float) -> float | None:
    n, d = finite_number(numerator, "numerator"), finite_number(denominator, "denominator")
    if n > d:
        raise ValueError("rate numerator cannot exceed denominator")
    return n / d * 100 if d else None


def wape(actual: list[float], predicted: list[float]) -> float | None:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("actual/predicted must be nonempty and of equal length")
    if any(type(v) not in {int, float} or not math.isfinite(v) for v in [*actual, *predicted]):
        raise ValueError("WAPE inputs must be finite numbers")
    denominator = sum(abs(v) for v in actual)
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / denominator * 100 if denominator else None


def validate_metric(metric: dict) -> dict:
    key = metric.get("metric_id")
    if key not in CATALOG:
        raise ValueError(f"unknown metric_id: {key}")
    current = finite_number(metric.get("current"), "current")
    kind = CATALOG[key][2]
    if kind == "rate" and current > 100:
        raise ValueError("rate must be within 0..100")
    if type(metric.get("sample_size")) is not int or metric["sample_size"] < 1:
        raise ValueError("metric sample_size must be a positive integer")
    if not metric.get("window") or not metric.get("comparison_basis"):
        raise ValueError("metric requires window and comparison_basis")
    result = dict(metric)
    result["current"] = current
    result["relative_reduction_pct"] = None
    result["percentage_point_change"] = None
    if metric.get("baseline") is not None:
        baseline = finite_number(metric["baseline"], "baseline")
        if kind == "rate" and baseline > 100:
            raise ValueError("baseline rate must be within 0..100")
        if type(metric.get("baseline_sample_size")) is not int or metric["baseline_sample_size"] < 1 or not metric.get("baseline_window"):
            raise ValueError("baseline needs baseline_sample_size and baseline_window")
        result["baseline"] = baseline
        if kind == "rate":
            result["percentage_point_change"] = current - baseline
        elif baseline > 0:
            result["relative_reduction_pct"] = (baseline - current) / baseline * 100
    return result


def format_metric(metric: dict) -> str:
    m = validate_metric(metric)
    label, unit, kind, _, _ = CATALOG[m["metric_id"]]
    value = f"{m['current']:g}{unit}"
    if m.get("baseline") is not None:
        value = f"{m['baseline']:g}{unit} → " + value
        delta = m["percentage_point_change"] if kind == "rate" else m["relative_reduction_pct"]
        if delta is None:
            value += "（基线为0，不计算相对变化）"
        elif kind == "rate":
            value += f"（变化{delta:+.2f}个百分点）"
        elif delta >= 0:
            value += f"（相对下降{delta:.2f}%）"
        else:
            value += f"（相对上升{-delta:.2f}%）"
    return f"{label}：{value}；当前样本量{m['sample_size']}；窗口{m['window']}；口径：{m['comparison_basis']}"


def catalog_rows() -> list[dict]:
    return [{"metric_id": key, "label": v[0], "unit": v[1], "kind": v[2], "formula": v[3], "measurement_contract": v[4]}
            for key, v in CATALOG.items()]
