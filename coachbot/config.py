from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    telegram_token: str
    telegram_allowed_users: frozenset[int]
    llm_api_url: str
    llm_api_key: str
    llm_model: str
    llm_fallback_api_url: str
    llm_fallback_api_key: str
    llm_fallback_model: str

    @classmethod
    def from_env(cls) -> "Settings":
        groq_key = os.getenv("GROQ_API_KEY", "")
        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        explicit_key = os.getenv("LLM_API_KEY", "")
        if explicit_key:
            api_url = os.getenv("LLM_API_URL", "https://api.groq.com/openai/v1/chat/completions")
            model = os.getenv("LLM_MODEL", "qwen/qwen3.8-27b")
            api_key = explicit_key
            fallback_url = ""
            fallback_key = ""
            fallback_model = ""
        elif deepseek_key:
            api_url = "https://api.deepseek.com/chat/completions"
            model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
            api_key = deepseek_key
            fallback_url = "https://api.groq.com/openai/v1/chat/completions" if groq_key else ""
            fallback_key = groq_key
            fallback_model = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b") if groq_key else ""
        else:
            api_url = "https://api.groq.com/openai/v1/chat/completions"
            model = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
            api_key = groq_key
            fallback_url = ""
            fallback_key = ""
            fallback_model = ""
        allowed = frozenset(
            int(item) for item in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",")
            if item.strip().lstrip("-").isdigit()
        )
        return cls(
            data_dir=Path(os.getenv("DATA_DIR", ROOT / "data")),
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_allowed_users=allowed,
            llm_api_url=api_url,
            llm_api_key=api_key,
            llm_model=model,
            llm_fallback_api_url=fallback_url,
            llm_fallback_api_key=fallback_key,
            llm_fallback_model=fallback_model,
        )
