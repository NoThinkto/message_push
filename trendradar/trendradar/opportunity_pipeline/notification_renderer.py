# coding=utf-8
"""
机会识别流程 - 通知文案渲染器
新流程强调“机会本身”和“中文分析逻辑”，因此文案按单条机会逐一展开。
"""

from html import escape
from typing import Dict, List


def render_opportunity_plain(report_data: Dict) -> str:
    """渲染为纯文本格式，便于企业微信 text 等渠道使用。"""
    meta = report_data.get("meta", {})
    opportunity = report_data.get("opportunity", {})
    top_opportunities = report_data.get("top_opportunities", []) or []

    lines = [
        "【机会预警】",
        f"候选事件：{meta.get('candidate_count', 0)} 条",
        f"入模分析：{meta.get('analyzed_news_count', 0)} 条",
        f"筛出机会：{meta.get('selected_opportunity_count', len(top_opportunities))} 条",
    ]

    overall_summary = opportunity.get("overall_summary") or opportunity.get("summary")
    overall_reason = opportunity.get("overall_reason") or opportunity.get("reason")
    overall_confidence = opportunity.get("overall_confidence", opportunity.get("confidence", 0.0))
    if overall_summary:
        lines.append(f"整体判断：{overall_summary}")
    if overall_reason:
        lines.append(f"整体逻辑：{overall_reason}")
    lines.append(f"整体置信度：{overall_confidence:.2f}")

    if top_opportunities:
        for idx, item in enumerate(top_opportunities, 1):
            lines.append("")
            lines.extend(_render_plain_signal(idx, item))
    else:
        lines.append("")
        lines.append("本轮未筛出达到推送门槛的具体机会。")

    if opportunity.get("error"):
        lines.append("")
        lines.append(f"【分析备注】{opportunity.get('error')}")

    return "\n".join(lines)


def render_opportunity_markdown(report_data: Dict) -> str:
    """渲染为 Markdown 格式，便于 Telegram 等渠道使用。"""
    meta = report_data.get("meta", {})
    opportunity = report_data.get("opportunity", {})
    top_opportunities = report_data.get("top_opportunities", []) or []

    lines = [
        "**机会预警**",
        "",
        f"- 候选事件：{meta.get('candidate_count', 0)} 条",
        f"- 入模分析：{meta.get('analyzed_news_count', 0)} 条",
        f"- 筛出机会：{meta.get('selected_opportunity_count', len(top_opportunities))} 条",
    ]

    overall_summary = opportunity.get("overall_summary") or opportunity.get("summary")
    overall_reason = opportunity.get("overall_reason") or opportunity.get("reason")
    overall_confidence = opportunity.get("overall_confidence", opportunity.get("confidence", 0.0))
    if overall_summary:
        lines.append(f"- 整体判断：{overall_summary}")
    if overall_reason:
        lines.append(f"- 整体逻辑：{overall_reason}")
    lines.append(f"- 整体置信度：{overall_confidence:.2f}")

    if top_opportunities:
        for idx, item in enumerate(top_opportunities, 1):
            lines.append("")
            lines.extend(_render_markdown_signal(idx, item))
    else:
        lines.append("")
        lines.append("本轮未筛出达到推送门槛的具体机会。")

    if opportunity.get("error"):
        lines.append("")
        lines.append(f"**分析备注**：{opportunity.get('error')}")

    return "\n".join(lines)


def render_opportunity_wework_text(report_data: Dict) -> str:
    """企业微信 opportunity 专用模板。"""
    meta = report_data.get("meta", {})
    top_opportunities = report_data.get("top_opportunities", []) or []
    push_sequence = meta.get("push_sequence", "?")

    lines = [
        f"--------------------------第{push_sequence}次推送-----------------------------------------",
        f"候选事件：{meta.get('candidate_count', 0)} 条",
        f"入模分析：{meta.get('analyzed_news_count', 0)} 条",
        f"筛出机会：{meta.get('selected_opportunity_count', len(top_opportunities))} 条",
    ]

    total = len(top_opportunities)
    if top_opportunities:
        for idx, item in enumerate(top_opportunities, 1):
            lines.append("")
            lines.append(f"[第 {idx}/{total} 批次]")
            lines.extend(_render_plain_signal(idx, item))
    else:
        lines.append("")
        lines.append("[第 0/0 批次]")
        lines.append("本轮未筛出达到推送门槛的具体机会。")

    opportunity = report_data.get("opportunity", {})
    if opportunity.get("error"):
        lines.append("")
        lines.append(f"【分析备注】{opportunity.get('error')}")

    return "\n".join(lines)


