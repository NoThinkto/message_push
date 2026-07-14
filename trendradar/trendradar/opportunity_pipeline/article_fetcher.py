# coding=utf-8
"""
机会识别流程 - 正文抓取器

职责：
1. 只对第一阶段挑出的少量高潜力候选抓正文；
2. 运行时增强候选事件，不改存储结构；
3. 抓取失败时保守跳过，不影响整轮流程稳定性。
"""

from typing import Dict, List

from trendradar.core.article_reader import ArticleReader


class OpportunityArticleFetcher:
    """正文抓取器，使用 Jina Reader 进行网页正文提取。"""

    def __init__(self, pipeline_config: Dict, proxy_url: str = "", debug: bool = False):
        self.pipeline_config = pipeline_config
        self.proxy_url = proxy_url
        self.debug = debug
        self.timeout = int(pipeline_config.get("ARTICLE_FETCH_TIMEOUT", 15) or 15)
        self.max_chars = int(pipeline_config.get("ARTICLE_MAX_CHARS", 6000) or 6000)
        self.reader = ArticleReader(
            jina_api_key=pipeline_config.get("JINA_API_KEY", ""),
            proxy_url=proxy_url,
        )

    def enrich_candidates(self, candidates: List[Dict]) -> List[Dict]:
        """抓取正文并回填到候选对象。"""
        enriched: List[Dict] = []
        for candidate in candidates:
            item = dict(candidate)
            url = self._pick_url(candidate)
            item["article_url"] = url
            item["article_fetch_success"] = False
            item["article_fetch_error"] = ""
            item["article_title"] = ""
            item["article_content"] = ""
            item["article_excerpt"] = ""

            if not url:
                item["article_fetch_error"] = "未找到可抓取的正文链接"
                enriched.append(item)
                continue

            fetch_result = self._fetch_article(url)
            if fetch_result["success"]:
                content = fetch_result["content"]
                item["article_fetch_success"] = True
                item["article_content"] = content
                item["article_excerpt"] = content[:800]
                item["article_title"] = self._extract_title(content, candidate.get("title", ""))
            else:
                item["article_fetch_error"] = fetch_result["error"]

            enriched.append(item)
        return enriched

    def _pick_url(self, candidate: Dict) -> str:
        urls = candidate.get("urls", []) or []
        for url in urls:
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                return url
        return ""

    def _fetch_article(self, url: str) -> Dict[str, str]:
        result = self.reader.read_article(url, timeout=self.timeout)
        if not result.get("success"):
            error = result.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            return {
                "success": False,
                "error": message or "未知正文抓取错误",
            }

        content = ((result.get("data") or {}).get("content") or "").strip()
        if len(content) > self.max_chars:
            content = content[: self.max_chars]
        return {
            "success": True,
            "content": content,
        }

    def _extract_title(self, content: str, fallback_title: str) -> str:
        lines = [line.strip("# ").strip() for line in content.splitlines() if line.strip()]
        if lines:
            return lines[0][:200]
        return str(fallback_title or "").strip()
