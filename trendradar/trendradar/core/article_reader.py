# coding=utf-8
"""
Shared article reader.

The project has two entry points that need article text:
1. MCP article tools.
2. The opportunity pipeline second-stage deep analysis.

Both paths should use this module, so network fixes and fallback behavior only
need to be maintained in one place.
"""

import re
import time
from html import unescape
from typing import Dict, List, Optional

import requests


JINA_READER_BASE = "https://r.jina.ai"
DEFAULT_TIMEOUT = 30
MAX_BATCH_SIZE = 5
BATCH_INTERVAL = 5.0
MIN_DIRECT_TEXT_LENGTH = 120


class ArticleReader:
    """Read article content with Jina Reader first, then direct HTML fallback."""

    def __init__(
        self,
        jina_api_key: Optional[str] = None,
        proxy_url: str = "",
        throttle_interval: float = BATCH_INTERVAL,
    ):
        self.jina_api_key = jina_api_key
        self.proxy_url = proxy_url
        self.throttle_interval = throttle_interval
        self._last_request_time = 0.0

    def read_article(self, url: str, timeout: int = DEFAULT_TIMEOUT) -> Dict:
        """Read one article as Markdown-like text."""
        if not url or not url.startswith(("http://", "https://")):
            return {
                "success": False,
                "error": {
                    "code": "INVALID_URL",
                    "message": f"Invalid URL: {url}",
                    "url": url,
                },
            }

        self._throttle()
        jina_result = self._read_via_jina(url=url, timeout=timeout)
        if jina_result.get("success"):
            return jina_result

        direct_result = self._read_direct(url=url, timeout=timeout)
        if direct_result.get("success"):
            data = direct_result.get("data") or {}
            data["fallback_from"] = "jina_reader"
            data["fallback_reason"] = self._error_message(jina_result)
            direct_result["data"] = data
            return direct_result

        return {
            "success": False,
            "error": {
                "code": "ARTICLE_FETCH_FAILED",
                "message": (
                    f"Jina Reader failed: {self._error_message(jina_result)}; "
                    f"direct fetch failed: {self._error_message(direct_result)}"
                ),
                "url": url,
            },
        }

    def read_articles_batch(self, urls: List[str], timeout: int = DEFAULT_TIMEOUT) -> Dict:
        """Read articles in small batches."""
        if not urls:
            return {
                "success": False,
                "error": {
                    "code": "INVALID_URLS",
                    "message": "URL list cannot be empty",
                },
            }

        actual_urls = urls[:MAX_BATCH_SIZE]
        skipped = len(urls) - len(actual_urls)
        results = []
        succeeded = 0
        failed = 0

        for index, url in enumerate(actual_urls):
            result = self.read_article(url=url, timeout=timeout)
            results.append(
                {
                    "index": index + 1,
                    "url": url,
                    "success": result["success"],
                    "data": result.get("data"),
                    "error": result.get("error"),
                }
            )
            if result["success"]:
                succeeded += 1
            else:
                failed += 1

        return {
            "success": True,
            "summary": {
                "description": "Batch article reading result",
                "requested": len(urls),
                "processed": len(actual_urls),
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped,
                "interval_seconds": self.throttle_interval,
            },
            "articles": results,
            "note": f"Skipped {skipped} articles, max batch size is {MAX_BATCH_SIZE}" if skipped > 0 else None,
        }

    def _read_via_jina(self, url: str, timeout: int) -> Dict:
        try:
            response = requests.get(
                f"{JINA_READER_BASE}/{url}",
                headers=self._build_jina_headers(),
                proxies=self._build_proxies(),
                timeout=timeout,
            )

            if response.status_code == 200:
                content = response.text or ""
                return self._success(url=url, content=content, reader="jina")

            if response.status_code == 429:
                return {
                    "success": False,
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Jina Reader rate limited",
                        "url": url,
                        "suggestion": "Configure Jina API Key or lower article fetch frequency",
                    },
                }

            detail = self._short_text(response.text or "")
            return {
                "success": False,
                "error": {
                    "code": "JINA_FETCH_FAILED",
                    "message": f"HTTP {response.status_code}: {response.reason}" + (f" | {detail}" if detail else ""),
                    "url": url,
                },
            }
        except requests.Timeout:
            return {
                "success": False,
                "error": {
                    "code": "JINA_TIMEOUT",
                    "message": f"Jina Reader timeout after {timeout} seconds",
                    "url": url,
                },
            }
        except requests.RequestException as exc:
            return {
                "success": False,
                "error": {
                    "code": "JINA_REQUEST_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                    "url": url,
                },
            }
        except Exception as exc:
            return {
                "success": False,
                "error": {
                    "code": "JINA_UNKNOWN_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                    "url": url,
                },
            }

    def _read_direct(self, url: str, timeout: int) -> Dict:
        try:
            response = requests.get(
                url,
                headers=self._build_direct_headers(),
                proxies=self._build_proxies(),
                timeout=timeout,
            )
            if response.status_code != 200:
                detail = self._short_text(response.text or "")
                return {
                    "success": False,
                    "error": {
                        "code": "DIRECT_FETCH_FAILED",
                        "message": f"HTTP {response.status_code}: {response.reason}" + (f" | {detail}" if detail else ""),
                        "url": url,
                    },
                }

            if response.apparent_encoding and (
                not response.encoding or response.encoding.lower() == "iso-8859-1"
            ):
                response.encoding = response.apparent_encoding
            content = self._extract_text_from_html(response.text or "")
            if len(content) < MIN_DIRECT_TEXT_LENGTH:
                return {
                    "success": False,
                    "error": {
                        "code": "DIRECT_CONTENT_TOO_SHORT",
                        "message": f"Direct fetch produced too little readable text ({len(content)} chars)",
                        "url": url,
                    },
                }
            return self._success(url=url, content=content, reader="direct")
        except requests.Timeout:
            return {
                "success": False,
                "error": {
                    "code": "DIRECT_TIMEOUT",
                    "message": f"Direct fetch timeout after {timeout} seconds",
                    "url": url,
                },
            }
        except requests.RequestException as exc:
            return {
                "success": False,
                "error": {
                    "code": "DIRECT_REQUEST_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                    "url": url,
                },
            }
        except Exception as exc:
            return {
                "success": False,
                "error": {
                    "code": "DIRECT_UNKNOWN_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                    "url": url,
                },
            }

    def _success(self, url: str, content: str, reader: str) -> Dict:
        return {
            "success": True,
            "data": {
                "url": url,
                "content": content,
                "format": "markdown",
                "content_length": len(content),
                "reader": reader,
            },
        }

    def _build_jina_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "text/markdown",
            "X-Return-Format": "markdown",
            "X-No-Cache": "true",
        }
        if self.jina_api_key:
            headers["Authorization"] = f"Bearer {self.jina_api_key}"
        return headers

    def _build_direct_headers(self) -> Dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Cache-Control": "no-cache",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        }

    def _build_proxies(self) -> Optional[Dict[str, str]]:
        if not self.proxy_url:
            return None
        return {
            "http": self.proxy_url,
            "https": self.proxy_url,
        }

    def _throttle(self) -> None:
        if self.throttle_interval <= 0:
            return
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.throttle_interval:
            time.sleep(self.throttle_interval - elapsed)
        self._last_request_time = time.time()

    def _extract_text_from_html(self, html: str) -> str:
        html = re.sub(r"(?is)<(script|style|noscript|svg|canvas|iframe).*?>.*?</\1>", " ", html)
        title = self._extract_first_match(html, r"(?is)<title[^>]*>(.*?)</title>")
        description = self._extract_meta_description(html)

        blocks = []
        for pattern in (
            r"(?is)<h[1-3][^>]*>(.*?)</h[1-3]>",
            r"(?is)<p[^>]*>(.*?)</p>",
            r"(?is)<li[^>]*>(.*?)</li>",
        ):
            blocks.extend(self._clean_html_fragment(match) for match in re.findall(pattern, html))

        seen = set()
        unique_blocks = []
        for block in blocks:
            if len(block) < 12:
                continue
            key = block[:120]
            if key in seen:
                continue
            seen.add(key)
            unique_blocks.append(block)

        parts = []
        if title:
            parts.append(f"# {title}")
        if description and description != title:
            parts.append(description)
        parts.extend(unique_blocks)
        return "\n\n".join(parts).strip()

    def _extract_first_match(self, text: str, pattern: str) -> str:
        match = re.search(pattern, text)
        if not match:
            return ""
        return self._clean_html_fragment(match.group(1))

    def _extract_meta_description(self, html: str) -> str:
        patterns = (
            r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
            r'(?is)<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
            r'(?is)<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
            r'(?is)<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:description["\']',
        )
        for pattern in patterns:
            value = self._extract_first_match(html, pattern)
            if value:
                return value
        return ""

    def _clean_html_fragment(self, value: str) -> str:
        value = re.sub(r"(?is)<br\s*/?>", "\n", value)
        value = re.sub(r"(?is)<[^>]+>", " ", value)
        value = unescape(value)
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = re.sub(r"\n\s*", "\n", value)
        return value.strip()

    def _short_text(self, value: str, limit: int = 180) -> str:
        value = self._clean_html_fragment(value).replace("\n", " ")
        if len(value) > limit:
            return value[:limit] + "..."
        return value

    def _error_message(self, result: Dict) -> str:
        error = result.get("error") or {}
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or "unknown error")
        return str(error or "unknown error")
