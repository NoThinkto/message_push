# coding=utf-8
"""
文章内容读取工具。

MCP 层只保留工具包装，实际 Jina Reader 请求逻辑复用
trendradar.core.article_reader.ArticleReader。
"""

from typing import Dict, List

from trendradar.core.article_reader import DEFAULT_TIMEOUT, ArticleReader

from ..utils.errors import MCPError, InvalidParameterError


class ArticleReaderTools:
    """文章内容读取工具类。"""

    def __init__(self, project_root: str = None, jina_api_key: str = None):
        self.project_root = project_root
        self.jina_api_key = jina_api_key
        self.reader = ArticleReader(jina_api_key=jina_api_key)

    def read_article(
        self,
        url: str,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> Dict:
        """读取单篇文章内容（Markdown 格式）。"""
        try:
            if not url or not url.startswith(("http://", "https://")):
                raise InvalidParameterError(
                    f"无效的 URL: {url}",
                    suggestion="URL 必须以 http:// 或 https:// 开头",
                )
            return self.reader.read_article(url=url, timeout=timeout)
        except MCPError as exc:
            return {"success": False, "error": exc.to_dict()}
        except Exception as exc:
            return {
                "success": False,
                "error": {
                    "code": "REQUEST_ERROR",
                    "message": str(exc),
                    "url": url,
                },
            }

    def read_articles_batch(
        self,
        urls: List[str],
        timeout: int = DEFAULT_TIMEOUT,
    ) -> Dict:
        """批量读取文章内容。"""
        try:
            if not urls:
                raise InvalidParameterError(
                    "URL 列表不能为空",
                    suggestion="请提供至少一个 URL",
                )
            return self.reader.read_articles_batch(urls=urls, timeout=timeout)
        except MCPError as exc:
            return {"success": False, "error": exc.to_dict()}
        except Exception as exc:
            return {
                "success": False,
                "error": {
                    "code": "BATCH_ERROR",
                    "message": str(exc),
                },
            }
