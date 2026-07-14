# coding=utf-8
"""
机会识别流程 - 原始候选池构建器

职责：
1. 复用现有抓取结果 `current_results` 与 `raw_rss_items`；
2. 在不依赖关键词业务筛选的前提下，做基础裁剪；
3. 将多来源重复资讯聚合成更适合 AI 判断的事件候选列表；
4. 不改动 legacy 流程的数据结构，只服务于 opportunity 流程。
"""

import re
from typing import Dict, List, Optional


_LOW_SIGNAL_PATTERNS = [
    r"^\s*【?快讯】?\s*",
    r"^\s*直播中[:：]?\s*",
    r"^\s*(早报|午报|晚报|日报|周报|收评|开盘|午盘|尾盘)[:：]?\s*",
]


def build_opportunity_candidates(
    current_results: Optional[Dict],
    id_to_name: Optional[Dict],
    raw_rss_items: Optional[List[Dict]],
    pipeline_config: Optional[Dict] = None,
    fallback_stats: Optional[List[Dict]] = None,
    fallback_rss_items: Optional[List[Dict]] = None,
) -> List[Dict]:
    """
    构建 opportunity 流程的原始候选池。

    优先使用：
    - current_results：原始热榜抓取结果
    - raw_rss_items：原始 RSS 条目

    若调用侧没有提供原始数据，则退回旧的 `stats/rss_items`，
    以确保新流程在灰度阶段也能稳定运行。
    """
    cfg = pipeline_config or {}
    use_raw_candidates = bool(cfg.get("USE_RAW_CANDIDATES", True))
    dedupe_enabled = bool(cfg.get("DEDUPE_ENABLED", True))
    drop_low_signal_titles = bool(cfg.get("DROP_LOW_SIGNAL_TITLES", True))
    per_source_limit = int(cfg.get("PER_SOURCE_LIMIT", 6) or 6)
    max_candidates = int(cfg.get("MAX_CANDIDATES", 60) or 60)
    max_evidence = int(cfg.get("MAX_EVIDENCE_PER_CANDIDATE", 3) or 3)
    include_rss = bool(cfg.get("INCLUDE_RSS", True))

    hotlist_items: List[Dict] = []
    rss_items: List[Dict] = []

    if use_raw_candidates and current_results:
        hotlist_items = _flatten_hotlist_results(
            current_results=current_results,
            id_to_name=id_to_name or {},
            per_source_limit=per_source_limit,
            drop_low_signal_titles=drop_low_signal_titles,
        )
    elif fallback_stats:
        hotlist_items = _flatten_stats(fallback_stats, drop_low_signal_titles)

    if include_rss:
        if use_raw_candidates and raw_rss_items:
            rss_items = _flatten_rss_items(
                raw_rss_items=raw_rss_items,
                per_source_limit=per_source_limit,
                drop_low_signal_titles=drop_low_signal_titles,
            )
        elif fallback_rss_items:
            rss_items = _flatten_rss_list(fallback_rss_items, drop_low_signal_titles)

    merged = hotlist_items + rss_items
    if not merged:
        return []

    candidates = _merge_candidates(
        merged,
        dedupe_enabled=dedupe_enabled,
        max_evidence=max_evidence,
    )
    candidates.sort(
        key=lambda item: (
            -int(item.get("source_count", 0)),
            item.get("best_rank", 9999),
            -int(item.get("hotlist_count", 0)),
            -int(item.get("rss_count", 0)),
            item.get("title", ""),
        )
    )
    return candidates[:max_candidates]


def _flatten_hotlist_results(
    current_results: Dict,
    id_to_name: Dict,
    per_source_limit: int,
    drop_low_signal_titles: bool,
) -> List[Dict]:
    items: List[Dict] = []
    for source_id, titles_data in (current_results or {}).items():
        source_name = id_to_name.get(source_id, source_id)
        source_items: List[Dict] = []
        for title, title_data in (titles_data or {}).items():
            clean_title = str(title or "").strip()
            if not clean_title:
                continue
            if drop_low_signal_titles and _is_low_signal_title(clean_title):
                continue

            ranks = list(title_data.get("ranks", []) or [])
            best_rank = min(ranks) if ranks else 9999
            source_items.append(
                {
                    "title": clean_title,
                    "source_type": "hotlist",
                    "source_id": source_id,
                    "source_name": source_name,
                    "url": title_data.get("url", "") or title_data.get("mobileUrl", ""),
                    "mobile_url": title_data.get("mobileUrl", ""),
                    "published_at": "",
                    "summary": "",
                    "author": "",
                    "ranks": ranks,
                    "best_rank": best_rank,
                    "evidence_title": clean_title,
                }
            )

        source_items.sort(key=lambda item: item.get("best_rank", 9999))
        items.extend(source_items[:per_source_limit])
    return items


def _flatten_rss_items(
    raw_rss_items: List[Dict],
    per_source_limit: int,
    drop_low_signal_titles: bool,
) -> List[Dict]:
    items: List[Dict] = []
    per_feed_counter: Dict[str, int] = {}
    for item in raw_rss_items or []:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        if drop_low_signal_titles and _is_low_signal_title(title):
            continue

        feed_id = item.get("feed_id", "") or item.get("feed_name", "") or "rss"
        used = per_feed_counter.get(feed_id, 0)
        if used >= per_source_limit:
            continue

        per_feed_counter[feed_id] = used + 1
        items.append(
            {
                "title": title,
                "source_type": "rss",
                "source_id": feed_id,
                "source_name": item.get("feed_name", "") or feed_id,
                "url": item.get("url", ""),
                "mobile_url": "",
                "published_at": item.get("published_at", ""),
                "summary": item.get("summary", "") or "",
                "author": item.get("author", "") or "",
                "ranks": [],
                "best_rank": 9999,
                "evidence_title": title,
            }
        )
    return items


