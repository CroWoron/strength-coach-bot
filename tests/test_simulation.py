import unittest

from simulate_training import LiveSimulation


class SimulationTests(unittest.TestCase):
    def test_calibration_uses_plausible_overhead_and_fixed_implement_loads(self):
        self.assertIn("40 кг", LiveSimulation.calibration_message("Жим стоя"))
        self.assertIn("16 кг", LiveSimulation.calibration_message("Жим гири одной рукой"))

    def test_trial_week_includes_an_easy_bench_result_for_adaptation(self):
        message = LiveSimulation.workout_report_message(1, {"plan": {"exercises": [{
            "exercise": "жим лёжа", "sets": 3, "reps": 5, "load_kg": 80, "target_rir": 2,
        }]}})
        self.assertIn("4 чистых повтора", message)

    def test_pain_abort_is_handled_but_unexplained_abort_is_not(self):
        self.assertTrue(LiveSimulation._session_was_handled({
            "status": "aborted", "sets": [{"pain": 1}],
        }))
        self.assertFalse(LiveSimulation._session_was_handled({
            "status": "aborted", "sets": [{"pain": 0}],
        }))

    def test_high_fatigue_or_technique_abort_is_handled(self):
        self.assertTrue(LiveSimulation._session_was_handled({
            "status": "aborted", "readiness": {"fatigue": 8}, "sets": [],
        }))
        self.assertTrue(LiveSimulation._session_was_handled({
            "status": "aborted", "readiness": {}, "sets": [{"technique_ok": 0}],
        }))

    def test_program_quality_rejects_the_old_overloaded_week(self):
        violations = LiveSimulation.program_quality_violations([{
            "name": "B — становая + жим",
            "exercises": [
                {"exercise": "становая", "sets": 3, "reps_max": 4, "target_rir": 2},
                {"exercise": "жим лёжа", "sets": 3, "reps_max": 6, "target_rir": 2},
                {"exercise": "подтягивания с весом", "sets": 3, "reps_max": 6, "target_rir": 2},
                {"exercise": "мах гири", "sets": 3, "reps_max": 8, "target_rir": None},
            ],
        }], 3)
        self.assertTrue(any("explosive work" in item for item in violations))
        self.assertTrue(any("heavy deadlift" in item for item in violations))


if __name__ == "__main__":
    unittest.main()
