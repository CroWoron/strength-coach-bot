from __future__ import annotations

from .coach import Coach
from .config import ROOT, Settings
from .database import Database
from .knowledge import KnowledgeBase
from .llm import LLMClient


def build_app(settings: Settings | None = None) -> tuple[Coach, Settings, Database]:
    settings = settings or Settings.from_env()
    database = Database(settings.data_dir / "coach.sqlite3")
    knowledge = KnowledgeBase(ROOT / "knowledge.json")
    llm = LLMClient(
        settings.llm_api_url,
        settings.llm_api_key,
        settings.llm_model,
        fallback_api_url=settings.llm_fallback_api_url,
        fallback_api_key=settings.llm_fallback_api_key,
        fallback_model=settings.llm_fallback_model,
    )
    return Coach(database, knowledge, llm), settings, database
