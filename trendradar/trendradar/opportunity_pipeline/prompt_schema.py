# coding=utf-8
"""
机会识别流程 - 提示词输出结构定义与归一化工具

这个模块的职责不是调用 AI，而是：
1. 规定 AI 结果最少应该包含哪些字段；
2. 将 AI 返回的弱类型 JSON 做一次“保守归一化”；
3. 避免因为字段缺失或格式漂移导致后续推送误触发。

设计原则：
- 宁可保守地判定为“无机会”，也不要因为解析漂移导致误推。
"""

from typing import Any, Dict, List


EXPECTED_SCHEMA = {
    "has_opportunity": False,
    "opportunity_type": "",
    "opportunity_summary": "",
    "opportunity_reason": "",
    "risk_level": "high",
    "confidence": 0.0,
    "actionable_targets": [],
    "suggested_action": "",
}

EXPECTED_SIGNAL_SCHEMA = {
    "title": "",
    "opportunity_type": "",
    "opportunity_summary": "",
    "opportunity_reason": "",
    "risk_level": "high",
    "confidence": 0.0,
    "actionable_targets": [],
    "suggested_action": "",
    "related_titles": [],
}

EXPECTED_FETCH_TARGET_SCHEMA = {
    "title": "",
    "fetch_priority": 0.0,
    "content_type": "",
    "information_density": "",
    "reason": "",
}


def build_default_payload() -> Dict[str, Any]:
    """
    构造一份默认 payload。

    后续解析失败、字段缺失或格式异常时，都可以回退到这一份默认值，
    从而确保上层门控逻辑有稳定输入。
    """
    return dict(EXPECTED_SCHEMA)


def build_default_signal_payload() -> Dict[str, Any]:
    """构造单条机会信号的默认结构。"""
    return dict(EXPECTED_SIGNAL_SCHEMA)


def build_default_fetch_target_payload() -> Dict[str, Any]:
    """构造标题级正文抓取候选的默认结构。"""
    return dict(EXPECTED_FETCH_TARGET_SCHEMA)


def normalize_risk_level(value: str) -> str:
    """归一化风险等级，只允许 low / medium / high。"""
    normalized = str(value or "").strip().lower()
    if normalized in {"low", "medium", "high"}:
        return normalized
    return "high"


def normalize_confidence(value: Any) -> float:
    """
    将置信度归一化到 0~1。

    任何非法值都会被保守处理为 0.0，避免误推。
    """
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0

    if result < 0:
        return 0.0
    if result > 1:
        return 1.0
    return result


def normalize_targets(value: Any) -> List[str]:
    """
    归一化可执行目标列表。

    允许 AI 返回 list，也允许返回逗号分隔字符串。
    """
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]

    return []


def normalize_string_list(value: Any) -> List[str]:
    """把字符串列表统一成干净的字符串数组。"""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def normalize_density(value: Any) -> str:
    """归一化信息密度级别。"""
    normalized = str(value or "").strip().lower()
    if normalized in {"high", "medium", "low"}:
        return normalized
    return "low"


def parse_assessment_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    将原始 payload 解析为统一结构。

    这里返回的是普通字典，便于在更高层组装成 dataclass。
    """
    normalized = build_default_payload()
    payload = payload or {}

    normalized["has_opportunity"] = bool(payload.get("has_opportunity", False))
    normalized["opportunity_type"] = str(payload.get("opportunity_type", "")).strip()
    normalized["opportunity_summary"] = str(payload.get("opportunity_summary", "")).strip()
    normalized["opportunity_reason"] = str(payload.get("opportunity_reason", "")).strip()
    normalized["risk_level"] = normalize_risk_level(payload.get("risk_level", "high"))
    normalized["confidence"] = normalize_confidence(payload.get("confidence", 0.0))
    normalized["actionable_targets"] = normalize_targets(payload.get("actionable_targets", []))
    normalized["suggested_action"] = str(payload.get("suggested_action", "")).strip()
    return normalized


def parse_signal_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """将单条机会信号的原始 payload 归一化。"""
    normalized = build_default_signal_payload()
    payload = payload or {}

    normalized["title"] = str(payload.get("title", "")).strip()
    normalized["opportunity_type"] = str(payload.get("opportunity_type", "")).strip()
    normalized["opportunity_summary"] = str(payload.get("opportunity_summary", "")).strip()
    normalized["opportunity_reason"] = str(payload.get("opportunity_reason", "")).strip()
    normalized["risk_level"] = normalize_risk_level(payload.get("risk_level", "high"))
    normalized["confidence"] = normalize_confidence(payload.get("confidence", 0.0))
    normalized["actionable_targets"] = normalize_targets(payload.get("actionable_targets", []))
    normalized["suggested_action"] = str(payload.get("suggested_action", "")).strip()
    normalized["related_titles"] = normalize_string_list(payload.get("related_titles", []))
    return normalized


def parse_title_filter_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """解析第一阶段标题级筛选输出。"""
    payload = payload or {}
    fetch_targets = []
    raw_targets = payload.get("top_articles_to_fetch", [])

    if isinstance(raw_targets, list):
        for item in raw_targets:
            if not isinstance(item, dict):
                continue
            normalized = build_default_fetch_target_payload()
            normalized["title"] = str(item.get("title", "")).strip()
            normalized["fetch_priority"] = normalize_confidence(item.get("fetch_priority", 0.0))
            normalized["content_type"] = str(item.get("content_type", "")).strip()
            normalized["information_density"] = normalize_density(item.get("information_density", "low"))
            normalized["reason"] = str(item.get("reason", "")).strip()
            if normalized["title"]:
                fetch_targets.append(normalized)

    return {
        "should_fetch_any": bool(payload.get("should_fetch_any", bool(fetch_targets))),
        "selection_reason": str(payload.get("selection_reason", "")).strip(),
        "fetch_targets": fetch_targets,
    }
