import tempfile
import unittest
from pathlib import Path

from coachbot.database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.sqlite3")
        self.user_id = self.db.get_or_create_user("test", "1", "Tester")

    def tearDown(self):
        self.temp.cleanup()

    def test_duplicate_external_message_is_ignored(self):
        self.assertTrue(self.db.remember_message("test", "one", self.user_id, "user", "hello"))
        self.assertFalse(self.db.remember_message("test", "one", self.user_id, "user", "hello"))

    def test_program_versions_are_immutable_and_only_one_is_active(self):
        first = self.db.propose_program(self.user_id, {"title": "A", "days": [{}]})
        second = self.db.propose_program(self.user_id, {"title": "B", "days": [{}]})
        self.db.activate_program(self.user_id, first["program_id"])
        self.db.activate_program(self.user_id, second["program_id"])
        self.assertEqual(self.db.program(self.user_id)["title"], "B")
        self.assertEqual(self.db.program(self.user_id)["version"], 2)

    def test_session_and_set_survive_reopen(self):
        program = self.db.propose_program(self.user_id, {"title": "A", "days": [{}]})
        self.db.activate_program(self.user_id, program["program_id"])
        self.db.start_session(self.user_id, {"session_type": "strength", "readiness": {"fatigue": 2}})
        planned = self.db.plan_set(
            self.user_id,
            {
                "exercise": "Жим лёжа",
                "weight_kg": 100,
                "reps": 5,
                "target_rir": 2,
                "rest_sec": 180,
                "is_warmup": False,
                "rationale": "",
                "evidence_ids": [],
            },
        )
        self.db.record_set(
            self.user_id,
            {"exercise": "Жим лёжа", "weight_kg": 100, "reps": 5, "rir": 2, "technique_ok": True, "pain": False},
        )
        reopened = Database(self.db.path).active_session(self.user_id)
        self.assertEqual(reopened["sets"][0]["id"], planned["set_id"])
        self.assertEqual(reopened["sets"][0]["actual_reps"], 5)

    def test_regular_workout_requires_active_program(self):
        with self.assertRaises(ValueError):
            self.db.start_session(self.user_id, {"session_type": "strength"})
        assessment = self.db.start_session(self.user_id, {"session_type": "assessment"})
        self.assertEqual(assessment["session_type"], "assessment")

    def test_unfinished_workout_cannot_be_restarted_or_replanned(self):
        program = self.db.propose_program(self.user_id, {"title": "A", "days": [{}]})
        self.db.activate_program(self.user_id, program["program_id"])
        self.db.start_session(self.user_id, {"session_type": "strength"})
        first_plan = {"exercises": [{"exercise": "Присед", "sets": 3}]}
        self.db.save_session_plan(self.user_id, first_plan)

        with self.assertRaisesRegex(ValueError, "unfinished workout"):
            self.db.start_session(self.user_id, {"session_type": "strength"})
        with self.assertRaisesRegex(ValueError, "already has a plan"):
            self.db.save_session_plan(
                self.user_id,
                {"exercises": [{"exercise": "Становая", "sets": 3}]},
            )

        self.assertEqual(self.db.active_session(self.user_id)["plan"], first_plan)


if __name__ == "__main__":
    unittest.main()
