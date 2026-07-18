"""Telegram Bot API — last-resort fatal-crash alert channel.

Normal run results ship to the ark-agent-fleet journal, not here. This
channel is used only when the pipeline crashes outright: if the ark/VPS
itself is down the fleet report may not deliver, so a fatal crash still
pings Telegram as an out-of-band safety net.
"""

from __future__ import annotations

from .retry import with_backoff

_SEND = "https://api.telegram.org/bot{token}/sendMessage"
_EMOJI = {"info": "✅", "warn": "⚠️", "hard": "🚨"}


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, logger=None):
        self._token = bot_token
        self._chat_id = chat_id
        self._log = logger

    def send(self, text: str, *, level: str = "info") -> None:
        """Send a text alert. Never raises — a dead alert channel must not
        abort a publish run (it is logged instead)."""
        body = f"{_EMOJI.get(level, 'ℹ️')} {text}"

        def _post():
            import requests

            r = requests.post(
                _SEND.format(token=self._token),
                json={"chat_id": self._chat_id, "text": body,
                      "disable_web_page_preview": True},
                timeout=15,
            )
            r.raise_for_status()
            return r

        try:
            with_backoff(_post, attempts=3, logger=self._log, label="telegram")
        except Exception as exc:  # noqa: BLE001
            if self._log:
                self._log.error("telegram alert dropped: %s | text=%s", exc, text)
