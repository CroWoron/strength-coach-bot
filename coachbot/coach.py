from __future__ import annotations

import json
import logging
import re
from typing import Any

from .database import Database, exercise_key
from .analytics import summarize
from .engine import ValidationError, adjust_after_set, validate_set_plan
from .exercises import catalog_for_llm, exercise_profile
from .knowledge import KnowledgeBase
from .llm import LLMClient, LLMError


CONFIRMATION = re.compile(
    r"(?:^|\b)(да|согласен|согласна|подтверждаю|активируй|применяй|принимаю)(?:\b|$)", re.IGNORECASE
)
TIME_LIMIT = re.compile(r"не успева|нет времени|мало времени|спеш|надо уход|пора уход|остал(?:ось|о)\s+\d+\s*мин", re.IGNORECASE)
RIR_TERM = re.compile(r"\bRIR(?:\s*[:=]?\s*(\d+(?:[.,]\d+)?))?", re.IGNORECASE)
FIXED_IMPLEMENT = re.compile(r"гир|гантел|медбол", re.IGNORECASE)
COMPOUND_EXERCISE = re.compile(
    r"станов|присед|жим|подтяг|тяга|выпад|брусь|deadlift|squat|bench|press|pull.?up|row|lunge",
    re.IGNORECASE,
)
DEADLIFT_EXERCISE = re.compile(r"станов|deadlift", re.IGNORECASE)


def is_explosive_rule(item: dict[str, Any]) -> bool:
    if item.get("target_rir") is not None:
        return False
    profile = exercise_profile(str(item.get("exercise") or ""))
    return not profile or bool(set(profile["tags"]) & {"power", "quality_limited"})


def is_demanding_strength_rule(item: dict[str, Any]) -> bool:
    target = item.get("target_rir")
    profile = exercise_profile(str(item.get("exercise") or ""))
    tags = set((profile or {}).get("tags", ()))
    compound = "compound" in tags or bool(COMPOUND_EXERCISE.search(str(item.get("exercise") or "")))
    maximum_reps = 8 if (profile or {}).get("systemic_fatigue") == "high" else 6
    return (
        target is not None
        and float(target) <= 2
        and int(item.get("reps_max", item.get("reps", 99))) <= maximum_reps
        and compound
    )


def is_deadlift_rule(item: dict[str, Any]) -> bool:
    profile = exercise_profile(str(item.get("exercise") or ""))
    return "deadlift" in str((profile or {}).get("id") or "") or bool(
        DEADLIFT_EXERCISE.search(str(item.get("exercise") or ""))
    )


def balance_program_day(day: dict[str, Any], boxing_sessions: int) -> set[str]:
    exercises = day.get("exercises", [])
    reasons: set[str] = set()
    ordered = [item for item in exercises if is_explosive_rule(item)] + [
        item for item in exercises if not is_explosive_rule(item)
    ]
    if ordered != exercises:
        day["exercises"] = exercises = ordered
        reasons.add("explosive_order")

    strength = [item for item in exercises if not is_explosive_rule(item)]
    demanding = [item for item in strength if is_demanding_strength_rule(item)]
    has_demanding_deadlift = any(is_deadlift_rule(item) for item in demanding)
    seen = 0
    for item in strength:
        if not is_demanding_strength_rule(item):
            continue
        seen += 1
        preserve = is_deadlift_rule(item) if has_demanding_deadlift else seen <= 2
        if preserve:
            continue
        previous_sets = int(item["sets"])
        previous_target = float(item["target_rir"])
        item["sets"] = min(previous_sets, 2)
        item["target_rir"] = max(previous_target, 3)
        if item["sets"] != previous_sets or item["target_rir"] != previous_target:
            reasons.add("combined_stress")

    name = str(day.get("name") or "").casefold().replace("ё", "е")
    explosive_count = sum(is_explosive_rule(item) for item in exercises)
    short_day = "коротк" in name or explosive_count * 2 >= len(exercises)
    if boxing_sessions >= 3 and short_day:
        boxing_stress_tags = {"axial", "posterior_chain", "grip", "shoulder", "soreness", "legs"}
        for item in strength:
            profile = exercise_profile(str(item.get("exercise") or ""))
            if not profile or not (
                profile["systemic_fatigue"] == "high" or set(profile["tags"]) & boxing_stress_tags
            ):
                continue
            previous_sets = int(item["sets"])
            previous_target = float(item["target_rir"])
            item["sets"] = min(previous_sets, 2)
            item["target_rir"] = max(previous_target, 3)
            if item["sets"] != previous_sets or item["target_rir"] != previous_target:
                reasons.add("boxing_interference")
    set_cap = 8 if boxing_sessions >= 3 and short_day else 12 if boxing_sessions >= 3 else None
    while set_cap is not None and sum(int(item.get("sets", 0)) for item in exercises) > set_cap:
        candidates = [item for item in exercises if int(item.get("sets", 0)) > 1]
        target = max(candidates, key=lambda item: int(item.get("sets", 0))) if candidates else None
        if target is None:
            break
        target["sets"] = int(target["sets"]) - 1
        reasons.add("daily_volume")
    return reasons


