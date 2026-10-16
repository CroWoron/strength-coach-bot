from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class KnowledgeBase:
    def __init__(self, path: Path):
        self.items: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        self.by_id = {item["id"]: item for item in self.items}

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        terms = {term.casefold() for term in query.replace(",", " ").split() if len(term) > 2}
        ranked = []
        for item in self.items:
            haystack = " ".join(
                [item["title"], item["summary"], item["scope"], " ".join(item.get("tags", []))]
            ).casefold()
            score = sum(term in haystack for term in terms)
            if score or not terms:
                ranked.append((score, item))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        return [item for _, item in ranked[: max(1, min(int(limit), 10))]]

    def resolve(self, identifiers: list[str]) -> list[dict[str, Any]]:
        return [self.by_id[item] for item in identifiers if item in self.by_id]
