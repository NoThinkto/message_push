# coding=utf-8
"""
机会识别流程 - AI 分析器

这里负责把“原始候选事件”交给专用提示词，让 AI 输出结构化机会判断。
设计原则：
1. 复用现有 AIClient；
2. 输入是去重后的候选事件，而不是关键词统计结果；
3. 只返回机会判断，不混入 legacy 五段式热点分析。
"""

import json
import re
from typing import Dict, List, Optional

from trendradar.ai.client import AIClient
from trendradar.ai.prompt_loader import load_prompt_template
from trendradar.opportunity_pipeline.models import (
    OpportunityAnalysisResult,
    OpportunityAssessment,
    OpportunitySignal,
)
from trendradar.opportunity_pipeline.prompt_schema import (
    parse_assessment_payload,
    parse_signal_payload,
)


class OpportunityAnalyzer:
    """机会识别流程专用 AI 分析器。"""

    def __init__(
        self,
        ai_config: Dict,
        pipeline_config: Dict,
        get_time_func,
        prompt_file: Optional[str] = None,
        debug: bool = False,
    ):
        self.ai_config = ai_config
        self.pipeline_config = pipeline_config
        self.get_time_func = get_time_func
        self.debug = debug
        self.client = AIClient(ai_config)
        self.max_news = int(pipeline_config.get("MAX_NEWS_FOR_ANALYSIS", 80) or 80)
        self.max_evidence = int(pipeline_config.get("MAX_EVIDENCE_PER_CANDIDATE", 3) or 3)
        self.prompt_file = prompt_file or pipeline_config.get("DEEP_ANALYSIS_PROMPT_FILE", "opportunity_deep_analysis_prompt.txt")

        self.system_prompt, self.user_prompt_template = load_prompt_template(
            self.prompt_file,
            label="Opportunity",
        )

    def analyze(
        self,
        candidates: List[Dict],
        report_mode: str = "current",
        platforms: Optional[List[str]] = None,
    ) -> OpportunityAnalysisResult:
        """
        执行机会识别分析。

        success=True 只表示：
        - AI 调用成功
        - JSON 结构化解析成功

        是否存在机会，取决于 assessment.has_opportunity。
        """
        valid, error = self.client.validate_config()
        if not valid:
            return OpportunityAnalysisResult(
                success=False,
                error=error,
                mode=report_mode,
            )

        selected_candidates = list(candidates[: self.max_news])
        analyzed_news_count = len(selected_candidates)
        source_count = len(
            {
                source_name
                for item in selected_candidates
                for source_name in item.get("source_names", [])
                if source_name
            }
        )

        if analyzed_news_count == 0:
            return OpportunityAnalysisResult(
                success=False,
                error="当前没有可供机会识别分析的候选事件",
                analyzed_news_count=0,
                source_count=0,
                mode=report_mode,
            )

        prompt = self._build_prompt(
            candidates=selected_candidates,
            report_mode=report_mode,
            platforms=platforms,
        )

        if self.debug:
            print("\n" + "=" * 80)
            print("[Opportunity 调试] 发送给 AI 的提示词")
            print("=" * 80)
            if self.system_prompt:
                print("\n--- System Prompt ---")
                print(self.system_prompt)
            print("\n--- User Prompt ---")
            print(prompt)
            print("=" * 80 + "\n")

        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self.client.chat(messages)
        except Exception as exc:
            return OpportunityAnalysisResult(
                success=False,
                error=f"机会识别 AI 调用失败: {type(exc).__name__}: {exc}",
                analyzed_news_count=analyzed_news_count,
                source_count=source_count,
                mode=report_mode,
            )

        result = self._parse_response(response)
        result.analyzed_news_count = analyzed_news_count
        result.source_count = source_count
        result.mode = report_mode
        return result

    def _build_prompt(
        self,
        candidates: List[Dict],
        report_mode: str,
        platforms: Optional[List[str]] = None,
    ) -> str:
        """构造发送给 AI 的完整提示词。"""
        current_time = self.get_time_func().strftime("%Y-%m-%d %H:%M:%S")
        candidate_content = self._build_candidate_content(candidates)

        prompt_parts = [self.user_prompt_template.strip()]
        prompt_parts.append("")
        prompt_parts.append("以下是本轮需要判断的候选事件，请只基于这些信息返回 JSON：")
        prompt_parts.append(f"- 当前时间：{current_time}")
        prompt_parts.append(f"- 报告模式：{report_mode}")
        prompt_parts.append(f"- 监控平台：{', '.join(platforms or []) or '未提供'}")
        prompt_parts.append(f"- 候选事件数：{len(candidates)}")
        prompt_parts.append("- 请先给出本轮整体判断，再给出最值得推送的 1~3 条具体机会。")
        prompt_parts.append("")
        prompt_parts.append("【候选事件列表】")
        prompt_parts.append(candidate_content or "无")
        return "\n".join(prompt_parts)

    def _build_candidate_content(self, candidates: List[Dict]) -> str:
        """把候选事件整理成适合模型阅读的紧凑文本。"""
        lines: List[str] = []
        for index, item in enumerate(candidates, 1):
            title = str(item.get("title", "")).strip()
            if not title:
                continue

            source_names = item.get("source_names", []) or []
            source_label = " / ".join(source_names[:4]) if source_names else "未知来源"
            lines.append(f"{index}. 标题：{title}")
            lines.append(f"   来源覆盖：{source_label}（共 {item.get('source_count', 0)} 个来源）")

            hotlist_count = int(item.get("hotlist_count", 0) or 0)
            rss_count = int(item.get("rss_count", 0) or 0)
            lines.append(
                f"   信号构成：热榜 {hotlist_count} 条，RSS {rss_count} 条"
            )

            best_rank = item.get("best_rank", 9999)
            if isinstance(best_rank, int) and best_rank < 9999:
                lines.append(f"   热榜最佳排名：{best_rank}")

            published_at = item.get("published_at", "")
            if published_at:
                lines.append(f"   发布时间：{published_at}")

            evidence_titles = item.get("evidence_titles", []) or []
            if evidence_titles:
                lines.append("   相关标题证据：")
                for evidence_title in evidence_titles[: self.max_evidence]:
                    lines.append(f"   - {evidence_title}")

            summary = str(item.get("summary", "")).strip()
            if summary:
                lines.append(f"   摘要：{summary[:280]}")

            article_title = str(item.get("article_title", "")).strip()
            if article_title:
                lines.append(f"   正文标题：{article_title}")

            article_excerpt = str(item.get("article_excerpt", "")).strip()
            if article_excerpt:
                lines.append(f"   正文摘要/节选：{article_excerpt[:600]}")

            article_content = str(item.get("article_content", "")).strip()
            if article_content:
                lines.append("   正文内容（节选）：")
                lines.append(f"   {article_content[:2000]}")
            lines.append("")

        return "\n".join(lines).strip()

    def _parse_response(self, response: str) -> OpportunityAnalysisResult:
        """解析 AI 返回的结构化 JSON。"""
        raw_response = response or ""
        json_text = self._extract_json_text(raw_response)
        if not json_text:
            return OpportunityAnalysisResult(
                success=False,
                raw_response=raw_response,
                error="机会识别响应中未找到可解析的 JSON 对象",
            )

        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as exc:
            return OpportunityAnalysisResult(
                success=False,
                raw_response=raw_response,
                error=f"机会识别 JSON 解析失败: {exc}",
            )

        if not isinstance(payload, dict):
            return OpportunityAnalysisResult(
                success=False,
                raw_response=raw_response,
                error="机会识别 JSON 根对象不是字典",
            )

        overall_payload = payload
        signals_payload = []

        if isinstance(payload.get("overall_assessment"), dict):
            overall_payload = payload["overall_assessment"]
            if isinstance(payload.get("top_opportunities"), list):
                signals_payload = payload.get("top_opportunities", [])
        elif isinstance(payload.get("opportunity_assessment"), dict):
            overall_payload = payload["opportunity_assessment"]
            if isinstance(payload.get("top_opportunities"), list):
                signals_payload = payload.get("top_opportunities", [])

        normalized = parse_assessment_payload(overall_payload)
        assessment = OpportunityAssessment(**normalized)
        signals: List[OpportunitySignal] = []

        if isinstance(signals_payload, list):
            for signal_payload in signals_payload:
                if not isinstance(signal_payload, dict):
                    continue
                signal_data = parse_signal_payload(signal_payload)
                if not signal_data.get("title") and signal_data.get("opportunity_summary"):
                    signal_data["title"] = signal_data["opportunity_summary"]
                signals.append(OpportunitySignal(**signal_data))

        if assessment.has_opportunity and not signals:
            signals.append(
                OpportunitySignal(
                    title=assessment.opportunity_summary or "未命名机会",
                    opportunity_type=assessment.opportunity_type,
                    opportunity_summary=assessment.opportunity_summary,
                    opportunity_reason=assessment.opportunity_reason,
                    risk_level=assessment.risk_level,
                    confidence=assessment.confidence,
                    actionable_targets=assessment.actionable_targets,
                    suggested_action=assessment.suggested_action,
                    related_titles=[],
                )
            )

        return OpportunityAnalysisResult(
            success=True,
            raw_response=raw_response,
            assessment=assessment,
            signals=signals,
        )

    def _extract_json_text(self, response: str) -> str:
        """
        从 AI 原始响应中提取 JSON 文本。

        兼容：
        1. 直接 JSON
        2. ```json 代码块
        3. 前后带少量说明文字
        """
        text = (response or "").strip()
        if not text:
            return ""

        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            return fenced.group(1).strip()

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1].strip()
        return ""
