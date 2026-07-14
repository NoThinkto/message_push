# coding=utf-8
"""
机会识别流程 - 第一阶段标题级筛选

职责：
1. 仅根据标题级信号判断“是否值得抓正文”；
2. 不做机会价值挖掘；
3. 优先过滤低信息量、纯参数、纯价格、纯描述性标题。
"""

import json
import re
from typing import Dict, List, Optional

from trendradar.ai.client import AIClient
from trendradar.ai.prompt_loader import load_prompt_template
from trendradar.opportunity_pipeline.models import (
    TitleFetchTarget,
    TitleFilterResult,
)
from trendradar.opportunity_pipeline.prompt_schema import parse_title_filter_payload


class OpportunityTitleFilterAnalyzer:
    """标题级正文抓取筛选器。"""

    def __init__(
        self,
        ai_config: Dict,
        pipeline_config: Dict,
        get_time_func,
        debug: bool = False,
    ):
        self.ai_config = ai_config
        self.pipeline_config = pipeline_config
        self.get_time_func = get_time_func
        self.debug = debug
        self.client = AIClient(ai_config)
        self.max_candidates = int(pipeline_config.get("MAX_CANDIDATES", 60) or 60)
        self.max_fetch_candidates = int(pipeline_config.get("SECOND_PASS_MAX_CANDIDATES", 30) or 30)#决定文章筛选的控制变量

        self.system_prompt, self.user_prompt_template = load_prompt_template(
            pipeline_config.get("TITLE_FILTER_PROMPT_FILE", "opportunity_title_filter_prompt.txt"),
            label="Opportunity Title Filter",
        )

    def analyze(
        self,
        candidates: List[Dict],
        report_mode: str = "current",
        platforms: Optional[List[str]] = None,
    ) -> TitleFilterResult:
        """执行标题级筛选，只判断是否值得抓正文。"""
        valid, error = self.client.validate_config()
        if not valid:
            return TitleFilterResult(success=False, error=error)

        selected_candidates = list(candidates[: self.max_candidates])
        if not selected_candidates:
            return TitleFilterResult(
                success=False,
                error="当前没有可供标题级筛选的候选事件",
            )

        prompt = self._build_prompt(
            candidates=selected_candidates,
            report_mode=report_mode,
            platforms=platforms,
        )

        if self.debug:
            print("\n" + "=" * 80)
            print("[Opportunity Title Filter][DEBUG] 发送给 AI 的提示词")
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
            return TitleFilterResult(
                success=False,
                error=f"标题级筛选 AI 调用失败: {type(exc).__name__}: {exc}",
            )

        return self._parse_response(response)

    def _build_prompt(
        self,
        candidates: List[Dict],
        report_mode: str,
        platforms: Optional[List[str]] = None,
    ) -> str:
        current_time = self.get_time_func().strftime("%Y-%m-%d %H:%M:%S")
        candidate_content = self._build_candidate_content(candidates)

        prompt_parts = [self.user_prompt_template.strip()]
        prompt_parts.append("")
        prompt_parts.append("以下是本轮标题级候选事件，请判断哪些标题值得继续抓正文。")
        prompt_parts.append(f"- 当前时间：{current_time}")
        prompt_parts.append(f"- 报告模式：{report_mode}")
        prompt_parts.append(f"- 监控平台：{', '.join(platforms or []) or '未提供'}")
        prompt_parts.append(f"- 候选事件数：{len(candidates)}")
        prompt_parts.append(f"- 最多挑选 {self.max_fetch_candidates} 条进入正文阶段")
        prompt_parts.append("")
        prompt_parts.append("【标题级候选事件列表】")
        prompt_parts.append(candidate_content or "无")
        return "\n".join(prompt_parts)

    def _build_candidate_content(self, candidates: List[Dict]) -> str:
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
            lines.append(f"   信号构成：热榜 {hotlist_count} 条，RSS {rss_count} 条")

            best_rank = item.get("best_rank", 9999)
            if isinstance(best_rank, int) and best_rank < 9999:
                lines.append(f"   热榜最佳排名：{best_rank}")

            lines.append("")

        return "\n".join(lines).strip()

    def _parse_response(self, response: str) -> TitleFilterResult:
        raw_response = response or ""
        json_text = self._extract_json_text(raw_response)
        if not json_text:
            return TitleFilterResult(
                success=False,
                raw_response=raw_response,
                error="标题级筛选响应中未找到可解析的 JSON 对象",
            )

        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as exc:
            return TitleFilterResult(
                success=False,
                raw_response=raw_response,
                error=f"标题级筛选 JSON 解析失败: {exc}",
            )

        if not isinstance(payload, dict):
            return TitleFilterResult(
                success=False,
                raw_response=raw_response,
                error="标题级筛选 JSON 根对象不是字典",
            )

        parsed = parse_title_filter_payload(payload)
        fetch_targets = [
            TitleFetchTarget(**item)
            for item in parsed.get("fetch_targets", [])
        ]

        return TitleFilterResult(
            success=True,
            raw_response=raw_response,
            should_fetch_any=parsed.get("should_fetch_any", bool(fetch_targets)),
            selection_reason=parsed.get("selection_reason", ""),
            fetch_targets=fetch_targets,
        )

    def _extract_json_text(self, response: str) -> str:
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
