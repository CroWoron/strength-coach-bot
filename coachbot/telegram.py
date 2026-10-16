from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .coach import Coach
from .database import Database


class TelegramBot:
    def __init__(self, token: str, allowed_users: frozenset[int], coach: Coach, database: Database):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.allowed_users = allowed_users
        self.coach = coach
        self.db = database

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/{method}",
            data=urlencode(params).encode("utf-8"),
            method="POST",
        )
        try:
            with urlopen(request, timeout=70) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Telegram request failed: {error}") from error
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error: {payload.get('description', 'unknown error')}")
        return payload["result"]

    def send(self, chat_id: int, text: str) -> None:
        remaining = text.strip()
        while remaining:
            if len(remaining) <= 4000:
                part, remaining = remaining, ""
            else:
                split = remaining.rfind("\n", 0, 4000)
                split = split if split > 1000 else 4000
                part, remaining = remaining[:split], remaining[split:].lstrip()
            self.call("sendMessage", chat_id=chat_id, text=part)

    def allowed(self, message: dict[str, Any]) -> bool:
        sender_id = int(message.get("from", {}).get("id", 0))
        chat_type = message.get("chat", {}).get("type")
        if self.allowed_users:
            return sender_id in self.allowed_users
        return chat_type == "private"

    def process(self, update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message or not message.get("text") or not self.allowed(message):
            return
        sender = message.get("from", {})
        chat_id = int(message["chat"]["id"])
        text = str(message["text"]).strip()
        if text == "/start":
            text = "Начинаем. Объясни, какие данные тебе нужны для составления моей тренировочной программы."
        display_name = " ".join(
            part for part in [sender.get("first_name", ""), sender.get("last_name", "")] if part
        ) or str(sender.get("username") or sender.get("id"))
        self.call("sendChatAction", chat_id=chat_id, action="typing")
        answer = self.coach.handle_message(
            platform="telegram",
            external_user_id=str(sender["id"]),
            display_name=display_name,
            event_id=f"{chat_id}:{message['message_id']}",
            text=text,
        )
        if answer:
            self.send(chat_id, answer)

    def run(self) -> None:
        offset = int(self.db.metadata("telegram_offset", "0") or 0)
        logging.info("Strength coach Telegram bot started")
        while True:
            try:
                updates = self.call(
                    "getUpdates",
                    offset=offset,
                    timeout=50,
                    allowed_updates=json.dumps(["message", "edited_message"]),
                )
                for update in updates:
                    offset = max(offset, int(update["update_id"]) + 1)
                    self.db.set_metadata("telegram_offset", str(offset))
                    try:
                        self.process(update)
                    except Exception:
                        logging.exception("Failed to process Telegram update %s", update.get("update_id"))
            except Exception:
                logging.exception("Telegram polling failed")
                time.sleep(3)