def _flatten_stats(stats: List[Dict], drop_low_signal_titles: bool) -> List[Dict]:
    items: List[Dict] = []
    for stat in stats or []:
        word = stat.get("word", "")
        for title_item in stat.get("titles", []) or []:
            title = str(title_item.get("title", "")).strip()
            if not title:
                continue
            if drop_low_signal_titles and _is_low_signal_title(title):
                continue

            ranks = list(title_item.get("ranks", []) or [])
            items.append(
                {
                    "title": title,
                    "source_type": title_item.get("source_type", "hotlist"),
                    "source_id": title_item.get("source", "") or word,
                    "source_name": title_item.get("source_name", "") or title_item.get("source", "") or "unknown",
                    "url": title_item.get("url", ""),
                    "mobile_url": title_item.get("mobileUrl", ""),
                    "published_at": title_item.get("published_at", ""),
                    "summary": title_item.get("summary", "") or "",
                    "author": title_item.get("author", "") or "",
                    "ranks": ranks,
                    "best_rank": min(ranks) if ranks else 9999,
                    "evidence_title": title,
                }
            )
    return items


def _flatten_rss_list(rss_items: List[Dict], drop_low_signal_titles: bool) -> List[Dict]:
    items: List[Dict] = []
    for item in rss_items or []:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        if drop_low_signal_titles and _is_low_signal_title(title):
            continue

        items.append(
            {
                "title": title,
                "source_type": "rss",
                "source_id": item.get("feed_id", "") or item.get("feed_name", "") or "rss",
                "source_name": item.get("feed_name", "") or item.get("source_name", "") or "rss",
                "url": item.get("url", ""),
                "mobile_url": "",
                "published_at": item.get("published_at", ""),
                "summary": item.get("summary", "") or "",
                "author": item.get("author", "") or "",
                "ranks": [],
                "best_rank": 9999,
                "evidence_title": title,
            }
        )
    return items


def _merge_candidates(
    items: List[Dict],
    dedupe_enabled: bool,
    max_evidence: int,
) -> List[Dict]:
    if not dedupe_enabled:
        return [_to_candidate(item) for item in items]

    merged: Dict[str, Dict] = {}
    for item in items:
        key = _build_candidate_key(item)
        if key not in merged:
            merged[key] = _to_candidate(item)
            continue

        target = merged[key]
        target["source_ids"].add(item.get("source_id", ""))
        target["source_names"].add(item.get("source_name", ""))
        target["urls"].update(filter(None, [item.get("url", ""), item.get("mobile_url", "")]))
        if item.get("source_type") == "hotlist":
            target["hotlist_count"] += 1
        else:
            target["rss_count"] += 1

        target["best_rank"] = min(target.get("best_rank", 9999), item.get("best_rank", 9999))
        if item.get("published_at") and (
            not target.get("published_at") or str(item.get("published_at")) > str(target.get("published_at"))
        ):
            target["published_at"] = item.get("published_at")

        evidence_title = item.get("evidence_title", "")
        if evidence_title and evidence_title not in target["evidence_titles"] and len(target["evidence_titles"]) < max_evidence:
            target["evidence_titles"].append(evidence_title)

        if item.get("summary") and not target.get("summary"):
            target["summary"] = item.get("summary")
        if item.get("author") and not target.get("author"):
            target["author"] = item.get("author")
        if len(item.get("title", "")) > len(target.get("title", "")):
            target["title"] = item.get("title", "")
        target["source_count"] = len(target["source_names"])

    final_items: List[Dict] = []
    for candidate in merged.values():
        candidate["source_ids"] = sorted(filter(None, candidate["source_ids"]))
        candidate["source_names"] = sorted(filter(None, candidate["source_names"]))
        candidate["urls"] = sorted(filter(None, candidate["urls"]))
        candidate["source_count"] = len(candidate["source_names"])
        final_items.append(candidate)
    return final_items


def _to_candidate(item: Dict) -> Dict:
    source_type = item.get("source_type", "hotlist")
    return {
        "title": item.get("title", ""),
        "source_type": source_type,
        "source_ids": {item.get("source_id", "")},
        "source_names": {item.get("source_name", "")},
        "source_count": 1,
        "urls": set(filter(None, [item.get("url", ""), item.get("mobile_url", "")])),
        "published_at": item.get("published_at", ""),
        "summary": item.get("summary", "") or "",
        "author": item.get("author", "") or "",
        "best_rank": item.get("best_rank", 9999),
        "hotlist_count": 1 if source_type == "hotlist" else 0,
        "rss_count": 1 if source_type == "rss" else 0,
        "evidence_titles": [item.get("evidence_title", "")] if item.get("evidence_title", "") else [],
    }


def _build_candidate_key(item: Dict) -> str:
    title_key = _normalize_title(item.get("title", ""))
    if title_key:
        return f"title:{title_key}"
    url = _normalize_url(item.get("url", "") or item.get("mobile_url", ""))
    if url:
        return f"url:{url}"
    return "unknown"


def _normalize_title(title: str) -> str:
    text = str(title or "").strip().lower()
    for pattern in _LOW_SIGNAL_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    text = re.sub(r"[\s\-\|\[\]【】()（）:：!！?？,，。、“”\"'`]+", "", text)
    return text


def _normalize_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    return text.split("#", 1)[0]


def _is_low_signal_title(title: str) -> bool:
    text = str(title or "").strip()
    if len(text) < 6:
        return True
    return any(re.match(pattern, text, flags=re.IGNORECASE) for pattern in _LOW_SIGNAL_PATTERNS)
