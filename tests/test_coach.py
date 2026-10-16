import tempfile
import unittest
import sqlite3
from pathlib import Path

from coachbot.coach import Coach
from coachbot.database import Database
from coachbot.knowledge import KnowledgeBase
from coachbot.engine import ValidationError


ROOT = Path(__file__).resolve().parent.parent


class FakeLLM:
    def __init__(self, plans):
        self.plans = list(plans)

    def plan(self, _context, _text):
        return self.plans.pop(0)

    def finalize(self, **_kwargs):
        return "Готово."


class CoachFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.sqlite3")
        self.knowledge = KnowledgeBase(ROOT / "knowledge.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_natural_information_reply_is_not_truncated_or_treated_as_a_command(self):
        detailed_reply = "Программа и история:\n" + "Тренировочные данные. " * 40
        coach = Coach(self.db, self.knowledge, FakeLLM([{
            "actions": [],
            "draft_reply": detailed_reply,
        }]))

        answer = coach.handle_message(
            platform="test", external_user_id="info", display_name="Tester",
            event_id="info-1", text="Расскажи подробно о моих тренировках и программе.",
        )

        self.assertEqual(answer, detailed_reply)
        self.assertGreater(len(answer), 300)

    def test_natural_message_can_create_and_record_workout(self):
        user_id = self.db.get_or_create_user("test", "1", "Tester")
        program = self.db.propose_program(
            user_id,
            {
                "title": "Test program",
                "days": [
                    {
                        "name": "A",
                        "exercises": [
                            {
                                "exercise": "Жим лёжа", "sets": 3, "reps_min": 4, "reps_max": 6,
                                "target_rir": 2, "rest_sec": 180, "substitutions": [],
                            }
                        ],
                    }
                ],
            },
        )
        self.db.activate_program(user_id, program["program_id"])
        llm = FakeLLM(
            [
                {
                    "actions": [
                        {"type": "update_profile", "values": {"priority": "strength", "smallest_weight_increment_kg": 2.5}},
                        {"type": "save_baseline", "exercise": "Жим лёжа", "weight_kg": 100, "reps": 5, "rir": 2},
                        {"type": "start_workout", "session_type": "strength", "readiness": {"fatigue": 2}},
                        {
                            "type": "plan_set", "exercise": "Жим лёжа", "weight_kg": 100, "reps": 5,
                            "target_rir": 2, "rest_sec": 180, "is_warmup": False,
                            "rationale": "Recent baseline", "evidence_ids": ["ROBINSON_2024_RIR"],
                        },
                    ],
                    "draft_reply": "Начинаем.",
                },
                {
                    "actions": [
                        {
                            "type": "record_set", "exercise": "Жим лёжа", "weight_kg": 100,
                            "reps": 5, "rir": 2, "technique_ok": True, "pain": False,
                        }
                    ],
                    "draft_reply": "Подход принят.",
                },
            ]
        )
        coach = Coach(self.db, self.knowledge, llm)
        coach.handle_message(
            platform="test", external_user_id="1", display_name="Tester", event_id="1",
            text="Сегодня готов тренироваться, мой жим 100 на 5 с запасом 2.",
        )
        coach.handle_message(
            platform="test", external_user_id="1", display_name="Tester", event_id="2",
            text="Сделал 100 на 5, запас 2, техника нормальная, боли нет.",
        )
        session = self.db.active_session(user_id)
        self.assertEqual(session["sets"][0]["actual_weight_kg"], 100)
        self.assertEqual(session["sets"][0]["actual_reps"], 5)

    def test_set_cannot_leave_active_program_range(self):
        user_id = self.db.get_or_create_user("test", "2", "Tester")
        proposed = self.db.propose_program(
            user_id,
            {
                "title": "Strict",
                "days": [
                    {
                        "name": "A",
                        "exercises": [
                            {
                                "exercise": "Жим лёжа",
                                "sets": 3,
                                "reps_min": 4,
                                "reps_max": 6,
                                "target_rir": 2,
                                "rest_sec": 180,
                                "substitutions": [],
                            }
                        ],
                    }
                ],
            },
        )
        self.db.activate_program(user_id, proposed["program_id"])
        self.db.save_baseline(user_id, {"exercise": "Жим лёжа", "weight_kg": 100, "reps": 5, "rir": 2})
        self.db.start_session(user_id, {"session_type": "strength"})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))
        with self.assertRaises(ValidationError):
            coach._execute(
                user_id,
                {
                    "type": "plan_set",
                    "exercise": "Жим лёжа",
                    "weight_kg": 100,
                    "reps": 8,
                    "target_rir": 2,
                    "rest_sec": 120,
                    "is_warmup": False,
                },
                "",
            )

    def test_rejected_llm_action_is_audited(self):
        llm = FakeLLM(
            [
                {
                    "actions": [{"type": "start_workout", "session_type": "strength", "readiness": {}}],
                    "draft_reply": "Начинаем.",
                }
            ]
        )
        coach = Coach(self.db, self.knowledge, llm)
        coach.handle_message(
            platform="test", external_user_id="3", display_name="Tester", event_id="bad-1",
            text="Начинаем без программы.",
        )
        connection = sqlite3.connect(self.db.path)
        try:
            result_json = connection.execute(
                "SELECT result_json FROM recommendations ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertIn('"ok": false', result_json)

    def test_cannot_skip_remaining_programmed_sets(self):
        user_id = self.db.get_or_create_user("test", "4", "Tester")
        program = self.db.propose_program(
            user_id,
            {
                "title": "Strict",
                "days": [{"name": "A", "exercises": [
                    {"exercise": "Присед", "sets": 4, "reps_min": 5, "reps_max": 5, "target_rir": 2, "rest_sec": 180, "substitutions": []},
                    {"exercise": "Жим", "sets": 3, "reps_min": 5, "reps_max": 5, "target_rir": 2, "rest_sec": 180, "substitutions": []},
                ]}],
            },
        )
        self.db.activate_program(user_id, program["program_id"])
        self.db.save_baseline(user_id, {"exercise": "Присед", "weight_kg": 100, "reps": 5, "rir": 2})
        self.db.save_baseline(user_id, {"exercise": "Жим", "weight_kg": 80, "reps": 5, "rir": 2})
        self.db.start_session(user_id, {"session_type": "strength"})
        self.db.save_session_plan(user_id, {"exercises": [
            {"exercise": "Присед", "sets": 4}, {"exercise": "Жим", "sets": 3}
        ]})
        for _ in range(3):
            self.db.plan_set(user_id, {"exercise": "Присед", "weight_kg": 100, "reps": 5, "target_rir": 2, "rest_sec": 180, "is_warmup": False})
            self.db.record_set(user_id, {"exercise": "Присед", "weight_kg": 100, "reps": 5, "rir": 2, "technique_ok": True, "pain": False})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))
        with self.assertRaises(ValidationError):
            coach._execute(
                user_id,
                {"type": "plan_set", "exercise": "Жим", "weight_kg": 80, "reps": 5, "target_rir": 2, "rest_sec": 180},
                "",
            )
        with self.assertRaises(ValidationError):
            coach._execute(
                user_id,
                {"type": "complete_workout", "duration_minutes": 60, "session_rpe": 7, "aborted": False},
                "",
            )

    def test_completed_workout_requires_reported_duration_and_rpe(self):
        user_id = self.db.get_or_create_user("test", "5", "Tester")
        program = self.db.propose_program(user_id, {"title": "A", "days": [{}]})
        self.db.activate_program(user_id, program["program_id"])
        self.db.start_session(user_id, {"session_type": "strength"})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))
        with self.assertRaises(ValidationError):
            coach._execute(
                user_id,
                {"type": "complete_workout", "duration_minutes": None, "session_rpe": None, "aborted": False},
                "",
            )

    def test_explicit_fatigue_adjustment_can_reduce_an_existing_plan(self):
        user_id = self.db.get_or_create_user("test", "6", "Tester")
        program = self.db.propose_program(
            user_id,
            {"title": "A", "days": [{"exercises": [{
                "exercise": "Присед", "sets": 4, "reps_min": 5, "reps_max": 5,
                "target_rir": 2, "rest_sec": 180, "substitutions": [],
            }]}]},
        )
        self.db.activate_program(user_id, program["program_id"])
        self.db.start_session(user_id, {"session_type": "strength"})
        self.db.save_session_plan(user_id, {"exercises": [{"exercise": "Присед", "sets": 4}]})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))

        result, _ = coach._execute(
            user_id,
            {"type": "adjust_session_exercise", "exercise": "Присед", "new_sets": 0, "reason": "fatigue"},
            "",
        )

        self.assertEqual(result["new_sets"], 0)
        self.assertEqual(self.db.active_session(user_id)["plan"]["exercises"][0]["sets"], 0)

    def test_time_limit_cannot_be_invented(self):
        user_id = self.db.get_or_create_user("test", "15", "Tester")
        exercise = {
            "exercise": "Присед", "sets": 3, "reps_min": 5, "reps_max": 5,
            "target_rir": 2, "rest_sec": 180, "substitutions": [],
        }
        program = self.db.propose_program(user_id, {"title": "A", "days": [{"name": "A", "exercises": [exercise]}]})
        self.db.activate_program(user_id, program["program_id"])
        self.db.start_session(user_id, {"session_type": "strength"})
        self.db.save_session_plan(user_id, {"exercises": [exercise]})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))
        action = {
            "type": "adjust_session_exercise", "exercise": "Присед",
            "new_sets": 0, "reason": "time_limit",
        }

        with self.assertRaisesRegex(ValidationError, "explicit user time constraint"):
            coach._execute(user_id, action, "На сегодня закончил, тренировка длилась 65 минут.")
        result, _ = coach._execute(user_id, action, "Не успеваю, надо уходить.")
        self.assertEqual(result["new_sets"], 0)

    def test_current_day_rule_wins_when_exercise_repeats_across_program_days(self):
        user_id = self.db.get_or_create_user("test", "7", "Tester")
        exercise = {
            "exercise": "Подтягивания", "sets": 3, "reps_min": 5, "reps_max": 6,
            "target_rir": 2, "rest_sec": 150, "substitutions": [],
        }
        program = self.db.propose_program(
            user_id,
            {"title": "A/B", "days": [
                {"name": "A", "exercises": [exercise]},
                {"name": "B", "exercises": [{**exercise, "sets": 4}]},
            ]},
        )
        self.db.activate_program(user_id, program["program_id"])
        self.db.save_baseline(user_id, {"exercise": "Подтягивания", "weight_kg": 15, "reps": 5, "rir": 2})
        self.db.start_session(user_id, {"session_type": "strength"})
        self.db.save_session_plan(user_id, {"exercises": [{**exercise, "sets": 4}]})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))
        for _ in range(3):
            coach._execute(user_id, {
                "type": "plan_set", "exercise": "Подтягивания", "weight_kg": 15,
                "reps": 5, "target_rir": 2, "rest_sec": 150, "is_warmup": False,
            }, "")
            coach._execute(user_id, {
                "type": "record_set", "exercise": "Подтягивания", "weight_kg": 15,
                "reps": 5, "rir": 2, "technique_ok": True, "pain": False,
            }, "")

        result, _ = coach._execute(user_id, {
            "type": "plan_set", "exercise": "Подтягивания", "weight_kg": 15,
            "reps": 5, "target_rir": 2, "rest_sec": 150, "is_warmup": False,
        }, "")

        self.assertEqual(result["set_number"], 4)

    def test_unplanned_easy_calibration_does_not_count_as_working_volume(self):
        user_id = self.db.get_or_create_user("test", "8", "Tester")
        exercise = {
            "exercise": "Румынская тяга", "sets": 3, "reps_min": 6, "reps_max": 8,
            "target_rir": 2, "rest_sec": 150, "substitutions": [],
        }
        program = self.db.propose_program(user_id, {"title": "A", "days": [{"name": "A", "exercises": [exercise]}]})
        self.db.activate_program(user_id, program["program_id"])
        self.db.start_session(user_id, {"session_type": "strength"})
        self.db.save_session_plan(user_id, {"exercises": [exercise]})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))

        coach._execute(user_id, {
            "type": "record_set", "exercise": "Румынская тяга", "weight_kg": 70,
            "reps": 5, "rir": 5, "technique_ok": True, "pain": False, "is_warmup": False,
        }, "")

        session = self.db.active_session(user_id)
        self.assertEqual(session["sets"][0]["is_warmup"], 1)
        self.assertEqual(coach._completed_working_sets(session, "Румынская тяга"), 0)

    def test_failed_transition_recovers_last_missing_working_set(self):
        user_id = self.db.get_or_create_user("test", "9", "Tester")
        throw = {
            "exercise": "Бросок медбола", "sets": 5, "reps_min": 3, "reps_max": 5,
            "target_rir": None, "rest_sec": 90, "substitutions": [],
        }
        jump = {
            "exercise": "Прыжок", "sets": 3, "reps_min": 3, "reps_max": 5,
            "target_rir": None, "rest_sec": 90, "substitutions": [],
        }
        program = self.db.propose_program(
            user_id, {"title": "Power", "days": [{"name": "C", "exercises": [throw, jump]}]},
        )
        self.db.activate_program(user_id, program["program_id"])
        self.db.start_session(user_id, {"session_type": "power"})
        self.db.save_session_plan(user_id, {"exercises": [throw, jump]})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))
        for warmup in [True, False, False, False, False]:
            coach._execute(user_id, {
                "type": "plan_set", "exercise": "Бросок медбола", "weight_kg": 0,
                "reps": 4, "target_rir": None, "rest_sec": 90, "is_warmup": warmup,
            }, "")
            coach._execute(user_id, {
                "type": "record_set", "exercise": "Бросок медбола", "weight_kg": 0,
                "reps": 4, "rir": None, "quality_ok": True, "technique_ok": True,
                "pain": False, "is_warmup": warmup,
            }, "")

        recovered = coach._recover_skipped_set(user_id)

        self.assertIsNotNone(recovered)
        pending = [item for item in self.db.active_session(user_id)["sets"] if item["actual_weight_kg"] is None]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["exercise_key"], "бросок медбола")
        self.assertEqual(pending[0]["is_warmup"], 0)

    def test_recovery_can_start_next_exercise_with_a_known_baseline(self):
        user_id = self.db.get_or_create_user("test", "14", "Tester")
        first = {
            "exercise": "Жим", "sets": 1, "reps_min": 5, "reps_max": 5,
            "target_rir": 2, "rest_sec": 180, "substitutions": [], "load_kg": 80,
        }
        second = {
            "exercise": "Подтягивания", "sets": 3, "reps_min": 5, "reps_max": 6,
            "target_rir": 2, "rest_sec": 150, "substitutions": [], "load_kg": 15,
        }
        program = self.db.propose_program(user_id, {"title": "A", "days": [{"name": "A", "exercises": [first, second]}]})
        self.db.activate_program(user_id, program["program_id"])
        self.db.save_baseline(user_id, {"exercise": "Жим", "weight_kg": 80, "reps": 5, "rir": 2})
        self.db.save_baseline(user_id, {"exercise": "Подтягивания", "weight_kg": 15, "reps": 5, "rir": 2})
        self.db.start_session(user_id, {"session_type": "strength"})
        self.db.save_session_plan(user_id, {"exercises": [first, second]})
        self.db.plan_set(user_id, {
            "exercise": "Жим", "weight_kg": 80, "reps": 5, "target_rir": 2,
            "rest_sec": 180, "is_warmup": False,
        })
        self.db.record_set(user_id, {
            "exercise": "Жим", "weight_kg": 80, "reps": 5, "rir": 2,
            "technique_ok": True, "pain": False,
        })
        coach = Coach(self.db, self.knowledge, FakeLLM([]))

        recovered = coach._recover_skipped_set(user_id)

        self.assertIsNotNone(recovered)
        pending = [item for item in self.db.active_session(user_id)["sets"] if item["actual_weight_kg"] is None]
        self.assertEqual(pending[0]["exercise_key"], "подтягивания")

    def test_premature_completion_plans_the_missing_set(self):
        user_id = self.db.get_or_create_user("test", "10", "Tester")
        exercise = {
            "exercise": "Румынская тяга", "sets": 3, "reps_min": 6, "reps_max": 8,
            "target_rir": 2, "rest_sec": 150, "substitutions": [],
        }
        program = self.db.propose_program(user_id, {"title": "A", "days": [{"name": "A", "exercises": [exercise]}]})
        self.db.activate_program(user_id, program["program_id"])
        self.db.start_session(user_id, {"session_type": "strength"})
        self.db.save_session_plan(user_id, {"exercises": [exercise]})
        self.db.save_baseline(user_id, {"exercise": "Румынская тяга", "weight_kg": 70, "reps": 5, "rir": 5})
        for warmup in [True, False, False]:
            self.db.plan_set(user_id, {
                "exercise": "Румынская тяга", "weight_kg": 70 if warmup else 72.5,
                "reps": 5 if warmup else 8, "target_rir": 2, "rest_sec": 150,
                "is_warmup": warmup,
            })
            self.db.record_set(user_id, {
                "exercise": "Румынская тяга", "weight_kg": 70 if warmup else 72.5,
                "reps": 5 if warmup else 8, "rir": 5 if warmup else 2,
                "technique_ok": True, "pain": False, "is_warmup": warmup,
            })
        llm = FakeLLM([{
            "actions": [{
                "type": "complete_workout", "duration_minutes": 65,
                "session_rpe": 7, "aborted": False,
            }],
            "draft_reply": "Готово.",
        }])

        Coach(self.db, self.knowledge, llm).handle_message(
            platform="test", external_user_id="10", display_name="Tester",
            event_id="premature-complete", text="Закончил, 65 минут, RPE 7.",
        )

        session = self.db.active_session(user_id)
        pending = [item for item in session["sets"] if item["actual_weight_kg"] is None]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["exercise_key"], "румынская тяга")

    def test_aborted_day_still_advances_program_rotation(self):
        user_id = self.db.get_or_create_user("test", "11", "Tester")
        days = [
            {"name": name, "exercises": [{
                "exercise": exercise, "sets": 1, "reps_min": 5, "reps_max": 5,
                "target_rir": 2, "rest_sec": 120, "substitutions": [],
            }]}
            for name, exercise in [("A", "Присед"), ("B", "Жим"), ("C", "Прыжок")]
        ]
        program = self.db.propose_program(user_id, {"title": "ABC", "days": days})
        self.db.activate_program(user_id, program["program_id"])
        coach = Coach(self.db, self.knowledge, FakeLLM([]))
        self.db.start_session(user_id, {"session_type": "strength"})
        self.db.complete_session(user_id, {"aborted": True})

        self.assertEqual(coach._scheduled_program_day(user_id)["name"], "B")
        self.db.start_session(user_id, {"session_type": "strength"})
        with self.assertRaisesRegex(ValidationError, "scheduled program day"):
            coach._execute(user_id, {
                "type": "save_session_plan", "exercises": [days[0]["exercises"][0]],
            }, "")

    def test_three_failed_sessions_block_another_working_set(self):
        user_id = self.db.get_or_create_user("test", "12", "Tester")
        exercise = {
            "exercise": "Жим", "sets": 1, "reps_min": 5, "reps_max": 5,
            "target_rir": 2, "rest_sec": 180, "substitutions": [],
        }
        program = self.db.propose_program(user_id, {"title": "Plateau", "days": [{"name": "A", "exercises": [exercise]}]})
        self.db.activate_program(user_id, program["program_id"])
        self.db.save_baseline(user_id, {"exercise": "Жим", "weight_kg": 80, "reps": 5, "rir": 2})
        for _ in range(3):
            self.db.start_session(user_id, {"session_type": "strength"})
            self.db.plan_set(user_id, {
                "exercise": "Жим", "weight_kg": 80, "reps": 5, "target_rir": 2,
                "rest_sec": 180, "is_warmup": False,
            })
            self.db.record_set(user_id, {
                "exercise": "Жим", "weight_kg": 80, "reps": 4, "rir": 0,
                "technique_ok": True, "pain": False,
            })
            self.db.complete_session(user_id, {"duration_minutes": 30, "session_rpe": 8})
        self.db.start_session(user_id, {"session_type": "strength"})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))

        with self.assertRaisesRegex(ValidationError, "Repeated failures"):
            coach._execute(user_id, {
                "type": "plan_set", "exercise": "Жим", "weight_kg": 70,
                "reps": 5, "target_rir": 2, "rest_sec": 180, "is_warmup": False,
            }, "")

    def test_three_boxing_days_clamp_initial_gym_volume(self):
        user_id = self.db.get_or_create_user("test", "13", "Tester")
        self.db.update_profile(user_id, {"boxing_schedule": ["вт", "чт", "сб"]})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))
        exercises = [
            {
                "exercise": f"Упражнение {index}", "sets": sets,
                "reps_min": 5, "reps_max": 5, "target_rir": 2,
                "rest_sec": 120, "substitutions": [],
            }
            for index, sets in enumerate([4, 4, 5], 1)
        ]

        result, _ = coach._execute(user_id, {
            "type": "propose_program", "title": "Too much",
            "days": [{"name": "A", "exercises": exercises}],
            "evidence_ids": [],
        }, "")

        saved = self.db.program(user_id, "proposed")
        self.assertEqual(sum(item["sets"] for item in saved["days"][0]["exercises"]), 12)
        self.assertEqual(result["volume_corrected_by_engine"], ["A"])

    def test_volume_guardrail_balances_sets_instead_of_crushing_last_exercise(self):
        user_id = self.db.get_or_create_user("test", "balanced-volume", "Tester")
        self.db.update_profile(user_id, {"boxing_schedule": ["вт", "чт", "сб"]})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))
        exercises = [
            {
                "exercise": f"Упражнение {index}", "sets": sets,
                "reps_min": 5, "reps_max": 5, "target_rir": 2,
                "rest_sec": 120, "substitutions": [],
            }
            for index, sets in enumerate([4, 4, 3, 3], 1)
        ]

        coach._execute(user_id, {
            "type": "propose_program", "title": "Balanced",
            "days": [{"name": "A", "exercises": exercises}], "evidence_ids": [],
        }, "")

        saved_sets = [item["sets"] for item in self.db.program(user_id, "proposed")["days"][0]["exercises"]]
        self.assertEqual(saved_sets, [3, 3, 3, 3])

    def test_heavy_deadlift_day_reduces_other_compound_work_and_moves_power_first(self):
        user_id = self.db.get_or_create_user("test", "deadlift-balance", "Tester")
        self.db.update_profile(user_id, {"boxing_schedule": ["вт", "чт", "сб"]})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))
        exercises = [
            {"exercise": "становая", "sets": 3, "reps_min": 3, "reps_max": 4, "target_rir": 2, "rest_sec": 210, "substitutions": []},
            {"exercise": "жим лёжа", "sets": 3, "reps_min": 5, "reps_max": 6, "target_rir": 2, "rest_sec": 180, "substitutions": []},
            {"exercise": "подтягивания с весом", "sets": 3, "reps_min": 5, "reps_max": 6, "target_rir": 2, "rest_sec": 150, "substitutions": []},
            {"exercise": "мах гири", "sets": 3, "reps_min": 6, "reps_max": 8, "target_rir": None, "rest_sec": 90, "substitutions": []},
        ]

        result, _ = coach._execute(user_id, {
            "type": "propose_program", "title": "Balanced deadlift",
            "days": [{"name": "B", "exercises": exercises}], "evidence_ids": [],
        }, "")

        saved = self.db.program(user_id, "proposed")["days"][0]["exercises"]
        self.assertEqual([item["exercise"] for item in saved], ["мах гири", "становая", "жим лёжа", "подтягивания с весом"])
        self.assertEqual([(item["sets"], item["target_rir"]) for item in saved[1:]], [(3, 2), (2, 3), (2, 3)])
        self.assertIn("combined_stress", result["program_corrections"]["B"])
        self.assertIn("explosive_order", result["program_corrections"]["B"])

    def test_third_full_effort_compound_lift_is_reduced_without_deadlift(self):
        user_id = self.db.get_or_create_user("test", "three-compounds", "Tester")
        coach = Coach(self.db, self.knowledge, FakeLLM([]))
        exercises = [
            {"exercise": "присед", "sets": 3, "reps_min": 4, "reps_max": 5, "target_rir": 2, "rest_sec": 180, "substitutions": []},
            {"exercise": "жим лёжа", "sets": 3, "reps_min": 4, "reps_max": 5, "target_rir": 2, "rest_sec": 180, "substitutions": []},
            {"exercise": "подтягивания с весом", "sets": 3, "reps_min": 5, "reps_max": 6, "target_rir": 2, "rest_sec": 150, "substitutions": []},
        ]

        coach._execute(user_id, {
            "type": "propose_program", "title": "Three compounds",
            "days": [{"name": "A", "exercises": exercises}], "evidence_ids": [],
        }, "")

        saved = self.db.program(user_id, "proposed")["days"][0]["exercises"]
        self.assertEqual([(item["sets"], item["target_rir"]) for item in saved], [(3, 2), (3, 2), (2, 3)])

    def test_short_explosive_day_is_capped_below_general_boxing_guardrail(self):
        user_id = self.db.get_or_create_user("test", "short-power", "Tester")
        self.db.update_profile(user_id, {"boxing_schedule": ["вт", "чт", "сб"]})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))
        exercises = [
            {"exercise": name, "sets": sets, "reps_min": 3, "reps_max": 5, "target_rir": None, "rest_sec": 90, "substitutions": []}
            for name, sets in [("прыжки", 3), ("бросок медбола", 4), ("мах гири", 3)]
        ] + [{"exercise": "присед", "sets": 2, "reps_min": 3, "reps_max": 3, "target_rir": 3, "rest_sec": 180, "substitutions": []}]

        coach._execute(user_id, {
            "type": "propose_program", "title": "Short power",
            "days": [{"name": "C — короткая взрывная", "exercises": exercises}],
            "rationale": "В этом дне должно быть 15 рабочих подходов.", "evidence_ids": [],
        }, "")

        program = self.db.program(user_id, "proposed")
        self.assertEqual(sum(item["sets"] for item in program["days"][0]["exercises"]), 8)
        self.assertNotIn("15", program["rationale"])
        self.assertIn("Движок скорректировал", program["rationale"])

    def test_pre_boxing_day_reduces_shoulder_or_axial_strength_work(self):
        user_id = self.db.get_or_create_user("test", "boxing-interference", "Tester")
        self.db.update_profile(user_id, {"boxing_schedule": ["вт", "чт", "сб"]})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))
        exercises = [
            {"exercise": "прыжок на тумбу", "sets": 2, "reps_min": 3, "reps_max": 3, "target_rir": None, "rest_sec": 90, "substitutions": []},
            {"exercise": "жим стоя", "sets": 4, "reps_min": 4, "reps_max": 5, "target_rir": 2, "rest_sec": 180, "substitutions": []},
        ]

        result, _ = coach._execute(user_id, {
            "type": "propose_program", "title": "Pre-boxing",
            "days": [{"name": "C — короткая", "exercises": exercises}], "evidence_ids": [],
        }, "")

        saved = self.db.program(user_id, "proposed")["days"][0]["exercises"][1]
        self.assertEqual((saved["sets"], saved["target_rir"]), (2, 3))
        self.assertIn("boxing_interference", result["program_corrections"]["C — короткая"])

    def test_isometric_cannot_be_mislabeled_as_explosive_work(self):
        user_id = self.db.get_or_create_user("test", "plank-speed", "Tester")
        coach = Coach(self.db, self.knowledge, FakeLLM([]))

        with self.assertRaisesRegex(ValidationError, "only for explosive"):
            coach._execute(user_id, {
                "type": "propose_program", "title": "Invalid plank",
                "days": [{"name": "A", "exercises": [{
                    "exercise": "планка", "sets": 3, "reps_min": 1, "reps_max": 1,
                    "target_rir": None, "rest_sec": 60, "substitutions": [],
                }]}], "evidence_ids": [],
            }, "")

    def test_complete_workout_is_prepared_and_recorded_from_one_report(self):
        user_id = self.db.get_or_create_user("test", "whole-workout", "Tester")
        strength = {
            "exercise": "Присед", "sets": 2, "reps_min": 5, "reps_max": 5,
            "target_rir": 2, "rest_sec": 180, "substitutions": [],
        }
        explosive = {
            "exercise": "Прыжок на тумбу", "sets": 2, "reps_min": 3, "reps_max": 3,
            "target_rir": None, "rest_sec": 90, "substitutions": [],
        }
        program = self.db.propose_program(
            user_id, {"title": "Whole", "days": [{"name": "A", "exercises": [strength, explosive]}]},
        )
        self.db.activate_program(user_id, program["program_id"])
        self.db.save_baseline(user_id, {
            "exercise": "Присед", "weight_kg": 100, "reps": 5, "rir": 2,
        })
        self.db.start_session(user_id, {"session_type": "mixed", "readiness": {"fatigue": 3}})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))

        prepared, _ = coach._execute(user_id, {
            "type": "prepare_workout", "summary": "Вся тренировка",
            "exercises": [
                {"exercise": "Присед", "sets": 2, "reps": 5, "weight_kg": 100, "target_rir": 2, "rest_sec": 180},
                {"exercise": "Прыжок на тумбу", "sets": 2, "reps": 3, "weight_kg": 0, "target_rir": None, "rest_sec": 90},
            ],
        }, "")
        self.assertEqual([item["sets"] for item in prepared["exercises"]], [2, 2])

        completed, _ = coach._execute(user_id, {
            "type": "record_workout",
            "exercises": [
                {"exercise": "Присед", "sets_completed": 2, "weight_kg": 100, "reps": 5,
                 "rir": 2, "quality_ok": None, "technique_ok": True, "pain": False},
                {"exercise": "Прыжок на тумбу", "sets_completed": 2, "weight_kg": 0, "reps": 3,
                 "rir": None, "quality_ok": True, "technique_ok": True, "pain": False},
            ],
            "duration_minutes": 45, "session_rpe": 7, "notes": "Всё по плану", "aborted": False,
        }, "")

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["recorded_sets"], 4)
        session = self.db.recent_sessions(user_id, 1)[0]
        self.assertEqual(len(session["sets"]), 4)
        self.assertEqual(session["sets"][0]["planned_reps"], 5)
        self.assertEqual(session["sets"][0]["target_rir"], 2)

    def test_unweighted_pullup_can_reuse_weighted_pullup_baseline(self):
        user_id = self.db.get_or_create_user("test", "pullup-variant", "Tester")
        self.db.update_profile(user_id, {"bodyweight_kg": 83})
        exercise = {
            "exercise": "Подтягивания", "sets": 2, "reps_min": 6, "reps_max": 8,
            "target_rir": 3, "rest_sec": 120, "substitutions": [],
        }
        program = self.db.propose_program(
            user_id, {"title": "Pull-ups", "days": [{"name": "A", "exercises": [exercise]}]},
        )
        self.db.activate_program(user_id, program["program_id"])
        self.db.save_baseline(user_id, {
            "exercise": "Подтягивания с весом", "weight_kg": 10, "bodyweight_kg": 83,
            "reps": 5, "rir": 4,
        })
        self.db.start_session(user_id, {"session_type": "strength"})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))

        prepared, _ = coach._execute(user_id, {
            "type": "prepare_workout", "exercises": [{
                "exercise": "Подтягивания", "sets": 2, "reps": 6,
                "weight_kg": 0, "target_rir": 3, "rest_sec": 120,
            }],
        }, "")

        self.assertEqual(prepared["exercises"][0]["load_kg"], 0)

    def test_whole_workout_report_rejects_sets_after_explosive_quality_drop(self):
        user_id = self.db.get_or_create_user("test", "quality-stop", "Tester")
        exercise = {
            "exercise": "Бросок медбола", "sets": 3, "reps_min": 3, "reps_max": 3,
            "target_rir": None, "rest_sec": 90, "substitutions": [],
        }
        program = self.db.propose_program(
            user_id, {"title": "Power", "days": [{"name": "A", "exercises": [exercise]}]},
        )
        self.db.activate_program(user_id, program["program_id"])
        self.db.start_session(user_id, {"session_type": "power"})
        coach = Coach(self.db, self.knowledge, FakeLLM([]))
        coach._execute(user_id, {
            "type": "prepare_workout", "exercises": [
                {"exercise": "Бросок медбола", "sets": 3, "reps": 3, "weight_kg": 0, "target_rir": None, "rest_sec": 90},
            ],
        }, "")

        with self.assertRaisesRegex(ValidationError, "No additional sets"):
            coach._execute(user_id, {
                "type": "record_workout", "duration_minutes": 20, "session_rpe": 5, "aborted": False,
                "exercises": [{"exercise": "Бросок медбола", "sets": [
                    {"weight_kg": 0, "reps": 3, "rir": None, "quality_ok": False, "technique_ok": True, "pain": False},
                    {"weight_kg": 0, "reps": 3, "rir": None, "quality_ok": True, "technique_ok": True, "pain": False},
                ]}],
            }, "")

    def test_user_facing_reply_never_exposes_rir_acronym(self):
        self.assertEqual(
            Coach._humanize_effort_terms("Присед: RIR 2. На прыжках RIR не оценивай."),
            "Присед: около 2 чистых повторов в запасе. На прыжках повторы в запасе не оценивай.",
        )

    def test_proposed_program_reply_is_rendered_from_saved_program(self):
        llm = FakeLLM([{
            "actions": [{
                "type": "propose_program", "title": "Точный план",
                "days": [{"name": "День A", "exercises": [{
                    "exercise": "Присед", "sets": 3, "reps_min": 5, "reps_max": 5,
                    "target_rir": 2, "rest_sec": 180, "load_kg": 100, "substitutions": [],
                }]}],
                "rationale": "Проверка", "evidence_ids": [],
            }],
            "draft_reply": "Другая программа: выпады и скручивания.",
        }])

        answer = Coach(self.db, self.knowledge, llm).handle_message(
            platform="test", external_user_id="exact-program", display_name="Tester",
            event_id="proposal", text="Составь программу.",
        )

        self.assertIn("Присед", answer)
        self.assertNotIn("выпады", answer)
        self.assertNotIn("скручивания", answer)
        self.assertNotIn("RIR", answer)


if __name__ == "__main__":
    unittest.main()
