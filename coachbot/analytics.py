from __future__ import annotations

import statistics
import time
from collections import defaultdict
from typing import Any

from .engine import epley_with_rir


def summarize(sessions: list[dict[str, Any]], now: int | None = None) -> dict[str, Any]:
    estimates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    session_summaries = []
    seven_days_ago = int(now if now is not None else time.time()) - 7 * 24 * 60 * 60
    weekly_session_load = 0.0

    for session in reversed(sessions):
        working_tonnage = 0.0
        for item in session.get("sets", []):
            if item.get("actual_weight_kg") is None:
                continue
            load = float(item["actual_weight_kg"])
            reps = int(item["actual_reps"])
            working_tonnage += 0 if item.get("is_warmup") else load * reps
            if (
                not item.get("is_warmup")
                and bool(item.get("technique_ok"))
                and not bool(item.get("pain"))
                and 1 <= reps <= 12
                and item.get("actual_rir") is not None
                and 0 <= float(item["actual_rir"]) <= 5
            ):
                bodyweight = float(item.get("bodyweight_kg") or 0)
                estimate = epley_with_rir(load + bodyweight, reps, float(item["actual_rir"]))
                estimates[item["exercise_key"]].append(
                    {
                        "exercise": item["exercise_name"],
                        "e1rm_kg": round(estimate, 1),
                        "added_load_only": bool(bodyweight),
                        "completed_at": item.get("completed_at"),
                    }
                )
        duration = session.get("duration_minutes")
        rpe = session.get("session_rpe")
        if (session.get("completed_at") or 0) >= seven_days_ago and duration is not None and rpe is not None:
            weekly_session_load += float(duration) * float(rpe)
        session_summaries.append(
            {
                "session_id": session["id"],
                "type": session["session_type"],
                "status": session["status"],
                "working_tonnage_kg": round(working_tonnage, 1),
                "duration_minutes": duration,
                "session_rpe": rpe,
            }
        )

    strength = []
    for values in estimates.values():
        last_three = values[-3:]
        strength.append(
            {
                "exercise": values[-1]["exercise"],
                "latest_e1rm_kg": values[-1]["e1rm_kg"],
                "median_last_3_e1rm_kg": round(statistics.median(item["e1rm_kg"] for item in last_three), 1),
                "quality_observations": len(values),
                "metric_note": "total system mass" if values[-1]["added_load_only"] else "external load",
            }
        )

    return {
        "strength_estimates": strength,
        "recent_session_summaries": session_summaries,
        "seven_day_session_load_au": round(weekly_session_load, 1),
        "caution": "Trends are observations, not proof of causation or an overtraining diagnosis.",
    }
