# coding=utf-8
"""
机会识别流程 - 通知分发器

设计重点：
1. 文案渲染走新流程；
2. 底层发送继续复用原有 sender；
3. 渠道启用既受全局通知配置约束，也受 opportunity_pipeline.channels 约束；
4. 邮件渠道使用新流程自己的轻量 HTML 页面，不依赖旧的完整日报模板。
"""

from tempfile import NamedTemporaryFile
from typing import Dict, List

from trendradar.core.config import (
    get_account_at_index,
    limit_accounts,
    parse_multi_account_config,
    validate_paired_configs,
)
from trendradar.notification.senders import (
    send_to_email,
    send_to_telegram,
    send_to_wework,
)

from .notification_renderer import (
    render_opportunity_html,
    render_opportunity_markdown,
    render_opportunity_plain,
    render_opportunity_wework_text,
)


class OpportunityNotificationDispatcher:
    """新流程专用通知分发器。"""

    def __init__(self, config: Dict, proxy_url: str = "", storage_manager=None):
        self.config = config
        self.proxy_url = proxy_url
        self.storage_manager = storage_manager
        self.pipeline_cfg = config.get("OPPORTUNITY_PIPELINE", {})
        self.channel_cfg = self.pipeline_cfg.get("CHANNELS", {})
        self.max_accounts = config.get("MAX_ACCOUNTS_PER_CHANNEL", 3)

    def dispatch(self, report_data: Dict, report_type: str = "套利机会预警") -> Dict[str, bool]:
        """分发机会预警通知。"""
        if not self.config.get("ENABLE_NOTIFICATION", True):
            print("[机会推送] 全局通知开关已关闭，跳过发送")
            return {}

        results: Dict[str, bool] = {}
        should_send_wework = self.channel_cfg.get("WEWORK", True) and self.config.get("WEWORK_WEBHOOK_URL")
        should_send_telegram = False
        if self.channel_cfg.get("TELEGRAM", False):
            should_send_telegram = bool(
                self.config.get("TELEGRAM_BOT_TOKEN", "") and self.config.get("TELEGRAM_CHAT_ID", "")
            )
        should_send_email = False
        if self.channel_cfg.get("EMAIL", False):
            should_send_email = bool(
                self.config.get("EMAIL_FROM") and self.config.get("EMAIL_PASSWORD") and self.config.get("EMAIL_TO")
            )

        if not (should_send_wework or should_send_telegram or should_send_email):
            return results

        self._assign_push_sequence(report_data)

        if should_send_wework:
            results["wework"] = self._send_wework(report_data, report_type)

        if should_send_telegram:
            results["telegram"] = self._send_telegram(report_data, report_type)

        if should_send_email:
            results["email"] = self._send_email(report_data, report_type)

        return results

    def _assign_push_sequence(self, report_data: Dict) -> None:
        """为本次 opportunity 推送分配当天稳定序号。"""
        meta = report_data.setdefault("meta", {})
        if meta.get("push_sequence"):
            return

        try:
            storage = self.storage_manager or self.config.get("_STORAGE_MANAGER")
            if storage is None:
                # Dispatcher 只持有 config；正常运行时从 AppContext 创建的 manager
                # 不直接挂在 config 上，因此这里延迟导入并复用全局单例。
                from trendradar.storage import get_storage_manager

                storage = get_storage_manager()
            sequence = storage.next_opportunity_push_sequence()
        except Exception as exc:
            print(f"[机会推送] 获取当天推送序号失败：{type(exc).__name__}: {exc}")
            sequence = 0

        meta["push_sequence"] = sequence or "?"

    def _send_wework(self, report_data: Dict, report_type: str) -> bool:
        """复用原有企业微信 sender，支持多账号。"""
        webhooks = parse_multi_account_config(self.config.get("WEWORK_WEBHOOK_URL", ""))
        if not webhooks:
            return False

        webhooks = limit_accounts(webhooks, self.max_accounts, "企业微信")
        msg_type = self.config.get("WEWORK_MSG_TYPE", "markdown")
        results: List[bool] = []

        for index, webhook_url in enumerate(webhooks):
            account_label = f"[账号{index + 1}]" if len(webhooks) > 1 else ""
            ok = send_to_wework(
                webhook_url=webhook_url,
                report_data=report_data,
                report_type=report_type,
                proxy_url=self.proxy_url,
                mode="opportunity",
                account_label=account_label,
                msg_type=msg_type,
                split_content_func=self._split_content,
            )
            results.append(ok)

        return any(results)

    def _send_telegram(self, report_data: Dict, report_type: str) -> bool:
        """复用原有 Telegram sender，支持 token/chat_id 配对。"""
        bot_tokens = parse_multi_account_config(self.config.get("TELEGRAM_BOT_TOKEN", ""))
        chat_ids = parse_multi_account_config(self.config.get("TELEGRAM_CHAT_ID", ""))
        valid, count = validate_paired_configs(
            {"bot_token": bot_tokens, "chat_id": chat_ids},
            "Telegram",
            required_keys=["bot_token", "chat_id"],
        )
        if not valid or count == 0:
            return False

        limited_count = min(count, self.max_accounts)
        results: List[bool] = []
        for index in range(limited_count):
            bot_token = get_account_at_index(bot_tokens, index, "")
            chat_id = get_account_at_index(chat_ids, index, "")
            account_label = f"[账号{index + 1}]" if limited_count > 1 else ""
            ok = send_to_telegram(
                bot_token=bot_token,
                chat_id=chat_id,
                report_data=report_data,
                report_type=report_type,
                proxy_url=self.proxy_url,
                mode="opportunity",
                account_label=account_label,
                split_content_func=self._split_content,
            )
            results.append(ok)
        return any(results)

    def _send_email(self, report_data: Dict, report_type: str) -> bool:
        """
        为新流程生成一份轻量 HTML 文件，再复用旧邮件 sender 发送。
        这样可以让邮件渠道与企业微信/Telegram 一样走独立机会文案。
        """
        html_file_path = self._write_email_html(report_data, report_type)
        report_data["html_file_path"] = html_file_path

        return send_to_email(
            from_email=self.config.get("EMAIL_FROM", ""),
            password=self.config.get("EMAIL_PASSWORD", ""),
            to_email=self.config.get("EMAIL_TO", ""),
            report_type=report_type,
            html_file_path=html_file_path,
            custom_smtp_server=self.config.get("EMAIL_SMTP_SERVER"),
            custom_smtp_port=self.config.get("EMAIL_SMTP_PORT"),
        )

    def _write_email_html(self, report_data: Dict, report_type: str) -> str:
        """生成轻量 HTML 文件，并返回文件路径。"""
        html_content = render_opportunity_html(report_data, report_type=report_type)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".html",
            prefix="trendradar_opportunity_",
            delete=False,
        ) as temp_file:
            temp_file.write(html_content)
            return temp_file.name

    def _split_content(
        self,
        report_data: Dict,
        format_type: str,
        update_info=None,
        max_bytes: int = 4000,
        **kwargs,
    ) -> List[str]:
        """为原有 sender 提供新流程自己的分片函数。"""
        _ = (update_info, kwargs)
        content = self._render_content(report_data, format_type)
        return self._chunk_text(content, max_bytes)

    def _render_content(self, report_data: Dict, format_type: str) -> str:
        """根据渠道格式类型选择对应渲染器。"""
        if format_type == "telegram":
            return render_opportunity_markdown(report_data)
        if format_type == "wework":
            return render_opportunity_wework_text(report_data)
        return render_opportunity_plain(report_data)

    def _chunk_text(self, content: str, max_bytes: int) -> List[str]:
        """按行优先切分内容，尽量保持阅读连续性。"""
        if not content:
            return [""]

        lines = content.splitlines()
        chunks: List[str] = []
        current: List[str] = []

        for line in lines:
            candidate = "\n".join(current + [line]) if current else line
            if len(candidate.encode("utf-8")) <= max_bytes:
                current.append(line)
                continue

            if current:
                chunks.append("\n".join(current))
                current = []

            if len(line.encode("utf-8")) <= max_bytes:
                current = [line]
            else:
                chunks.extend(self._split_long_line(line, max_bytes))

        if current:
            chunks.append("\n".join(current))

        return chunks or [content]

    def _split_long_line(self, line: str, max_bytes: int) -> List[str]:
        """兜底处理超长单行文本。"""
        parts: List[str] = []
        current = ""
        for char in line:
            candidate = current + char
            if len(candidate.encode("utf-8")) <= max_bytes:
                current = candidate
            else:
                if current:
                    parts.append(current)
                current = char
        if current:
            parts.append(current)
        return parts