def render_opportunity_html(report_data: Dict, report_type: str = "机会预警") -> str:
    """渲染轻量 HTML 页面，供邮件渠道复用。"""
    meta = report_data.get("meta", {})
    opportunity = report_data.get("opportunity", {})
    top_opportunities = report_data.get("top_opportunities", []) or []

    overall_summary = opportunity.get("overall_summary") or opportunity.get("summary") or "未提供"
    overall_reason = opportunity.get("overall_reason") or opportunity.get("reason") or "未提供"
    overall_confidence = opportunity.get("overall_confidence", opportunity.get("confidence", 0.0))

    cards_html = "".join(_render_signal_card_html(idx, item) for idx, item in enumerate(top_opportunities, 1))
    if not cards_html:
        cards_html = (
            "<div style='padding:18px;border:1px solid #e2e8f0;border-radius:12px;background:#f8fafc;color:#475569;'>"
            "本轮未筛出达到推送门槛的具体机会。"
            "</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(report_type)}</title>
</head>
<body style="margin:0;background:#f5f7fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:860px;margin:0 auto;padding:24px;">
    <div style="background:#ffffff;border-radius:16px;padding:28px;box-shadow:0 8px 30px rgba(15,23,42,0.08);">
      <div style="font-size:13px;color:#64748b;letter-spacing:0.04em;">TrendRadar Opportunity Pipeline</div>
      <h1 style="margin:8px 0 4px;font-size:28px;color:#0f172a;">{escape(report_type)}</h1>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:20px 0;">
        {_render_metric_card('候选事件', str(meta.get('candidate_count', 0)))}
        {_render_metric_card('入模分析', str(meta.get('analyzed_news_count', 0)))}
        {_render_metric_card('筛出机会', str(meta.get('selected_opportunity_count', len(top_opportunities))))}
        {_render_metric_card('整体置信度', f"{overall_confidence:.2f}")}
      </div>

      <section style="margin-bottom:20px;">
        <h2 style="font-size:18px;color:#0f172a;margin:0 0 8px;">整体判断</h2>
        <p style="margin:0 0 10px;line-height:1.7;color:#334155;">{escape(overall_summary)}</p>
        <p style="margin:0;line-height:1.8;color:#475569;white-space:pre-wrap;">{escape(overall_reason)}</p>
      </section>

      <section>
        <h2 style="font-size:18px;color:#0f172a;margin:0 0 12px;">可执行机会</h2>
        {cards_html}
      </section>

      {_render_optional_error(opportunity.get('error'))}
    </div>
  </div>
</body>
</html>
"""


def _render_plain_signal(index: int, item: Dict) -> List[str]:
    lines = [
        f"【机会 {index}】{item.get('title') or item.get('summary') or '未命名机会'}",
        f"类型：{item.get('type') or 'other'}",
        f"置信度：{item.get('confidence', 0):.2f}",
        f"风险：{item.get('risk_level') or 'high'}",
    ]
    if item.get("summary"):
        lines.append(f"机会概述：{item.get('summary')}")
    if item.get("reason"):
        lines.append(f"分析逻辑：{item.get('reason')}")
    if item.get("targets"):
        lines.append(f"关注对象：{' / '.join(item.get('targets', []))}")
    if item.get("suggested_action"):
        lines.append(f"建议动作：{item.get('suggested_action')}")
    if item.get("related_titles"):
        lines.append("证据资讯：")
        for title in item.get("related_titles", []):
            lines.append(f"- {title}")
    return lines


def _render_markdown_signal(index: int, item: Dict) -> List[str]:
    lines = [
        f"**机会 {index}｜{item.get('title') or item.get('summary') or '未命名机会'}**",
        f"- 类型：{item.get('type') or 'other'}",
        f"- 置信度：{item.get('confidence', 0):.2f}",
        f"- 风险：{item.get('risk_level') or 'high'}",
    ]
    if item.get("summary"):
        lines.append(f"- 机会概述：{item.get('summary')}")
    if item.get("reason"):
        lines.append(f"- 分析逻辑：{item.get('reason')}")
    if item.get("targets"):
        lines.append(f"- 关注对象：{' / '.join(item.get('targets', []))}")
    if item.get("suggested_action"):
        lines.append(f"- 建议动作：{item.get('suggested_action')}")
    if item.get("related_titles"):
        lines.append("- 证据资讯：")
        for title in item.get("related_titles", []):
            lines.append(f"  - {title}")
    return lines


def _render_signal_card_html(index: int, item: Dict) -> str:
    tags_html = "".join(
        '<span style="display:inline-block;margin:4px 6px 0 0;padding:4px 10px;'
        'background:#eef4ff;border-radius:999px;color:#1f4db8;font-size:12px;">'
        f"{escape(str(target))}</span>"
        for target in item.get("targets", [])
    )
    related_titles_html = "".join(
        f"<li style='margin:6px 0;line-height:1.6;color:#334155;'>{escape(str(title))}</li>"
        for title in item.get("related_titles", [])
    )

    return (
        "<div style='margin-bottom:16px;padding:18px;border:1px solid #e2e8f0;border-radius:14px;background:#ffffff;'>"
        f"<h3 style='margin:0 0 10px;font-size:18px;color:#0f172a;'>机会 {index}｜{escape(item.get('title') or item.get('summary') or '未命名机会')}</h3>"
        "<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:12px;'>"
        + _render_metric_card("类型", item.get("type") or "other")
        + _render_metric_card("置信度", f"{item.get('confidence', 0):.2f}")
        + _render_metric_card("风险", item.get("risk_level") or "high")
        + "</div>"
        + f"<p style='margin:0 0 8px;line-height:1.7;color:#334155;'><strong>机会概述：</strong>{escape(item.get('summary') or '未提供')}</p>"
        + f"<p style='margin:0 0 8px;line-height:1.8;color:#334155;white-space:pre-wrap;'><strong>分析逻辑：</strong>{escape(item.get('reason') or '未提供')}</p>"
        + f"<p style='margin:0 0 8px;line-height:1.7;color:#334155;'><strong>建议动作：</strong>{escape(item.get('suggested_action') or '未提供')}</p>"
        + "<div style='margin:8px 0 10px;'><strong style='color:#0f172a;'>关注对象：</strong><div>"
        + (tags_html or "<span style='color:#64748b;'>未提供</span>")
        + "</div></div>"
        + (
            "<div><strong style='color:#0f172a;'>证据资讯：</strong><ul style='margin:8px 0 0;padding-left:20px;'>"
            + related_titles_html
            + "</ul></div>"
            if related_titles_html else ""
        )
        + "</div>"
    )


def _render_metric_card(label: str, value: str) -> str:
    return (
        '<div style="padding:14px 16px;border:1px solid #e2e8f0;border-radius:12px;background:#f8fafc;">'
        f'<div style="font-size:12px;color:#64748b;">{escape(label)}</div>'
        f'<div style="margin-top:6px;font-size:16px;color:#0f172a;font-weight:600;">{escape(value)}</div>'
        "</div>"
    )


def _render_optional_error(error_text: str) -> str:
    if not error_text:
        return ""
    return (
        '<section style="margin-top:18px;padding:16px;border-radius:12px;'
        'background:#fff7ed;border:1px solid #fed7aa;">'
        '<h2 style="font-size:16px;color:#9a3412;margin:0 0 8px;">分析备注</h2>'
        f'<p style="margin:0;line-height:1.7;color:#7c2d12;">{escape(error_text)}</p>'
        '</section>'
    )
