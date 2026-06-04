"""Telegram Bot API alert channel."""

from __future__ import annotations

from pathlib import Path

from .retry import with_backoff

_SEND = "https://api.telegram.org/bot{token}/sendMessage"
_DOC = "https://api.telegram.org/bot{token}/sendDocument"
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

    def send_document(self, file_path, *, caption: str = "",
                      level: str = "hard") -> None:
        """Attach a file (e.g. the run log). Never raises."""
        path = Path(file_path)
        cap = f"{_EMOJI.get(level, 'ℹ️')} {caption}".strip()[:1024]

        def _post():
            import requests

            with path.open("rb") as fh:
                r = requests.post(
                    _DOC.format(token=self._token),
                    data={"chat_id": self._chat_id, "caption": cap},
                    files={"document": (path.name, fh)},
                    timeout=30,
                )
            r.raise_for_status()
            return r

        try:
            if not path.is_file():
                raise FileNotFoundError(path)
            with_backoff(_post, attempts=2, logger=self._log,
                         label="telegram-doc")
        except Exception as exc:  # noqa: BLE001
            if self._log:
                self._log.error("telegram document dropped: %s | file=%s",
                                exc, file_path)
