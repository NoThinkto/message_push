# coding=utf-8
"""
机会识别流程 - 推送数据构建器骨架

职责：
1. 将原始热点资讯、RSS、AI 机会结果组织成一份新流程专用 report_data；
2. 为后续的新通知渲染器提供统一输入；
3. 避免直接污染旧流程的 `report_data` 结构。
"""

from typing import Dict, List, Optional

from trendradar.opportunity_pipeline.models import (
    OpportunityAnalysisResult,
    OpportunityPushDecision,
)


def build_opportunity_report(
    stats: List[Dict],
    failed_ids: Optional[List],
    new_titles: Optional[Dict],
    id_to_name: Optional[Dict],
    opportunity_result: OpportunityAnalysisResult,
    mode: str,
    rss_items: Optional[List[Dict]] = None,
    candidates: Optional[List[Dict]] = None,
    decision: Optional[OpportunityPushDecision] = None,
) -> Dict:
    """
    构建新流程专用报告数据。

    当前版本先把结构搭好，便于后续渲染和发送模块提前对齐。
    """
    selected_signals = list((decision.selected_signals if decision else []) or [])
    top_opportunities = [
        {
            "title": signal.title,
            "type": signal.opportunity_type,
            "summary": signal.opportunity_summary,
            "reason": signal.opportunity_reason,
            "risk_level": signal.risk_level,
            "confidence": signal.confidence,
            "targets": signal.actionable_targets,
            "suggested_action": signal.suggested_action,
            "related_titles": signal.related_titles,
        }
        for signal in selected_signals
    ]

    primary_opportunity = top_opportunities[0] if top_opportunities else {
        "title": "",
        "type": opportunity_result.assessment.opportunity_type,
        "summary": opportunity_result.assessment.opportunity_summary,
        "reason": opportunity_result.assessment.opportunity_reason,
        "risk_level": opportunity_result.assessment.risk_level,
        "confidence": opportunity_result.assessment.confidence,
        "targets": opportunity_result.assessment.actionable_targets,
        "suggested_action": opportunity_result.assessment.suggested_action,
        "related_titles": [],
    }

    return {
        "mode": mode,
        "stats": stats,
        "failed_ids": failed_ids or [],
        "new_titles": new_titles or {},
        "id_to_name": id_to_name or {},
        "rss_items": rss_items or [],
        "candidates": candidates or [],
        "opportunity": {
            "success": opportunity_result.success,
            "error": opportunity_result.error,
            "has_opportunity": opportunity_result.assessment.has_opportunity,
            "type": primary_opportunity.get("type", ""),
            "summary": primary_opportunity.get("summary", ""),
            "reason": primary_opportunity.get("reason", ""),
            "risk_level": primary_opportunity.get("risk_level", ""),
            "confidence": primary_opportunity.get("confidence", 0.0),
            "targets": primary_opportunity.get("targets", []),
            "suggested_action": primary_opportunity.get("suggested_action", ""),
            "overall_summary": opportunity_result.assessment.opportunity_summary,
            "overall_reason": opportunity_result.assessment.opportunity_reason,
            "overall_confidence": opportunity_result.assessment.confidence,
        },
        "top_opportunities": top_opportunities,
        "meta": {
            "candidate_count": len(candidates or []),
            "analyzed_news_count": opportunity_result.analyzed_news_count,
            "source_count": opportunity_result.source_count,
            "selected_opportunity_count": len(top_opportunities),
        },
    }
