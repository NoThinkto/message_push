# coding=utf-8
"""
机会识别流程 - 总控

两阶段流程：
1. 标题级深读筛选：只判断哪些候选值得抓正文；
2. 正文级机会挖掘：只对高潜力候选做深度价值分析；
3. 门控与推送：仅根据正文确认后的具体机会决定是否推送。
"""

import re
from typing import Dict, List, Optional

from .analyzer import OpportunityAnalyzer
from .article_fetcher import OpportunityArticleFetcher
from .candidate_pool import build_opportunity_candidates
from .gate import evaluate_push_gate
from .models import OpportunityAnalysisResult, OpportunityAssessment
from .notification_dispatcher import OpportunityNotificationDispatcher
from .report_builder import build_opportunity_report
from .title_filter import OpportunityTitleFilterAnalyzer


class OpportunityPipeline:
    """机会识别流程总控器。"""

    def __init__(self, ctx, config: Dict, proxy_url: str = "", debug: bool = False):
        self.ctx = ctx
        self.config = config
        self.proxy_url = proxy_url
        self.debug = debug

    def run(
        self,
        stats: List[Dict],
        failed_ids: Optional[List],
        new_titles: Optional[Dict],
        id_to_name: Optional[Dict],
        mode: str,
        rss_items: Optional[List[Dict]] = None,
        report_type: str = "套利机会预警",
        current_results: Optional[Dict] = None,
        raw_rss_items: Optional[List[Dict]] = None,
    ) -> Dict:
        """执行两阶段机会识别流程。"""
        pipeline_cfg = self.config.get("OPPORTUNITY_PIPELINE", {})

        candidates = build_opportunity_candidates(
            current_results=current_results,
            id_to_name=id_to_name,
            raw_rss_items=raw_rss_items,
            pipeline_config=pipeline_cfg,
            fallback_stats=stats,
            fallback_rss_items=rss_items,
        )
        print(f"[机会流程] 候选池构建完成，共 {len(candidates)} 个候选事件")
        candidates = self._filter_previously_analyzed_candidates(candidates, pipeline_cfg)
        if not candidates:
            print("[机会流程] 本轮不推送：候选均已在近期深读分析过")
            return {
                "implemented": True,
                "message": "近期无新的机会候选",
                "analysis_result": None,
                "decision": None,
                "dispatch_result": {},
                "fallback_to_legacy": False,
                "should_push": False,
                "push_success": False,
            }

        platforms = self._extract_platforms(id_to_name)
        if not pipeline_cfg.get("SECOND_PASS_ENABLED", True):
            print("[机会流程] second_pass_enabled=false，跳过正文二阶段分析")
            return {
                "implemented": True,
                "message": "正文二阶段分析未启用",
                "analysis_result": None,
                "decision": None,
                "dispatch_result": {},
                "fallback_to_legacy": False,
                "should_push": False,
                "push_success": False,
            }

        title_filter = OpportunityTitleFilterAnalyzer(
            ai_config=self.config.get("AI", {}),
            pipeline_config=pipeline_cfg,
            get_time_func=self.ctx.get_time,
            debug=self.debug,
        )
        title_filter_result = title_filter.analyze(
            candidates=candidates,
            report_mode=mode,
            platforms=platforms,
        )

        if not title_filter_result.success:
            print(f"[机会流程] 标题级筛选失败：{title_filter_result.error}")
            return {
                "implemented": True,
                "message": "标题级筛选失败",
                "analysis_result": None,
                "decision": None,
                "dispatch_result": {},
                "fallback_to_legacy": False,
                "should_push": False,
                "push_success": False,
            }

        fetch_candidates = self._select_fetch_candidates(
            candidates=candidates,
            title_filter_result=title_filter_result,
            pipeline_cfg=pipeline_cfg,
        )
        fetch_summary = self._format_fetch_summary(fetch_candidates)
        print(
            f"[机会流程] 标题级筛选完成：原始候选 {len(candidates)} 个，"
            f"选出 {len(fetch_candidates)} 个正文深读候选。标题：{fetch_summary or '无'}"
        )

        if not fetch_candidates:
            print("[机会流程] 本轮不推送：标题级筛选后没有值得抓正文的候选")
            return {
                "implemented": True,
                "message": "没有正文深读候选",
                "analysis_result": None,
                "decision": None,
                "dispatch_result": {},
                "fallback_to_legacy": False,
                "should_push": False,
                "push_success": False,
            }

        article_fetcher = OpportunityArticleFetcher(
            pipeline_cfg,
            proxy_url=self.proxy_url,
            debug=self.debug,
        )
        enriched_candidates = article_fetcher.enrich_candidates(fetch_candidates)
        successful_articles = [item for item in enriched_candidates if item.get("article_fetch_success")]
        print(
            f"[机会流程] 正文抓取完成：计划抓取 {len(fetch_candidates)} 篇，"
            f"成功 {len(successful_articles)} 篇，失败 {len(enriched_candidates) - len(successful_articles)} 篇"
        )
        failure_summary = self._format_article_failure_summary(enriched_candidates)
        if failure_summary:
            print(f"[机会流程] 正文抓取失败详情：{failure_summary}")
        for item in enriched_candidates:
            if not item.get("article_fetch_success"):
                self._mark_candidate_analyzed(
                    item,
                    status="failed",
                    error=item.get("article_fetch_error", "") or "正文抓取失败",
                    pipeline_cfg=pipeline_cfg,
                )

        if not successful_articles:
            print("[机会流程] 本轮不推送：正文抓取全部失败，无法进入第二阶段价值挖掘")
            return {
                "implemented": True,
                "message": "正文抓取失败",
                "analysis_result": None,
                "decision": None,
                "dispatch_result": {},
                "fallback_to_legacy": False,
                "should_push": False,
                "push_success": False,
            }

        deep_analyzer = OpportunityAnalyzer(
            ai_config=self.config.get("AI", {}),
            pipeline_config=pipeline_cfg,
            get_time_func=self.ctx.get_time,
            prompt_file=pipeline_cfg.get("DEEP_ANALYSIS_PROMPT_FILE", "opportunity_deep_analysis_prompt.txt"),
            debug=self.debug,
        )
        result = self._analyze_articles_individually(
            analyzer=deep_analyzer,
            articles=successful_articles,
            report_mode=mode,
            platforms=platforms,
        )

        decision = evaluate_push_gate(result, pipeline_cfg)
        report_data = build_opportunity_report(
            stats=stats,
            failed_ids=failed_ids,
            new_titles=new_titles,
            id_to_name=id_to_name,
            opportunity_result=result,
            mode=mode,
            rss_items=rss_items,
            candidates=successful_articles,
            decision=decision,
        )

        dispatch_result: Dict[str, bool] = {}
        recognized_count = len(result.signals) if result else 0
        signal_summary = self._format_signal_summary(result.signals if result else [])
        if decision.should_push:
            selected_count = len(decision.selected_signals)
            if signal_summary:
                print(
                    f"[机会流程] 满足推送条件：标题级候选 {len(candidates)} 个，"
                    f"正文深读 {len(successful_articles)} 篇，AI 识别到 {recognized_count} 个机会，"
                    f"入选推送 {selected_count} 个。机会标题：{signal_summary}。{decision.reason}"
                )
            else:
                print(
                    f"[机会流程] 满足推送条件：标题级候选 {len(candidates)} 个，"
                    f"正文深读 {len(successful_articles)} 篇，AI 识别到 {recognized_count} 个机会，"
                    f"入选推送 {selected_count} 个。{decision.reason}"
                )
            dispatcher = OpportunityNotificationDispatcher(
                self.config,
                proxy_url=self.proxy_url,
                storage_manager=self.ctx.get_storage_manager(),
            )
            dispatch_result = dispatcher.dispatch(report_data, report_type=report_type)
            if not dispatch_result:
                print("[机会流程] 未发现可用的新流程通知渠道")
        else:
            if signal_summary:
                print(
                    f"[机会流程] 本轮不推送：标题级候选 {len(candidates)} 个，"
                    f"正文深读 {len(successful_articles)} 篇，AI 识别到 {recognized_count} 个机会。"
                    f"机会标题：{signal_summary}。{decision.reason}"
                )
            else:
                print(
                    f"[机会流程] 本轮不推送：标题级候选 {len(candidates)} 个，"
                    f"正文深读 {len(successful_articles)} 篇，但未形成通过门控的具体机会。{decision.reason}"
                )

        return {
            "implemented": True,
            "message": "机会识别流程已执行",
            "analysis_result": result,
            "decision": decision,
            "dispatch_result": dispatch_result,
            "fallback_to_legacy": decision.fallback_to_legacy,
            "should_push": decision.should_push,
            "push_success": any(dispatch_result.values()) if dispatch_result else False,
        }

    def _select_fetch_candidates(
        self,
        candidates: List[Dict],
        title_filter_result,
        pipeline_cfg: Dict,
    ) -> List[Dict]:
        """根据第一阶段结果挑出真正要抓正文的候选。"""
        priority_threshold = float(pipeline_cfg.get("SECOND_PASS_MIN_FETCH_PRIORITY", 0.45) or 0.45)
        require_url = bool(pipeline_cfg.get("SECOND_PASS_REQUIRE_URL", True))
        max_candidates = int(pipeline_cfg.get("SECOND_PASS_MAX_CANDIDATES", 30) or 30) #决定文章筛选的控制变量

        candidate_map = {self._normalize_title(item.get("title", "")): item for item in candidates}
        selected: List[Dict] = []

        for target in title_filter_result.fetch_targets:
            if target.fetch_priority < priority_threshold:
                continue
            candidate = candidate_map.get(self._normalize_title(target.title))
            if not candidate:
                continue
            if require_url and not self._pick_url(candidate):
                continue
            enriched = dict(candidate)
            enriched["fetch_priority"] = target.fetch_priority
            enriched["title_filter_reason"] = target.reason
            enriched["title_filter_content_type"] = target.content_type
            enriched["title_filter_information_density"] = target.information_density
            selected.append(enriched)
            if len(selected) >= max_candidates:
                break

        return selected

    def _filter_previously_analyzed_candidates(
        self,
        candidates: List[Dict],
        pipeline_cfg: Dict,
    ) -> List[Dict]:
        """过滤近期已进入过 opportunity 正文深读/分析的候选。"""
        if not pipeline_cfg.get("ANALYSIS_DEDUPE_ENABLED", True):
            return candidates

        key_to_candidate: Dict[str, Dict] = {}
        filtered_candidates: List[Dict] = []
        for candidate in candidates:
            item_key = self._build_opportunity_item_key(candidate)
            if not item_key:
                filtered_candidates.append(candidate)
                continue
            enriched = dict(candidate)
            enriched["opportunity_item_key"] = item_key
            key_to_candidate[item_key] = enriched
            filtered_candidates.append(enriched)

        try:
            storage = self.ctx.get_storage_manager()
            skip_keys = storage.get_recent_opportunity_analyzed_keys(
                list(key_to_candidate.keys()),
                ttl_hours=int(pipeline_cfg.get("ANALYSIS_DEDUPE_TTL_HOURS", 24) or 24),
                retry_failed=bool(pipeline_cfg.get("ANALYSIS_RETRY_FAILED", True)),
                failed_retry_after_minutes=int(
                    pipeline_cfg.get("ANALYSIS_FAILED_RETRY_AFTER_MINUTES", 60) or 60
                ),
            )
        except Exception as exc:
            print(f"[机会流程] 查询已分析候选失败，跳过去重过滤：{type(exc).__name__}: {exc}")
            return filtered_candidates

        if not skip_keys:
            return filtered_candidates

        kept = [
            candidate
            for candidate in filtered_candidates
            if candidate.get("opportunity_item_key") not in skip_keys
        ]
        print(
            f"[机会流程] 近期已深读去重：跳过 {len(filtered_candidates) - len(kept)} 个，"
            f"剩余 {len(kept)} 个候选"
        )
        return kept

    def _analyze_articles_individually(
        self,
        analyzer: OpportunityAnalyzer,
        articles: List[Dict],
        report_mode: str,
        platforms: List[str],
    ) -> OpportunityAnalysisResult:
        """逐篇调用正文级 AI 分析，再合并成一次门控可用的结果。"""
        results: List[OpportunityAnalysisResult] = []
        errors: List[str] = []
        storage = self._get_storage_for_opportunity_records()

        total = len(articles)
        if storage:
            storage.begin_batch()
        for index, article in enumerate(articles, 1):
            title = article.get("title", "") or "未命名候选"
            print(f"[机会流程] 正文深度分析 {index}/{total}：{title}")
            try:
                result = analyzer.analyze(
                    candidates=[article],
                    report_mode=report_mode,
                    platforms=platforms,
                )
            except Exception as exc:
                errors.append(f"{title}：{type(exc).__name__}: {exc}")
                print(f"[机会流程] 正文深度分析异常，跳过当前文章：{title} | {type(exc).__name__}: {exc}")
                self._mark_candidate_analyzed(
                    article,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                    storage=storage,
                    pipeline_cfg=analyzer.pipeline_config,
                )
                continue

            if result.success:
                results.append(result)
                self._mark_candidate_analyzed(
                    article,
                    status="success",
                    storage=storage,
                    pipeline_cfg=analyzer.pipeline_config,
                )
            else:
                errors.append(f"{title}：{result.error}")
                self._mark_candidate_analyzed(
                    article,
                    status="failed",
                    error=result.error,
                    storage=storage,
                    pipeline_cfg=analyzer.pipeline_config,
                )
        if storage:
            storage.end_batch()

        if not results:
            return OpportunityAnalysisResult(
                success=False,
                error="正文级逐篇 AI 分析全部失败：" + "；".join(errors[:3]),
                analyzed_news_count=total,
                source_count=0,
                mode=report_mode,
            )

        signals = [
            signal
            for result in results
            for signal in result.signals
        ]
        best_assessment = max(
            (result.assessment for result in results),
            key=lambda assessment: assessment.confidence,
            default=OpportunityAssessment(),
        )
        has_opportunity = bool(signals) or any(result.assessment.has_opportunity for result in results)
        assessment = OpportunityAssessment(
            has_opportunity=has_opportunity,
            opportunity_type=best_assessment.opportunity_type,
            opportunity_summary=self._build_merged_assessment_summary(results, signals),
            opportunity_reason=self._build_merged_assessment_reason(results, errors),
            risk_level=best_assessment.risk_level,
            confidence=best_assessment.confidence,
            actionable_targets=best_assessment.actionable_targets,
            suggested_action=best_assessment.suggested_action,
        )
        raw_response = "\n\n".join(
            result.raw_response
            for result in results
            if result.raw_response
        )
        source_names = {
            source_name
            for article in articles
            for source_name in article.get("source_names", [])
            if source_name
        }

        if errors:
            print(f"[机会流程] 正文逐篇分析失败详情：{'；'.join(errors[:5])}")

        return OpportunityAnalysisResult(
            success=True,
            error="；".join(errors),
            raw_response=raw_response,
            assessment=assessment,
            signals=signals,
            analyzed_news_count=total,
            source_count=len(source_names),
            mode=report_mode,
        )

    def _build_merged_assessment_summary(
        self,
        results: List[OpportunityAnalysisResult],
        signals: List,
    ) -> str:
        if signals:
            return f"逐篇正文分析后识别到 {len(signals)} 个候选机会"

        summaries = [
            result.assessment.opportunity_summary
            for result in results
            if result.assessment.opportunity_summary
        ]
        return summaries[0] if summaries else "逐篇正文分析后未形成明确可执行机会"

    def _build_merged_assessment_reason(
        self,
        results: List[OpportunityAnalysisResult],
        errors: List[str],
    ) -> str:
        reasons = [
            result.assessment.opportunity_reason
            for result in results
            if result.assessment.opportunity_reason
        ]
        reason = "；".join(reasons[:3])
        if errors:
            suffix = f"其中 {len(errors)} 篇正文级 AI 分析失败，已跳过。"
            reason = f"{reason}；{suffix}" if reason else suffix
        return reason

    def _get_storage_for_opportunity_records(self):
        try:
            return self.ctx.get_storage_manager()
        except Exception as exc:
            print(f"[机会流程] 获取存储管理器失败，无法记录去重状态：{type(exc).__name__}: {exc}")
            return None

    def _mark_candidate_analyzed(
        self,
        candidate: Dict,
        status: str,
        error: str = "",
        storage=None,
        pipeline_cfg: Optional[Dict] = None,
    ) -> None:
        pipeline_cfg = pipeline_cfg or self.config.get("OPPORTUNITY_PIPELINE", {})
        if not pipeline_cfg.get("ANALYSIS_DEDUPE_ENABLED", True):
            return

        storage = storage or self._get_storage_for_opportunity_records()
        if not storage:
            return

        item_key = candidate.get("opportunity_item_key") or self._build_opportunity_item_key(candidate)
        if not item_key:
            return

        try:
            storage.save_opportunity_analyzed_item(
                item_key=item_key,
                title=candidate.get("title", "") or candidate.get("article_title", "") or "未命名候选",
                url=self._pick_url(candidate) or candidate.get("article_url", ""),
                source_type=candidate.get("source_type", ""),
                source_names=candidate.get("source_names", []) or [],
                status=status,
                error=(error or "")[:500],
            )
        except Exception as exc:
            print(f"[机会流程] 保存已分析候选记录失败：{type(exc).__name__}: {exc}")

    def _build_opportunity_item_key(self, candidate: Dict) -> str:
        url = self._pick_url(candidate) or candidate.get("article_url", "")
        normalized_url = self._normalize_url_for_key(url)
        if normalized_url:
            return f"url:{normalized_url}"

        normalized_title = self._normalize_title(candidate.get("title", ""))
        if normalized_title:
            return f"title:{normalized_title}"
        return ""

    def _normalize_url_for_key(self, url: str) -> str:
        text = str(url or "").strip()
        if not text:
            return ""
        return text.split("#", 1)[0].rstrip("/")

    def _pick_url(self, candidate: Dict) -> str:
        urls = candidate.get("urls", []) or []
        for url in urls:
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                return url
        return ""

    def _extract_platforms(self, id_to_name: Optional[Dict]) -> List[str]:
        """从 ID->名称映射中提取平台名称列表。"""
        if not id_to_name:
            return []
        return list(id_to_name.values())

    def _format_signal_summary(self, signals: List) -> str:
        """把识别出的机会标题压成一行摘要，便于日志快速查看。"""
        if not signals:
            return ""

        parts: List[str] = []
        for signal in signals[:5]:
            title = signal.title or signal.opportunity_summary or "未命名机会"
            parts.append(f"{title}({signal.confidence:.2f})")

        summary = "；".join(parts)
        if len(signals) > 5:
            summary += f"；其余 {len(signals) - 5} 条略"
        return summary

    def _format_fetch_summary(self, candidates: List[Dict]) -> str:
        """格式化第一阶段筛出来的正文候选标题。"""
        if not candidates:
            return ""

        parts: List[str] = []
        for item in candidates[:5]:
            title = item.get("title", "") or "未命名候选"
            parts.append(f"{title}({float(item.get('fetch_priority', 0.0)):.2f})")

        summary = "；".join(parts)
        if len(candidates) > 5:
            summary += f"；其余 {len(candidates) - 5} 条略"
        return summary

    def _normalize_title(self, title: str) -> str:
        text = str(title or "").strip().lower()
        text = re.sub(r"^\s*【?快讯】?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^\s*直播中[:：]?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^\s*(早报|午报|晚报|日报|周报|收评|开盘|午盘|尾盘)[:：]?\s*", "", text, flags=re.IGNORECASE)
        return re.sub(r"[\s\-\|\[\]【】()（）:：!！?？,，。、“”\"'`]+", "", text)

    def _format_article_failure_summary(self, candidates: List[Dict]) -> str:
        """格式化正文抓取失败原因，避免日志只看到 0/N。"""
        failed = [item for item in candidates if not item.get("article_fetch_success")]
        if not failed:
            return ""

        parts: List[str] = []
        for item in failed[:5]:
            title = item.get("title", "") or "未命名候选"
            url = item.get("article_url", "") or "无URL"
            error = item.get("article_fetch_error", "") or "未知错误"
            parts.append(f"{title} | {url} | {error}")

        summary = "；".join(parts)
        if len(failed) > 5:
            summary += f"；其余 {len(failed) - 5} 条略"
        return summary
