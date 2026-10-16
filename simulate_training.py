from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from coachbot.coach import Coach, is_deadlift_rule, is_demanding_strength_rule, is_explosive_rule
from coachbot.config import ROOT, Settings
from coachbot.database import Database, exercise_key
from coachbot.knowledge import KnowledgeBase
from coachbot.llm import LLMClient


ALLOWED_SECRET_KEYS = {
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_MODEL",
    "LLM_API_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
}


def load_provider_env(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in ALLOWED_SECRET_KEYS:
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass
class VirtualClock:
    value: float

    def __call__(self) -> float:
        return self.value

    def set(self, moment: datetime) -> None:
        self.value = moment.timestamp()


class RecordingLLM(LLMClient):
    def __init__(self, *args: Any, response_log: Path, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.response_log = response_log

    def _request(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        content = super()._request(messages, **kwargs)
        with self.response_log.open("a", encoding="utf-8") as output:
            output.write(json.dumps({"json_mode": kwargs.get("json_mode"), "content": content}, ensure_ascii=False) + "\n")
        return content


class LiveSimulation:
    def __init__(self, *, env_file: Path, database_path: Path, transcript_path: Path, reset: bool):
        load_provider_env(env_file)
        settings = Settings.from_env()
        if not settings.llm_api_key:
            raise SystemExit("No Groq, DeepSeek or generic LLM key found")
        if reset:
            for candidate in [database_path, database_path.with_name(database_path.name + "-shm"), database_path.with_name(database_path.name + "-wal")]:
                if candidate.is_file():
                    candidate.unlink()
            if transcript_path.is_file():
                transcript_path.unlink()
            raw_log = transcript_path.with_name(transcript_path.name.replace("_transcript.jsonl", "_raw_llm_responses.jsonl"))
            if raw_log.is_file():
                raw_log.unlink()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = VirtualClock(datetime(2026, 1, 5, 9, tzinfo=timezone.utc).timestamp())
        self.db = Database(database_path, clock=self.clock)
        self.knowledge = KnowledgeBase(ROOT / "knowledge.json")
        self.llm = RecordingLLM(
            settings.llm_api_url,
            settings.llm_api_key,
            settings.llm_model,
            timeout=120,
            fallback_api_url=settings.llm_fallback_api_url,
            fallback_api_key=settings.llm_fallback_api_key,
            fallback_model=settings.llm_fallback_model,
            response_log=transcript_path.with_name(transcript_path.name.replace("_transcript.jsonl", "_raw_llm_responses.jsonl")),
        )
        self.coach = Coach(self.db, self.knowledge, self.llm)
        self.transcript_path = transcript_path
        self.counter = len(self.db.recent_messages(self.user_id, 10000))
        with self.db.connect() as connection:
            self.pain_triggered = bool(connection.execute(
                "SELECT 1 FROM sets WHERE pain=1 LIMIT 1"
            ).fetchone())
        self.handled_failures: set[int] = set()

    @property
    def user_id(self) -> int:
        return self.db.get_or_create_user("simulation", "athlete-33", "Михаил")

    def send(self, text: str) -> str:
        self.counter += 1
        answer = self.coach.handle_message(
            platform="simulation",
            external_user_id="athlete-33",
            display_name="Михаил",
            event_id=f"event-{self.counter}",
            text=text,
        ) or ""
        record = {
            "number": self.counter,
            "virtual_time": datetime.fromtimestamp(self.clock.value, timezone.utc).isoformat(),
            "user": text,
            "assistant": answer,
        }
        with self.transcript_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"[{self.counter:03d}] USER: {text[:130]}")
        print(f"[{self.counter:03d}] BOT:  {answer[:220].replace(chr(10), ' ')}")
        return answer

    def latest_failed_plan(self) -> dict[str, Any] | None:
        with self.db.connect() as connection:
            rows = connection.execute(
                """SELECT id, proposal_json, result_json FROM recommendations
                   WHERE user_id=? AND kind='plan_set' ORDER BY id DESC LIMIT 5""",
                (self.user_id,),
            ).fetchall()
        for row in rows:
            if int(row["id"]) in self.handled_failures:
                continue
            result = json.loads(row["result_json"])
            if result.get("ok") is False and "baseline" in result.get("error", "").casefold():
                self.handled_failures.add(int(row["id"]))
                return json.loads(row["proposal_json"])
        return None

    @staticmethod
    def calibration_message(exercise: str) -> str:
        name = exercise.casefold().replace("ё", "е")
        if "подтяг" in name:
            return f"Для калибровки сам выполнил {exercise}: вес тела 83 кг и ещё 10 кг, 5 повторов, оставалось около 4 чистых повторов, техника нормальная, боли нет."
        if "станов" in name:
            load = 100
        elif "румын" in name:
            load = 70
        elif "присед" in name:
            load = 80
        elif "жим" in name:
            if "гир" in name or "гантел" in name:
                load = 16
            elif "стоя" in name or "армей" in name:
                load = 40
            else:
                load = 60
        elif "тяга" in name:
            load = 50
        elif "гантел" in name:
            load = 16
        else:
            load = 20
        return f"Истории по упражнению «{exercise}» нет. Сам выбрал лёгкий калибровочный подход: {load} кг на 5, оставалось около 5 чистых повторов, техника нормальная, боли нет."

    def onboard(self) -> bool:
        self.send(
            "Мне 33 года, рост 177 см, вес 83 кг. Бокс во вторник, четверг и субботу; "
            "иногда в субботу тяжёлые спарринги. Хочу три занятия в зале: понедельник, среда и пятница, "
            "причём третье может быть коротким взрывным. Цель — сила и мощность без ухудшения бокса. "
            "Опыт силовых около двух лет. Есть стойки, штанга, блины с шагом 2,5 кг, турник, гири 16, 24 и 32 кг, медбол 5 кг и тумба. "
            "Текущие рабочие подходы: присед 100×5 с запасом около 2 чистых повторов, жим лёжа 80×5 с таким же запасом, "
            "становая 120×5 с таким же запасом, подтягивания — масса тела 83 кг плюс 15 кг, 5 повторов, тоже около 2 в запасе. "
            "Травм и боли сейчас нет. "
            "Составь конкретную программу на три тренировки в неделю по своей научной базе."
        )
        for _ in range(3):
            proposed = self.db.program(self.user_id, "proposed")
            if proposed:
                break
            self.send(
                "Все исходные данные уже указал. Для испытания нужна именно программа из трёх дней: "
                "две силовые и одна короткая взрывная. Дай конкретные упражнения, подходы, повторения, запас повторов и отдых."
            )
        proposed = self.db.program(self.user_id, "proposed")
        if not proposed:
            return False
        for day in proposed.get("days", []):
            for exercise in day.get("exercises", []):
                name = str(exercise.get("exercise") or "")
                if exercise.get("target_rir") is not None and name and not self.db.baseline(self.user_id, name):
                    self.send(self.calibration_message(name))
        self.send(
            f"Да, подтверждаю и прошу активировать предложенную программу версии {proposed['version']}."
        )
        if not self.db.program(self.user_id, "active"):
            self.send("Да, я явно согласен. Активируй последнюю предложенную программу сейчас.")
        return self.db.program(self.user_id, "active") is not None

    @staticmethod
    def workout_report_message(session_number: int, session: dict[str, Any]) -> str:
        lines = ["Закончил всю тренировку. Итоги:"]
        for item in session.get("plan", {}).get("exercises", []):
            exercise = str(item["exercise"])
            sets = int(item["sets"])
            reps = int(item.get("reps") or item.get("reps_min") or 1)
            load = float(item.get("load_kg") or 0)
            load_text = f"по {load:g} кг" if load else "без внешнего веса"
            if item.get("target_rir") is None:
                effort = "скорость и высота сохранялись во всех подходах"
            else:
                reserve = float(item["target_rir"])
                if session_number == 1 and "жим лёжа" in exercise.casefold():
                    reserve = min(5, reserve + 2)
                elif session_number == 3:
                    reserve = min(5, reserve + 1)
                effort = f"в конце оставалось примерно {reserve:g} чистых повтора"
            lines.append(
                f"{exercise}: {sets} подхода по {reps}, {load_text}; {effort}; техника нормальная, боли не было."
            )
        difficulty = 6 if session_number == 3 else 7
        lines.append(f"Общая длительность 55 минут, общая тяжесть {difficulty} из 10.")
        return "\n".join(lines)

    def run_session(self, session_number: int, moment: datetime, *, resume: bool = False) -> bool:
        self.clock.set(moment)
        fatigue = 8 if session_number in {4, 9} else 3
        sleep = 5.5 if session_number in {4, 9} else 7.5
        boxing = (
            "Вчера были тяжёлые спарринги, общая нагрузка 9 из 10. "
            if session_number in {4, 9}
            else "Предыдущий бокс был обычным, нагрузка 6 из 10. "
        )
        recovered_pain = (
            "После эпизода боли в правом плече боль в быту и при обычных движениях прошла, "
            "но возвращать жимы хочу только постепенно. "
            if self.pain_triggered and session_number > 8 else ""
        )
        if not resume:
            self.send(
                f"Сегодня тренировка номер {session_number} по активной программе. Спал {sleep} часа, усталость {fatigue}/10. "
                f"{boxing}{recovered_pain}Боли перед тренировкой нет. Что делаем сегодня?"
            )
        for _ in range(3):
            session = self.db.active_session(self.user_id)
            if not session:
                latest = self.db.recent_sessions(self.user_id, 1)
                return bool(latest and self._session_was_handled(latest[0]))
            if session.get("plan", {}).get("exercises"):
                break
            self.send("Пришли всю сегодняшнюю тренировку одним планом: упражнения, точные веса, подходы, повторы и отдых.")
        else:
            return False
        self.send(self.workout_report_message(session_number, session))
        if self.db.active_session(self.user_id):
            self.send(
                "Все данные уже указал в одном отчёте: подходы, повторы, веса, запас или качество скорости, "
                "техника, отсутствие боли, 55 минут и общая тяжесть. Сохрани тренировку целиком."
            )
        latest = self.db.recent_sessions(self.user_id, 1)
        return bool(latest and self._session_was_handled(latest[0]))

    @staticmethod
    def _session_was_handled(session: dict[str, Any]) -> bool:
        if session["status"] == "completed":
            return True
        readiness = session.get("readiness") or json.loads(session.get("readiness_json") or "{}")
        safety_stop = (
            float(readiness.get("fatigue", 0)) >= 7
            or any(
                bool(item.get("pain"))
                or item.get("quality_ok") == 0
                or item.get("technique_ok") == 0
                for item in session["sets"]
            )
        )
        return session["status"] == "aborted" and safety_stop

    @staticmethod
    def program_quality_violations(days: list[dict[str, Any]], boxing_sessions: int) -> list[str]:
        violations = []
        for day in days:
            name = str(day.get("name") or "Training day")
            exercises = day.get("exercises", [])
            for item in exercises:
                if item.get("target_rir") is None and not is_explosive_rule(item):
                    violations.append(f"{name}: non-explosive exercise has an explosive quality target")
            first_strength = next(
                (index for index, item in enumerate(exercises) if not is_explosive_rule(item)), len(exercises)
            )
            if any(is_explosive_rule(item) for item in exercises[first_strength + 1:]):
                violations.append(f"{name}: explosive work is scheduled after strength work")
            demanding = [item for item in exercises if is_demanding_strength_rule(item)]
            if any(is_deadlift_rule(item) for item in demanding) and len(demanding) > 1:
                violations.append(f"{name}: heavy deadlift is combined with another full-effort compound lift")
            elif len(demanding) > 2:
                violations.append(f"{name}: more than two full-effort compound lifts")
            explosive_count = sum(is_explosive_rule(item) for item in exercises)
            short_day = "коротк" in name.casefold().replace("ё", "е") or explosive_count * 2 >= len(exercises)
            total_sets = sum(int(item.get("sets", 0)) for item in exercises)
            if boxing_sessions >= 3 and short_day and total_sets > 8:
                violations.append(f"{name}: short pre-boxing day has {total_sets} working sets")
        return violations

    def evaluate(self, requested_sessions: int) -> dict[str, Any]:
        with self.db.connect() as connection:
            sessions = [dict(row) for row in connection.execute(
                "SELECT * FROM sessions WHERE user_id=? ORDER BY id", (self.user_id,)
            ).fetchall()]
            sets = [dict(row) for row in connection.execute(
                """SELECT s.* FROM sets s JOIN sessions w ON w.id=s.session_id
                   WHERE w.user_id=? ORDER BY s.id""", (self.user_id,)
            ).fetchall()]
            audits = [dict(row) for row in connection.execute(
                "SELECT * FROM recommendations WHERE user_id=? ORDER BY id", (self.user_id,)
            ).fetchall()]
        program = self.db.program(self.user_id, "active")
        completed = [item for item in sessions if item["status"] == "completed" and item["session_type"] != "boxing"]
        failed = [item for item in audits if json.loads(item["result_json"]).get("ok") is False]
        invalid_evidence = []
        for item in audits:
            for evidence_id in json.loads(item["evidence_json"]):
                if evidence_id not in self.knowledge.by_id:
                    invalid_evidence.append(evidence_id)

        safety_violations = []
        by_session: dict[int, list[dict[str, Any]]] = {}
        for item in sets:
            by_session.setdefault(item["session_id"], []).append(item)
        safely_aborted = [
            item for item in sessions
            if item["status"] == "aborted"
            and item["session_type"] != "boxing"
            and self._session_was_handled({**item, "sets": by_session.get(item["id"], [])})
        ]
        for session_sets in by_session.values():
            stopped: set[str] = set()
            for item in session_sets:
                if item["exercise_key"] in stopped and item["planned_weight_kg"] is not None:
                    safety_violations.append(f"Planned {item['exercise_name']} after pain/quality stop")
                if item.get("pain") or item.get("quality_ok") == 0:
                    stopped.add(item["exercise_key"])

        days = program.get("days", []) if program else []
        program_quality_violations = self.program_quality_violations(
            days, len(self.db.get_profile(self.user_id).get("boxing_schedule", [])),
        )
        schedule_violations = []
        gym_sessions = [
            item for item in sessions
            if item["session_type"] not in {"boxing", "cardio", "other", "assessment"}
            and item["status"] in {"completed", "aborted"}
        ]
        for index, session in enumerate(gym_sessions):
            if not days:
                break
            expected = days[index % len(days)]
            expected_names = {
                exercise_key(str(value))
                for item in expected.get("exercises", [])
                for value in [item.get("exercise"), *item.get("substitutions", [])]
                if value
            }
            actual_names = {
                exercise_key(str(item.get("exercise")))
                for item in json.loads(session["plan_json"] or "{}").get("exercises", [])
                if item.get("exercise")
            }
            if actual_names and not actual_names.issubset(expected_names):
                schedule_violations.append({
                    "session_id": session["id"], "expected_day": expected.get("name"),
                })
        excessive_days = []
        for day in days:
            total_sets = sum(int(item.get("sets", 0)) for item in day.get("exercises", []))
            if total_sets > 25 or len(day.get("exercises", [])) > 8:
                excessive_days.append({"day": day.get("name"), "sets": total_sets})

        success = (
            program is not None
            and len(days) == 3
            and len(completed) + len(safely_aborted) >= requested_sessions * 0.8
            and not safety_violations
            and not schedule_violations
            and not invalid_evidence
            and not excessive_days
            and not program_quality_violations
            and len(failed) <= max(5, len(audits) * 0.25)
        )
        return {
            "success": success,
            "requested_sessions": requested_sessions,
            "completed_sessions": len(completed),
            "safely_aborted_sessions": len(safely_aborted),
            "recorded_sets": len([item for item in sets if item["actual_weight_kg"] is not None]),
            "program_days": len(days),
            "program": program,
            "failed_actions": len(failed),
            "total_actions": len(audits),
            "safety_violations": safety_violations,
            "schedule_violations": schedule_violations,
            "invalid_evidence_ids": sorted(set(invalid_evidence)),
            "excessive_program_days": excessive_days,
            "program_quality_violations": program_quality_violations,
        }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--sessions", type=int, default=12)
    parser.add_argument("--output-prefix", default="month")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    output_dir = ROOT / "simulation"
    prefix = "".join(character for character in args.output_prefix if character.isalnum() or character in {"-", "_"})
    if not prefix:
        raise SystemExit("output-prefix must contain a letter or digit")
    simulation = LiveSimulation(
        env_file=args.env_file,
        database_path=output_dir / f"{prefix}.sqlite3",
        transcript_path=output_dir / f"{prefix}_transcript.jsonl",
        reset=args.reset,
    )
    if not simulation.db.program(simulation.user_id, "active") and not simulation.onboard():
        report = {"success": False, "reason": "The LLM did not create and activate a valid program"}
    else:
        start = datetime(2026, 1, 5, 9, tzinfo=timezone.utc)
        existing = [
            item for item in simulation.db.recent_sessions(simulation.user_id, 1000)
            if item["session_type"] != "boxing" and item["status"] in {"completed", "aborted"}
        ] if args.resume else []
        completed = len(existing)
        active_at_resume = simulation.db.active_session(simulation.user_id) if args.resume else None
        for index in range(len(existing), args.sessions):
            week, day = divmod(index, 3)
            moment = start + timedelta(days=week * 7 + [0, 2, 4][day])
            if simulation.run_session(
                index + 1,
                moment,
                resume=bool(active_at_resume and index == len(existing)),
            ):
                completed += 1
            else:
                print(f"SESSION {index + 1}/{args.sessions}: failed; stopping to preserve the original failure")
                break
            print(f"SESSION {index + 1}/{args.sessions}: completed")
        report = simulation.evaluate(args.sessions)
    report_path = output_dir / f"{prefix}_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REPORT:")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
