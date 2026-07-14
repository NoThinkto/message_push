# coding=utf-8
"""
机会识别流程 - 数据模型定义

为什么要单独建模：
1. 现有 `AIAnalysisResult` 主要服务于“五大板块热点分析”；
2. 机会识别流程强调的是“是否存在机会、原因、风险、目标、建议动作”；
3. 两套结果虽然都来自 AI，但关注点完全不同，混在一起会让数据结构越来越臃肿。

因此这里采用“独立模型”的方式，让新流程和旧流程在业务层保持解耦。
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class OpportunityAssessment:
    """
    单次机会判断结果。

    这是最核心的业务对象，用于描述“这批资讯是否形成值得推送的套利机会”。
    """

    has_opportunity: bool = False
    opportunity_type: str = ""
    opportunity_summary: str = ""
    opportunity_reason: str = ""
    risk_level: str = ""
    confidence: float = 0.0
    actionable_targets: List[str] = field(default_factory=list)
    suggested_action: str = ""


@dataclass
class TitleFetchTarget:
    """
    第一阶段标题级筛选后，值得继续抓正文的候选目标。
    这一层不做价值挖掘，只回答“值不值得为它抓正文”。
    """

    title: str = ""
    fetch_priority: float = 0.0
    content_type: str = ""
    information_density: str = ""
    reason: str = ""


@dataclass
class TitleFilterResult:
    """
    第一阶段标题级筛选结果。
    """

    success: bool = False
    error: str = ""
    raw_response: str = ""
    should_fetch_any: bool = False
    selection_reason: str = ""
    fetch_targets: List[TitleFetchTarget] = field(default_factory=list)


@dataclass
class OpportunitySignal:
    """
    单条机会信号。
    与 overall assessment 不同，这里描述的是“某一个值得跟踪或推送的具体机会”。
    """

    title: str = ""
    opportunity_type: str = ""
    opportunity_summary: str = ""
    opportunity_reason: str = ""
    risk_level: str = ""
    confidence: float = 0.0
    actionable_targets: List[str] = field(default_factory=list)
    suggested_action: str = ""
    related_titles: List[str] = field(default_factory=list)


@dataclass
class OpportunityAnalysisResult:
    """
    新流程的 AI 分析总结果。

    与旧流程的 `AIAnalysisResult` 不同，这里只关注“机会识别”这一件事。
    """

    success: bool = False
    error: str = ""
    raw_response: str = ""
    assessment: OpportunityAssessment = field(default_factory=OpportunityAssessment)
    signals: List[OpportunitySignal] = field(default_factory=list)
    analyzed_news_count: int = 0
    source_count: int = 0
    mode: str = ""


@dataclass
class OpportunityPushDecision:
    """
    推送门控决策结果。

    这个对象的作用是把“是否允许推送”和“为什么”明确表达出来，
    方便日志输出、后续调试和灰度验证。
    """

    should_push: bool = False
    reason: str = ""
    fallback_to_legacy: bool = False
    selected_signals: List[OpportunitySignal] = field(default_factory=list)
