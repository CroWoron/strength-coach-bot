import time
import unittest

from coachbot.analytics import summarize


class AnalyticsTests(unittest.TestCase):
    def test_strength_and_session_load_are_separate(self):
        result = summarize(
            [
                {
                    "id": 1,
                    "session_type": "strength",
                    "status": "completed",
                    "completed_at": int(time.time()),
                    "duration_minutes": 60,
                    "session_rpe": 7,
                    "sets": [
                        {
                            "exercise_key": "жим лежа",
                            "exercise_name": "Жим лёжа",
                            "actual_weight_kg": 100,
                            "actual_reps": 5,
                            "actual_rir": 2,
                            "bodyweight_kg": None,
                            "is_warmup": 0,
                            "technique_ok": 1,
                            "pain": 0,
                            "completed_at": int(time.time()),
                        }
                    ],
                }
            ]
        )
        self.assertEqual(result["seven_day_session_load_au"], 420)
        self.assertAlmostEqual(result["strength_estimates"][0]["latest_e1rm_kg"], 123.3)
        self.assertEqual(result["recent_session_summaries"][0]["working_tonnage_kg"], 500)

    def test_unknown_rir_is_not_used_for_strength_estimate(self):
        result = summarize(
            [
                {
                    "id": 1,
                    "session_type": "strength",
                    "status": "in_progress",
                    "completed_at": None,
                    "duration_minutes": None,
                    "session_rpe": None,
                    "sets": [
                        {
                            "exercise_key": "жим",
                            "exercise_name": "Жим",
                            "actual_weight_kg": 100,
                            "actual_reps": 5,
                            "actual_rir": None,
                            "bodyweight_kg": None,
                            "is_warmup": 0,
                            "technique_ok": 1,
                            "pain": 0,
                        }
                    ],
                }
            ]
        )
        self.assertEqual(result["strength_estimates"], [])


if __name__ == "__main__":
    unittest.main()