class Coach:
    def __init__(self, database: Database, knowledge: KnowledgeBase, llm: LLMClient):
        self.db = database
        self.knowledge = knowledge
        self.llm = llm

    def context(self, user_id: int) -> dict[str, Any]:
        recent_workouts = self.db.recent_sessions(user_id, 8)
        active_workout = self.db.active_session(user_id)
        now = int(self.db.clock())
        program = self.db.program(user_id, "active")
        return {
            "current_unix_time": now,
            "profile": self.db.get_profile(user_id),
            "active_program": program,
            "scheduled_program_day": self._scheduled_program_day(user_id),
            "proposed_program": self.db.program(user_id, "proposed"),
            "active_workout": active_workout,
            "active_workout_progress": [
                {
                    "exercise": item["exercise"],
                    "completed_working_sets": self._completed_working_sets(active_workout, str(item["exercise"])),
                    "required_working_sets": int(item["sets"]),
                }
                for item in (active_workout or {}).get("plan", {}).get("exercises", [])
                if isinstance(item, dict) and item.get("exercise") and item.get("sets") is not None
            ],
            "baselines": self.db.list_baselines(user_id),
            "recent_workouts": recent_workouts[:5],
            "analytics": summarize(recent_workouts, now),
            "performance_alerts": [
                {"exercise": name, "consecutive_failed_sessions": self._failure_streak(user_id, name)}
                for name in dict.fromkeys(
                    str(item.get("exercise"))
                    for day in (program or {}).get("days", [])
                    for item in day.get("exercises", [])
                    if isinstance(item, dict) and item.get("exercise")
                )
                if self._failure_streak(user_id, name) >= 2
            ],
            "recent_dialogue": self.db.recent_messages(user_id, 16),
            "scientific_knowledge": self.knowledge.items,
            "exercise_catalog": catalog_for_llm(),
        }

    @staticmethod
    def _humanize_effort_terms(text: str) -> str:
        def replace(match: re.Match[str]) -> str:
            value = match.group(1)
            return (
                f"около {value.replace(',', '.')} чистых повторов в запасе"
                if value
                else "повторы в запасе"
            )

        return RIR_TERM.sub(replace, text)

    def _scheduled_program_day(self, user_id: int) -> dict[str, Any] | None:
        program = self.db.program(user_id, "active")
        days = (program or {}).get("days", [])
        if not days:
            return None
        finished = [
            item for item in self.db.recent_sessions(user_id, 1000)
            if item.get("program_id") == program["id"]
            and item["status"] in {"completed", "aborted"}
            and item["session_type"] not in {"boxing", "cardio", "other", "assessment"}
        ]
        return days[len(finished) % len(days)]

    def _failure_streak(self, user_id: int, exercise: str) -> int:
        program = self.db.program(user_id, "active")
        key = exercise_key(exercise)
        streak = 0
        for session in self.db.recent_sessions(user_id, 40):
            if session.get("program_id") != (program or {}).get("id") or session["status"] not in {"completed", "aborted"}:
                continue
            sets = [
                item for item in session["sets"]
                if item["exercise_key"] == key and not item["is_warmup"] and item["actual_weight_kg"] is not None
            ]
            if not sets:
                continue
            failed = any(
                bool(item.get("pain"))
                or item.get("actual_rir") is not None and float(item["actual_rir"]) <= 0
                or item.get("planned_reps") is not None and int(item["actual_reps"]) < int(item["planned_reps"])
                for item in sets
            )
            if not failed:
                break
            streak += 1
        return streak

    def _increment(self, user_id: int) -> float:
        value = self.db.get_profile(user_id).get("smallest_weight_increment_kg", 2.5)
        try:
            increment = float(value)
        except (TypeError, ValueError):
            increment = 2.5
        return increment if 0 < increment <= 20 else 2.5

    def _baseline_for_exercise(self, user_id: int, exercise: str) -> dict[str, Any] | None:
        direct = self.db.baseline(user_id, exercise)
        if direct:
            return direct
        profile = exercise_profile(exercise)
        if (profile or {}).get("id") not in {"pullup", "weighted_pullup"}:
            return None
        for candidate in self.db.list_baselines(user_id):
            candidate_profile = exercise_profile(str(candidate.get("exercise") or ""))
            if (candidate_profile or {}).get("id") not in {"pullup", "weighted_pullup"}:
                continue
            result = dict(candidate)
            if result.get("bodyweight_kg") is None:
                result["bodyweight_kg"] = self.db.get_profile(user_id).get("bodyweight_kg")
            return result
        return None

    @staticmethod
    def _uses_fixed_implement(item: dict[str, Any]) -> bool:
        return bool(item.get("fixed_load")) or bool(FIXED_IMPLEMENT.search(str(item.get("exercise") or "")))

    def _allowed_exercises(self, user_id: int) -> set[str]:
        program = self.db.program(user_id)
        if not program:
            return set()
        allowed: set[str] = set()
        for day in program.get("days", []):
            if not isinstance(day, dict):
                continue
            for item in day.get("exercises", []):
                if not isinstance(item, dict):
                    continue
                if item.get("exercise"):
                    allowed.add(exercise_key(str(item["exercise"])))
                for substitute in item.get("substitutions", []):
                    allowed.add(exercise_key(str(substitute)))
        return allowed

    def _exercise_rule(self, user_id: int, exercise: str) -> dict[str, Any] | None:
        program = self.db.program(user_id)
        key = exercise_key(exercise)
        if not program:
            return None
        session = self.db.active_session(user_id)
        for item in (session or {}).get("plan", {}).get("exercises", []):
            if isinstance(item, dict) and exercise_key(str(item.get("exercise") or "")) == key:
                return item
        scheduled = self._scheduled_program_day(user_id)
        for day in [scheduled] if scheduled else program.get("days", []):
            for item in day.get("exercises", []) if isinstance(day, dict) else []:
                if isinstance(item, dict) and exercise_key(str(item.get("exercise") or "")) == key:
                    return item
                if isinstance(item, dict) and key in {
                    exercise_key(str(value)) for value in item.get("substitutions", [])
                }:
                    return item
        return None

    @staticmethod
    def _validate_program_days(days: Any) -> list[dict[str, Any]]:
        if not isinstance(days, list) or not 1 <= len(days) <= 7:
            raise ValidationError("Program requires 1-7 training days")
        for day in days:
            if not isinstance(day, dict) or not str(day.get("name") or "").strip():
                raise ValidationError("Each program day requires a name")
            exercises = day.get("exercises")
            if not isinstance(exercises, list) or not 1 <= len(exercises) <= 20:
                raise ValidationError("Each program day requires 1-20 exercises")
            for item in exercises:
                if not isinstance(item, dict) or not str(item.get("exercise") or "").strip():
                    raise ValidationError("Each program exercise requires a name")
                sets = int(item.get("sets", 0))
                reps_min = int(item.get("reps_min", 0))
                reps_max = int(item.get("reps_max", 0))
                target_rir = item.get("target_rir")
                rest_sec = int(item.get("rest_sec", 0))
                if not 1 <= sets <= 10 or not 1 <= reps_min <= reps_max <= 30 or not 15 <= rest_sec <= 900:
                    raise ValidationError("Invalid sets, reps or rest in program")
                if target_rir is not None and not 0 <= float(target_rir) <= 5:
                    raise ValidationError("Program RIR must be 0-5 or null for explosive work")
                profile = exercise_profile(str(item.get("exercise") or ""))
                if target_rir is None and profile and not (
                    set(profile["tags"]) & {"power", "quality_limited"}
                ):
                    raise ValidationError("An empty effort target is allowed only for explosive quality-based work")
                if item.get("load_kg") is not None and float(item["load_kg"]) < 0:
                    raise ValidationError("Program load cannot be negative")
        return days

    def _last_adjustment(self, user_id: int, exercise: str) -> dict[str, Any] | None:
        session = self.db.active_session(user_id)
        if not session:
            return None
        key = exercise_key(exercise)
        completed = [
            item for item in session["sets"]
            if item["exercise_key"] == key and item["actual_weight_kg"] is not None
        ]
        if not completed:
            return None
        item = completed[-1]
        return adjust_after_set(
            weight_kg=item["actual_weight_kg"],
            actual_reps=item["actual_reps"],
            planned_reps=item["planned_reps"] or item["actual_reps"],
            actual_rir=item["actual_rir"],
            target_rir=item["target_rir"] if item["target_rir"] is not None else 2,
            technique_ok=bool(item["technique_ok"]),
            pain=bool(item["pain"]),
            quality_ok=None if item["quality_ok"] is None else bool(item["quality_ok"]),
            increment_kg=self._increment(user_id),
        ).as_dict()

    @staticmethod
    def _completed_working_sets(session: dict[str, Any], exercise: str) -> int:
        key = exercise_key(exercise)
        return sum(
            1 for item in session["sets"]
            if item["exercise_key"] == key and not item["is_warmup"] and item["actual_weight_kg"] is not None
        )

    def _incomplete_session_exercises(self, session: dict[str, Any]) -> list[dict[str, Any]]:
        incomplete = []
        for item in session.get("plan", {}).get("exercises", []):
            if not isinstance(item, dict) or not item.get("exercise") or item.get("sets") is None:
                continue
            completed = self._completed_working_sets(session, str(item["exercise"]))
            required = int(item["sets"])
            if completed < required:
                incomplete.append({"exercise": item["exercise"], "completed": completed, "required": required})
        return incomplete

    def _adjust_session_exercise(self, user_id: int, exercise: str, new_sets: int, reason: str) -> dict[str, Any]:
        session = self.db.active_session(user_id)
        if not session:
            raise ValidationError("No active workout")
        if reason not in {"fatigue", "pain", "quality_drop", "missed_target", "time_limit"}:
            raise ValidationError("Session set reduction requires an allowed reason")
        plan = session.get("plan", {})
        target = next(
            (
                item for item in plan.get("exercises", [])
                if isinstance(item, dict)
                and exercise_key(str(item.get("exercise") or "")) == exercise_key(exercise)
            ),
            None,
        )
        if target is None:
            raise ValidationError("Exercise is not present in the current session plan")
        completed = self._completed_working_sets(session, exercise)
        previous = int(target.get("sets", 0))
        new_sets = int(new_sets)
        if not completed <= new_sets < previous:
            raise ValidationError("Adjusted sets must cover completed work and reduce the current plan")
        target["sets"] = new_sets
        self.db.save_session_plan(user_id, plan, replace=True)
        return {"exercise": exercise, "previous_sets": previous, "new_sets": new_sets, "reason": reason}

    def _recover_skipped_set(self, user_id: int) -> tuple[dict[str, Any], list[str]] | None:
        session = self.db.active_session(user_id)
        if not session or any(item["actual_weight_kg"] is None for item in session["sets"]):
            return None
        incomplete = self._incomplete_session_exercises(session)
        if not incomplete:
            return None
        exercise = str(incomplete[0]["exercise"])
        key = exercise_key(exercise)
        last = next(
            (
                item for item in reversed(session["sets"])
                if item["exercise_key"] == key and item["actual_weight_kg"] is not None
            ),
            None,
        )
        rule = self._exercise_rule(user_id, exercise)
        if not rule:
            return None
        if last:
            adjustment = self._last_adjustment(user_id, exercise)
            if not adjustment or adjustment["action"] == "stop":
                return None
            weight = adjustment.get("recommended_weight_kg")
            if weight is None:
                return None
            reps = int(last.get("planned_reps") or last.get("actual_reps") or rule["reps_min"])
        else:
            baseline = self._baseline_for_exercise(user_id, exercise)
            if rule.get("target_rir") is not None and not baseline:
                return None
            weight = rule.get("load_kg")
            if weight is None:
                weight = baseline.get("weight_kg", 0) if baseline else 0
            reps = int((baseline or {}).get("reps") or rule["reps_min"])
        reps = max(int(rule["reps_min"]), min(reps, int(rule["reps_max"])))
        return self._execute(
            user_id,
            {
                "type": "plan_set",
                "exercise": exercise,
                "weight_kg": weight,
                "reps": reps,
                "target_rir": rule.get("target_rir"),
                "rest_sec": int(rule["rest_sec"]),
                "is_warmup": False,
                "rationale": "Complete the remaining working sets in the saved session plan.",
                "evidence_ids": [],
            },
            "",
        )

    def _prepare_workout(self, user_id: int, action: dict[str, Any]) -> dict[str, Any]:
        session = self.db.active_session(user_id)
        if not session:
            raise ValidationError("No active workout")
        exercises = action.get("exercises")
        if not isinstance(exercises, list) or not 1 <= len(exercises) <= 20:
            raise ValidationError("Workout plan requires 1-20 exercises")
        scheduled = self._scheduled_program_day(user_id)
        rules = (scheduled or {}).get("exercises", [])
        normalized = []
        last_position = -1
        for item in exercises:
            if not isinstance(item, dict):
                raise ValidationError("Each workout exercise must be an object")
            key = exercise_key(str(item.get("exercise") or ""))
            match = next(
                (
                    (index, rule) for index, rule in enumerate(rules)
                    if index > last_position and key in {
                        exercise_key(str(value))
                        for value in [rule.get("exercise"), *rule.get("substitutions", [])]
                        if value
                    }
                ),
                None,
            )
            if match is None:
                raise ValidationError("Workout exercise or order differs from the scheduled program day")
            position, rule = match
            last_position = position
            sets = int(item.get("sets", 0))
            reps = int(item.get("reps", 0))
            target_rir = item.get("target_rir")
            if not 1 <= sets <= int(rule.get("sets", 0)):
                raise ValidationError("Workout sets exceed the active program")
            if not int(rule.get("reps_min", 0)) <= reps <= int(rule.get("reps_max", 0)):
                raise ValidationError("Workout repetitions are outside the active program")
            if target_rir != rule.get("target_rir"):
                raise ValidationError("Workout effort target differs from the active program")
            rest_sec = max(int(item.get("rest_sec", 0)), int(rule.get("rest_sec", 0)))
            validated = validate_set_plan(
                {
                    "exercise": item.get("exercise"),
                    "weight_kg": item.get("weight_kg", 0),
                    "reps": reps,
                    "target_rir": target_rir,
                    "rest_sec": rest_sec,
                    "is_warmup": False,
                    "quality_based": target_rir is None,
                },
                increment_kg=self._increment(user_id),
                reference=self._baseline_for_exercise(user_id, str(item.get("exercise") or "")),
                latest_adjustment=None,
                round_to_increment=not self._uses_fixed_implement({**rule, **item}),
            )
            normalized.append({
                "exercise": validated["exercise"],
                "sets": sets,
                "reps": validated["reps"],
                "reps_min": validated["reps"],
                "reps_max": validated["reps"],
                "target_rir": validated["target_rir"],
                "rest_sec": validated["rest_sec"],
                "load_kg": validated["weight_kg"],
                "quality_based": validated["quality_based"],
                "fixed_load": self._uses_fixed_implement({**rule, **item}),
                "instruction": str(item.get("instruction") or "")[:500],
            })
        return self.db.save_session_plan(
            user_id,
            {"summary": str(action.get("summary") or "")[:2000], "exercises": normalized},
        )

    def _record_workout(self, user_id: int, action: dict[str, Any]) -> dict[str, Any]:
        session = self.db.active_session(user_id)
        if not session or not session.get("plan"):
            raise ValidationError("No prepared workout to record")
        if session.get("sets"):
            raise ValidationError("A whole-workout report cannot be mixed with individually recorded sets")
        reports = action.get("exercises")
        if not isinstance(reports, list) or not reports:
            raise ValidationError("Workout report requires exercise results")
        by_key: dict[str, dict[str, Any]] = {}
        for report in reports:
            if not isinstance(report, dict) or not str(report.get("exercise") or "").strip():
                raise ValidationError("Each workout result requires an exercise")
            key = exercise_key(str(report["exercise"]))
            if key in by_key:
                raise ValidationError("Workout report contains a duplicate exercise")
            by_key[key] = report

        aborted = bool(action.get("aborted", False))
        validated_reports = []
        planned_keys = {
            exercise_key(str(item.get("exercise") or ""))
            for item in session["plan"].get("exercises", [])
        }
        if not set(by_key).issubset(planned_keys):
            raise ValidationError("Workout report contains an exercise outside today's plan")
        for planned in session["plan"].get("exercises", []):
            key = exercise_key(str(planned.get("exercise") or ""))
            report = by_key.get(key)
            if report is None:
                if aborted:
                    continue
                raise ValidationError("Workout report is missing a planned exercise")
            actual_sets = report.get("sets")
            if actual_sets is None:
                count = int(report.get("sets_completed", 0))
                actual_sets = [
                    {
                        "weight_kg": report.get("weight_kg"),
                        "reps": report.get("reps"),
                        "rir": report.get("rir"),
                        "quality_ok": report.get("quality_ok"),
                        "technique_ok": report.get("technique_ok"),
                        "pain": report.get("pain"),
                        "pain_location": report.get("pain_location"),
                    }
                    for _ in range(count)
                ]
            if not isinstance(actual_sets, list) or not actual_sets:
                raise ValidationError("Each exercise report requires at least one completed set")
            if len(actual_sets) > int(planned["sets"]):
                raise ValidationError("Reported sets exceed today's plan")
            normalized_sets = []
            stop_reason = None
            for index, result in enumerate(actual_sets):
                if not isinstance(result, dict):
                    raise ValidationError("Each completed set must be an object")
                weight = float(result.get("weight_kg", -1))
                reps = int(result.get("reps", -1))
                rir = None if result.get("rir") is None else float(result["rir"])
                if weight < 0 or not 0 <= reps <= 30 or (rir is not None and not 0 <= rir <= 10):
                    raise ValidationError("Reported set requires non-negative load, 0-30 reps and a valid reserve")
                if not isinstance(result.get("technique_ok"), bool) or not isinstance(result.get("pain"), bool):
                    raise ValidationError("Workout report must state technique and pain for every set")
                quality = result.get("quality_ok")
                if planned.get("quality_based") and not isinstance(quality, bool):
                    raise ValidationError("Explosive work requires a speed or quality result for every set")
                if stop_reason is not None:
                    raise ValidationError("No additional sets are allowed after pain or explosive quality loss")
                if result["pain"]:
                    stop_reason = "pain"
                elif planned.get("quality_based") and quality is False:
                    stop_reason = "quality_drop"
                normalized_sets.append({
                    "weight_kg": weight,
                    "reps": reps,
                    "rir": rir,
                    "quality_ok": quality,
                    "technique_ok": result["technique_ok"],
                    "pain": result["pain"],
                    "pain_location": str(result.get("pain_location") or "")[:200],
                })
            if len(normalized_sets) < int(planned["sets"]) and not (aborted or stop_reason):
                raise ValidationError("Incomplete exercise report requires a stop reason")
            validated_reports.append((planned, normalized_sets, stop_reason))

        duration = action.get("duration_minutes")
        session_rpe = action.get("session_rpe")
        if not aborted and (duration is None or session_rpe is None):
            raise ValidationError("Completed workout requires duration and overall difficulty reported by the user")
        if duration is not None and not 1 <= int(duration) <= 600:
            raise ValidationError("duration_minutes must be 1-600")
        if session_rpe is not None and not 0 <= float(session_rpe) <= 10:
            raise ValidationError("session_rpe must be 0-10")

        recorded = 0
        stopped = []
        for planned, actual_sets, stop_reason in validated_reports:
            for result in actual_sets:
                self.db.plan_set(user_id, {
                    "exercise": planned["exercise"],
                    "weight_kg": planned["load_kg"],
                    "reps": planned["reps"],
                    "target_rir": planned["target_rir"],
                    "rest_sec": planned["rest_sec"],
                    "is_warmup": False,
                })
                self.db.record_set(user_id, {"exercise": planned["exercise"], **result})
                recorded += 1
            if stop_reason and len(actual_sets) < int(planned["sets"]):
                self._adjust_session_exercise(
                    user_id, str(planned["exercise"]), len(actual_sets), stop_reason
                )
                stopped.append({"exercise": planned["exercise"], "reason": stop_reason})
        result = self.db.complete_session(user_id, {
            "duration_minutes": duration,
            "session_rpe": session_rpe,
            "notes": str(action.get("notes") or "")[:4000],
            "aborted": aborted,
        })
        return {**result, "recorded_sets": recorded, "stopped_exercises": stopped}

    def _execute(self, user_id: int, action: dict[str, Any], user_text: str) -> tuple[dict[str, Any], list[str]]:
        action_type = str(action.get("type") or "")
        evidence = [item for item in action.get("evidence_ids", []) if item in self.knowledge.by_id]

        if action_type == "update_profile":
            result = self.db.update_profile(user_id, dict(action.get("values") or {}))
        elif action_type == "save_baseline":
            weight = float(action.get("weight_kg", 0))
            reps = int(action.get("reps", 0))
            rir = None if action.get("rir") is None else float(action["rir"])
            bodyweight = float(action.get("bodyweight_kg") or 0)
            if weight < 0 or weight + bodyweight <= 0 or not 1 <= reps <= 20 or (rir is not None and not 0 <= rir <= 10):
                raise ValidationError("Baseline requires a positive total load, 1-20 reps and RIR 0-10")
            result = self.db.save_baseline(user_id, action)
        elif action_type == "propose_program":
            self._validate_program_days(action.get("days"))
            profile = self.db.get_profile(user_id)
            corrected_days: list[str] = []
            correction_reasons: dict[str, list[str]] = {}
            days = json.loads(json.dumps(action["days"], ensure_ascii=False))
            boxing_sessions = len(profile.get("boxing_schedule", []))
            for day in days:
                reasons = balance_program_day(day, boxing_sessions)
                if reasons:
                    name = str(day.get("name") or "")
                    corrected_days.append(name)
                    correction_reasons[name] = sorted(reasons)
            proposal = {**action, "days": days}
            if correction_reasons:
                proposal["rationale"] = (
                    "Движок скорректировал исходное предложение по общей нагрузке: порядок взрывной работы, "
                    "совмещение тяжёлых базовых упражнений и дневной объём. Итоговые значения указаны в днях программы."
                )
            result = self.db.propose_program(user_id, proposal)
            result["program"] = self.db.program(user_id, "proposed")
            if corrected_days:
                result["volume_corrected_by_engine"] = corrected_days
                result["program_corrections"] = correction_reasons
        elif action_type == "activate_program":
            if not CONFIRMATION.search(user_text):
                raise ValidationError("Program activation requires explicit confirmation in the current message")
            result = self.db.activate_program(user_id, int(action["program_id"]))
        elif action_type == "start_workout":
            readiness = action.get("readiness") or {}
            if not isinstance(readiness, dict):
                raise ValidationError("readiness must be an object")
            result = self.db.start_session(user_id, action)
        elif action_type == "prepare_workout":
            result = self._prepare_workout(user_id, action)
        elif action_type == "save_session_plan":
            exercises = action.get("exercises")
            if not isinstance(exercises, list) or not 1 <= len(exercises) <= 20:
                raise ValidationError("Session plan requires 1-20 exercises")
            allowed = self._allowed_exercises(user_id)
            names = {
                exercise_key(str(item.get("exercise") if isinstance(item, dict) else item))
                for item in exercises
            }
            if not names.issubset(allowed):
                raise ValidationError("Session plan contains an exercise outside the active program")
            scheduled = self._scheduled_program_day(user_id)
            scheduled_names = {
                exercise_key(str(value))
                for item in (scheduled or {}).get("exercises", [])
                if isinstance(item, dict)
                for value in [item.get("exercise"), *item.get("substitutions", [])]
                if value
            }
            if scheduled_names and not names.issubset(scheduled_names):
                raise ValidationError("Session plan does not match the scheduled program day")
            result = self.db.save_session_plan(
                user_id,
                {"summary": str(action.get("summary") or "")[:2000], "exercises": exercises},
            )
        elif action_type == "plan_set":
            session = self.db.active_session(user_id)
            if not session:
                raise ValidationError("No active workout")
            if session["session_type"] != "assessment" and exercise_key(str(action.get("exercise") or "")) not in self._allowed_exercises(user_id):
                raise ValidationError("Exercise is not allowed by the active program")
            if (
                session["session_type"] != "assessment"
                and not bool(action.get("is_warmup", False))
                and self._failure_streak(user_id, str(action.get("exercise") or "")) >= 3
            ):
                raise ValidationError("Repeated failures require reassessment or a new program before another working set")
            rule = self._exercise_rule(user_id, str(action.get("exercise") or ""))
            if rule and not bool(action.get("is_warmup", False)):
                session_plan = session.get("plan", {}).get("exercises", [])
                current_key = exercise_key(str(action.get("exercise") or ""))
                position = next(
                    (
                        index for index, item in enumerate(session_plan)
                        if isinstance(item, dict)
                        and exercise_key(str(item.get("exercise") or "")) == current_key
                    ),
                    None,
                )
                if position is None:
                    raise ValidationError("Exercise is not present in the current session plan")
                for earlier in session_plan[:position]:
                    if not isinstance(earlier, dict):
                        continue
                    completed_earlier = self._completed_working_sets(
                        session, str(earlier.get("exercise") or "")
                    )
                    if completed_earlier < int(earlier.get("sets", 0)):
                        raise ValidationError("Previous exercise still has programmed working sets")
                reps = int(action.get("reps", 0))
                if not int(rule["reps_min"]) <= reps <= int(rule["reps_max"]):
                    raise ValidationError("Repetitions are outside the active program")
                if rule.get("target_rir") != action.get("target_rir"):
                    raise ValidationError("Target RIR differs from the active program")
                completed_sets = self._completed_working_sets(session, str(action.get("exercise") or ""))
                if completed_sets >= int(rule["sets"]):
                    raise ValidationError("All programmed working sets are already complete")
                action = {**action, "rest_sec": max(int(action.get("rest_sec", 0)), int(rule["rest_sec"]))}
            if action.get("target_rir") is None and session["session_type"] != "assessment":
                if not rule or rule.get("target_rir") is not None:
                    raise ValidationError("Quality-based work must be explicitly configured in the active program")
                if rule.get("load_kg") is not None and float(action.get("weight_kg", 0)) > float(rule["load_kg"]):
                    raise ValidationError("Explosive load exceeds the active program")
                action = {**action, "quality_based": True}
            reference = self._baseline_for_exercise(user_id, str(action.get("exercise") or ""))
            adjustment = self._last_adjustment(user_id, str(action.get("exercise") or ""))
            validated = validate_set_plan(
                action,
                increment_kg=self._increment(user_id),
                reference=reference,
                latest_adjustment=adjustment,
                round_to_increment=not self._uses_fixed_implement({**(rule or {}), **action}),
            )
            result = self.db.plan_set(user_id, validated)
            if float(action.get("weight_kg", 0)) != result["weight_kg"]:
                result["load_corrected_by_engine"] = True
                result["proposed_weight_kg"] = float(action.get("weight_kg", 0))
        elif action_type == "record_set":
            weight = float(action.get("weight_kg", -1))
            reps = int(action.get("reps", -1))
            rir = None if action.get("rir") is None else float(action["rir"])
            if weight < 0 or reps < 0 or (rir is not None and not 0 <= rir <= 10):
                raise ValidationError("Set result requires non-negative load/reps and RIR 0-10 or unknown")
            session = self.db.active_session(user_id)
            key = exercise_key(str(action.get("exercise") or ""))
            pending = next(
                (
                    item for item in (session or {}).get("sets", [])
                    if item["exercise_key"] == key and item["actual_weight_kg"] is None
                ),
                None,
            )
            session_rule = next(
                (
                    item for item in (session or {}).get("plan", {}).get("exercises", [])
                    if isinstance(item, dict) and exercise_key(str(item.get("exercise") or "")) == key
                ),
                None,
            )
            target = (session_rule or {}).get("target_rir")
            if pending is None and target is not None and rir is not None and rir >= float(target) + 2:
                action = {**action, "is_warmup": True}
            saved = self.db.record_set(user_id, action)
            adjustment = adjust_after_set(
                weight_kg=saved["actual_weight_kg"],
                actual_reps=saved["actual_reps"],
                planned_reps=saved["planned_reps"] or max(1, saved["actual_reps"]),
                actual_rir=saved["actual_rir"],
                target_rir=saved["target_rir"] if saved["target_rir"] is not None else 2,
                technique_ok=bool(saved["technique_ok"]),
                pain=bool(saved["pain"]),
                quality_ok=None if saved["quality_ok"] is None else bool(saved["quality_ok"]),
                increment_kg=self._increment(user_id),
            ).as_dict()
            result = {"saved_set": saved, "next_set_guardrail": adjustment}
            if adjustment["action"] == "stop":
                session = self.db.active_session(user_id)
                current_plan = next(
                    (
                        item for item in session.get("plan", {}).get("exercises", [])
                        if isinstance(item, dict)
                        and exercise_key(str(item.get("exercise") or ""))
                        == exercise_key(str(action["exercise"]))
                    ),
                    None,
                ) if session else None
                if current_plan:
                    completed = self._completed_working_sets(session, str(action["exercise"]))
                    if completed < int(current_plan.get("sets", 0)):
                        result["session_plan_adjustment"] = self._adjust_session_exercise(
                            user_id,
                            str(action["exercise"]),
                            completed,
                            "pain" if saved["pain"] else "quality_drop",
                        )
        elif action_type == "complete_workout":
            session_rpe = action.get("session_rpe")
            if session_rpe is not None and not 0 <= float(session_rpe) <= 10:
                raise ValidationError("session_rpe must be 0-10")
            duration = action.get("duration_minutes")
            if duration is not None and not 1 <= int(duration) <= 600:
                raise ValidationError("duration_minutes must be 1-600")
            if not action.get("aborted") and (duration is None or session_rpe is None):
                raise ValidationError("Completed workout requires duration and session RPE reported by the user")
            session = self.db.active_session(user_id)
            incomplete = self._incomplete_session_exercises(session) if session else []
            if incomplete and not action.get("aborted"):
                raise ValidationError("Current session plan still has incomplete exercises")
            result = self.db.complete_session(user_id, action)
        elif action_type == "record_workout":
            result = self._record_workout(user_id, action)
        elif action_type == "adjust_session_exercise":
            if action.get("reason") == "time_limit" and not TIME_LIMIT.search(user_text):
                raise ValidationError("Time-limit adjustment requires an explicit user time constraint")
            result = self._adjust_session_exercise(
                user_id,
                str(action.get("exercise") or ""),
                int(action.get("new_sets", -1)),
                str(action.get("reason") or ""),
            )
        elif action_type == "log_activity":
            duration = int(action.get("duration_minutes", 0))
            session_rpe = float(action.get("session_rpe", -1))
            if not 1 <= duration <= 600 or not 0 <= session_rpe <= 10:
                raise ValidationError("Activity requires duration 1-600 and session RPE 0-10")
            result = self.db.log_activity(user_id, action)
        elif action_type == "search_science":
            found = self.knowledge.search(str(action.get("query") or ""), int(action.get("limit") or 5))
            result = {"records": found}
            evidence = [item["id"] for item in found]
        else:
            raise ValidationError(f"Unknown action: {action_type or '<empty>'}")

        self.db.audit(user_id, action_type, action, result, evidence)
        return result, evidence

    @staticmethod
    def _fallback_answer(draft: str, results: list[dict[str, Any]]) -> str:
        failed = [item for item in results if not item["ok"]]
        if failed:
            return (
                "Не удалось применить часть решения: она выходит за границы активной программы "
                "или безопасной нагрузки. Уточни данные последнего подхода."
            )
        for item in reversed(results):
            result = item["result"]
            if item["type"] == "prepare_workout":
                lines = [str(result.get("summary") or "Сегодняшняя тренировка:").strip()]
                for exercise in result.get("exercises", []):
                    target = exercise.get("target_rir")
                    effort = (
                        f"оставь около {target:g} чистых повторов в запасе"
                        if target is not None
                        else "каждый повтор максимально резко; остановись при заметном падении скорости"
                    )
                    load_value = float(exercise["load_kg"])
                    load = f"{load_value:g} кг" if load_value else "без внешнего веса"
                    lines.append(
                        f"{exercise['exercise']}: {exercise['sets']}×{exercise['reps']}, {load}, "
                        f"{effort}, отдых {exercise['rest_sec']} с."
                    )
                lines.append("После тренировки пришли один общий отчёт по упражнениям, боли, технике, длительности и общей тяжести 0–10.")
                return "\n".join(lines)
            if item["type"] == "plan_set":
                effort = (
                    f"оставь около {result['target_rir']:g} чистых повторов в запасе"
                    if result["target_rir"] is not None
                    else "контроль скорости и качества"
                )
                return (
                    f"Следующий подход: {result['exercise']} — {result['weight_kg']:g} кг × "
                    f"{result['reps']}, {effort}, отдых {result['rest_sec']} секунд."
                )
            if item["type"] == "record_set":
                guardrail = result["next_set_guardrail"]
                return f"Подход записан. Решение по следующему подходу: {guardrail['explanation']}"
            if item["type"] == "record_workout":
                return "Тренировка и все подходы сохранены. Следующую тренировку скорректирую по этому результату."
            if item["type"] == "propose_program":
                program = result["program"]
                lines = [f"Предлагаю программу версии {program['version']}: «{program['title']}». "]
                for day in program.get("days", []):
                    lines.append(f"{day.get('name', 'Тренировочный день')}:")
                    for exercise in day.get("exercises", []):
                        target = exercise.get("target_rir")
                        effort = (
                            f"запас около {target:g} чистых повторов"
                            if target is not None
                            else "максимальная скорость; остановка при заметном падении качества"
                        )
                        load = exercise.get("load_kg")
                        load_text = f", {float(load):g} кг" if load is not None else ""
                        lines.append(
                            f"- {exercise['exercise']}: {exercise['sets']}×"
                            f"{exercise['reps_min']}–{exercise['reps_max']}{load_text}, {effort}, "
                            f"отдых {exercise['rest_sec']} с."
                        )
                lines.append("Если план подходит, подтверди его отдельным сообщением. До подтверждения он не активируется.")
                return "\n".join(lines)
        return draft.strip() or "Данные сохранены."

    def handle_message(
        self,
        *,
        platform: str,
        external_user_id: str,
        display_name: str,
        event_id: str,
        text: str,
    ) -> str | None:
        user_id = self.db.get_or_create_user(platform, external_user_id, display_name)
        if not self.db.remember_message(platform, event_id, user_id, "user", text):
            return None

        try:
            plan = self.llm.plan(self.context(user_id), text)
            results: list[dict[str, Any]] = []
            used_evidence: list[str] = []
            for action in plan["actions"]:
                if not isinstance(action, dict):
                    continue
                action_type = str(action.get("type") or "")
                try:
                    value, evidence = self._execute(user_id, action, text)
                    results.append({"type": action_type, "ok": True, "result": value})
                    used_evidence.extend(evidence)
                except (KeyError, TypeError, ValueError, ValidationError) as error:
                    failure = {"type": action_type, "ok": False, "error": str(error)}
                    results.append(failure)
                    self.db.audit(user_id, action_type or "invalid", action, failure, [])
            skipped_set = any(
                not item["ok"]
                and (
                    item["type"] == "plan_set"
                    and "Previous exercise still has programmed working sets" in item["error"]
                    or item["type"] == "complete_workout"
                    and "incomplete exercises" in item["error"]
                )
                for item in results
            )
            if skipped_set:
                try:
                    recovered = self._recover_skipped_set(user_id)
                    if recovered:
                        value, evidence = recovered
                        results.append({"type": "plan_set", "ok": True, "result": value})
                        used_evidence.extend(evidence)
                except (KeyError, TypeError, ValueError, ValidationError) as error:
                    failure = {"type": "plan_set", "ok": False, "error": str(error)}
                    results.append(failure)
                    self.db.audit(user_id, "plan_set", {}, failure, [])
            science = self.knowledge.resolve(list(dict.fromkeys(used_evidence)))
            if results:
                if any(
                    item["ok"] and item["type"] in {"propose_program", "prepare_workout"}
                    for item in results
                ):
                    answer = self._fallback_answer(plan["draft_reply"], results)
                else:
                    try:
                        answer = self.llm.finalize(
                            user_text=text,
                            draft_reply=plan["draft_reply"],
                            results=results,
                            science=science,
                        )
                    except LLMError:
                        logging.exception("LLM finalization failed; using deterministic reply")
                        answer = self._fallback_answer(plan["draft_reply"], results)
                critical = {
                    "propose_program", "activate_program", "prepare_workout", "record_workout",
                    "plan_set", "record_set", "complete_workout",
                }
                if any(item["ok"] and item["type"] in critical for item in results) and not answer.strip():
                    answer = self._fallback_answer(plan["draft_reply"], results)
            else:
                answer = plan["draft_reply"]
            if not answer.strip():
                answer = "Мне не хватает данных для безопасного решения. Опиши цель и последний рабочий подход."
        except LLMError as error:
            logging.exception("LLM processing failed")
            answer = "Сейчас не удалось получить ответ от модели. Данные сообщения сохранены; попробуй ещё раз чуть позже."
            if "not configured" in str(error):
                answer = "LLM не настроена. Добавь API-ключ в .env и перезапусти бота."

        answer = self._humanize_effort_terms(answer)
        self.db.remember_message(platform, f"reply:{event_id}", user_id, "assistant", answer)
        return answer
