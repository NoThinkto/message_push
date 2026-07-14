# coding=utf-8
"""
机会识别流程包

这个包用于承载“资讯筛选 -> AI 机会识别 -> 门控 -> 机会预警推送”这条新业务链路。

设计原则：
1. 不替换旧流程；
2. 不复制底层爬虫和 sender；
3. 只在上层新增独立业务结构；
4. 所有说明尽量使用中文，便于后续维护和扩展。
"""

from .models import (
    OpportunityAnalysisResult,
    OpportunityAssessment,
    OpportunityPushDecision,
    OpportunitySignal,
    TitleFetchTarget,
    TitleFilterResult,
)
from .pipeline import OpportunityPipeline

__all__ = [
    "OpportunityAssessment",
    "OpportunitySignal",
    "OpportunityAnalysisResult",
    "OpportunityPushDecision",
    "TitleFetchTarget",
    "TitleFilterResult",
    "OpportunityPipeline",
]
