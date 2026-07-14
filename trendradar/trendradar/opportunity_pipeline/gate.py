# coding=utf-8
"""
机会识别流程 - 推送门控
这层负责把“AI 整体判断 + 多条机会信号”转成是否允许推送的明确决策。
"""

from typing import Dict, Iterable, List

from trendradar.opportunity_pipeline.models import (
    OpportunityAnalysisResult,
    OpportunityPushDecision,
    OpportunitySignal,
)


def evaluate_push_gate(
    result: OpportunityAnalysisResult,
    gate_config: Dict,
) -> OpportunityPushDecision:
    """根据机会识别结果，筛出真正允许推送的具体机会。"""
    if not gate_config.get("ENABLED", False):
        return OpportunityPushDecision(
            should_push=False,
            reason="opportunity_pipeline.enabled=false，新流程门控未启用",
            fallback_to_legacy=True,
        )

    if not result.success:
        on_ai_failure = str(gate_config.get("ON_AI_FAILURE", "skip") or "skip").strip().lower()
        if on_ai_failure == "fallback_legacy":
            return OpportunityPushDecision(
                should_push=False,
                reason=f"AI 分析失败，按 on_ai_failure={on_ai_failure} 回退到 legacy 流程：{result.error}",
                fallback_to_legacy=True,
            )
        return OpportunityPushDecision(
            should_push=False,
            reason=f"AI 分析失败，按 on_ai_failure={on_ai_failure} 跳过推送：{result.error}",
            fallback_to_legacy=False,
        )

    if not result.assessment.has_opportunity and not result.signals:
        if gate_config.get("PUSH_WHEN_NO_OPPORTUNITY", False):
            return OpportunityPushDecision(
                should_push=True,
                reason="AI 认为本轮没有明确机会，但配置要求仍然推送分析结果",
                fallback_to_legacy=False,
                selected_signals=[],
            )
        return OpportunityPushDecision(
            should_push=False,
            reason="AI 已完成分析，但本轮未发现值得推送的具体机会",
            fallback_to_legacy=False,
        )

    allowed_risk_levels = _normalize_allowed_risk_levels(
        gate_config.get("ALLOWED_RISK_LEVELS", ["low", "medium"])
    )
    min_confidence = _safe_float(gate_config.get("MIN_CONFIDENCE", 0.75), default=0.75)
    max_push_opportunities = int(gate_config.get("MAX_PUSH_OPPORTUNITIES", 3) or 3)
    require_targets = bool(gate_config.get("REQUIRE_ACTIONABLE_TARGETS", True))
    require_action = bool(gate_config.get("REQUIRE_SUGGESTED_ACTION", True))

    selected_signals: List[OpportunitySignal] = []
    rejected_reasons: List[str] = []

    ordered_signals = sorted(
        result.signals,
        key=lambda signal: signal.confidence,
        reverse=True,
    )

    for signal in ordered_signals:
        signal_reason = _validate_signal(
            signal=signal,
            min_confidence=min_confidence,
            allowed_risk_levels=allowed_risk_levels,
            require_targets=require_targets,
            require_action=require_action,
        )
        if signal_reason is None:
            selected_signals.append(signal)
            if len(selected_signals) >= max_push_opportunities:
                break
        else:
            title = signal.title or signal.opportunity_summary or "未命名机会"
            rejected_reasons.append(f"{title}：{signal_reason}")

    if selected_signals:
        top_confidence = selected_signals[0].confidence
        return OpportunityPushDecision(
            should_push=True,
            reason=f"筛出 {len(selected_signals)} 条可执行机会，最高置信度 {top_confidence:.2f}",
            fallback_to_legacy=False,
            selected_signals=selected_signals,
        )

    if rejected_reasons:
        return OpportunityPushDecision(
            should_push=False,
            reason=f"AI 识别到机会，但都未通过门控：{rejected_reasons[0]}",
            fallback_to_legacy=False,
        )

    overall_confidence = result.assessment.confidence
    if result.assessment.has_opportunity:
        return OpportunityPushDecision(
            should_push=False,
            reason=f"AI 给出了整体机会判断，但没有产出可执行的单条机会信号（整体置信度 {overall_confidence:.2f}）",
            fallback_to_legacy=False,
        )

    return OpportunityPushDecision(
        should_push=False,
        reason="AI 未产出可推送的机会信号",
        fallback_to_legacy=False,
    )


def _validate_signal(
    signal: OpportunitySignal,
    min_confidence: float,
    allowed_risk_levels: set[str],
    require_targets: bool,
    require_action: bool,
) -> str | None:
    """返回 None 表示通过门控；否则返回拒绝原因。"""
    if signal.confidence < min_confidence:
        return f"置信度 {signal.confidence:.2f} 低于阈值 {min_confidence:.2f}"

    if allowed_risk_levels and signal.risk_level not in allowed_risk_levels:
        return f"风险等级 {signal.risk_level} 不在允许范围 {sorted(allowed_risk_levels)} 内"

    if require_targets and not signal.actionable_targets:
        return "缺少明确的关注对象或执行方向"

    if require_action and not signal.suggested_action.strip():
        return "缺少后续验证或执行建议"

    if not signal.opportunity_reason.strip():
        return "缺少可解释的分析逻辑"

    return None


def _safe_float(value, default: float) -> float:
    """把阈值类配置转成 float，异常时回退默认值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_allowed_risk_levels(levels: Iterable[str]) -> set[str]:
    """把允许推送的风险等级配置统一成小写集合。"""
    normalized = {
        str(level).strip().lower()
        for level in (levels or [])
        if str(level).strip()
    }
    return normalized & {"low", "medium", "high"}
