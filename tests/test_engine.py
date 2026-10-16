import unittest

from coachbot.engine import (
    ValidationError,
    adjust_after_set,
    brzycki,
    epley,
    epley_with_rir,
    round_load,
    target_weight,
    tonnage,
    validate_set_plan,
)


class FormulaTests(unittest.TestCase):
    def test_tonnage(self):
        self.assertEqual(tonnage([{"weight_kg": 135, "reps": 5}] * 3), 2025)

    def test_epley(self):
        self.assertAlmostEqual(epley(135, 5), 157.5)

    def test_rir_adjusted_epley(self):
        self.assertAlmostEqual(epley_with_rir(135, 5, 2), 166.5)

    def test_brzycki(self):
        self.assertAlmostEqual(brzycki(135, 5), 151.875)

    def test_brzycki_rejects_high_rep_estimate(self):
        with self.assertRaises(ValidationError):
            brzycki(100, 13)
        with self.assertRaises(ValidationError):
            brzycki(100, 37)

    def test_target_round_trip(self):
        estimate = epley_with_rir(100, 5, 2)
        self.assertAlmostEqual(target_weight(estimate, 5, 2), 100)
        self.assertEqual(round_load(101.2, 2.5, "down"), 100)


class AdjustmentTests(unittest.TestCase):
    def test_pain_stops_exercise(self):
        result = adjust_after_set(
            weight_kg=100,
            actual_reps=5,
            planned_reps=5,
            actual_rir=4,
            target_rir=2,
            technique_ok=True,
            pain=True,
        )
        self.assertEqual(result.action, "stop")
        self.assertIsNone(result.recommended_weight_kg)

    def test_bad_technique_never_increases_load(self):
        result = adjust_after_set(
            weight_kg=100,
            actual_reps=5,
            planned_reps=5,
            actual_rir=4,
            target_rir=2,
            technique_ok=False,
            pain=False,
        )
        self.assertEqual(result.action, "reduce")
        self.assertLess(result.recommended_weight_kg, 100)

    def test_easy_set_increases_one_increment(self):
        result = adjust_after_set(
            weight_kg=100,
            actual_reps=5,
            planned_reps=5,
            actual_rir=4,
            target_rir=2,
            technique_ok=True,
            pain=False,
            increment_kg=2.5,
        )
        self.assertEqual(result.recommended_weight_kg, 102.5)

    def test_unknown_rir_never_increases_load(self):
        result = adjust_after_set(
            weight_kg=100,
            actual_reps=5,
            planned_reps=5,
            actual_rir=None,
            target_rir=2,
            technique_ok=True,
            pain=False,
        )
        self.assertEqual(result.action, "hold")
        self.assertEqual(result.reason_code, "rir_unknown")

    def test_explosive_quality_drop_stops_series(self):
        result = adjust_after_set(
            weight_kg=5,
            actual_reps=4,
            planned_reps=4,
            actual_rir=None,
            target_rir=0,
            technique_ok=True,
            pain=False,
            quality_ok=False,
        )
        self.assertEqual(result.action, "stop")
        self.assertEqual(result.reason_code, "quality_drop")

    def test_quality_based_plan_does_not_require_strength_baseline(self):
        result = validate_set_plan(
            {
                "exercise": "Бросок медбола",
                "weight_kg": 5,
                "reps": 4,
                "target_rir": None,
                "rest_sec": 90,
                "quality_based": True,
            },
            increment_kg=1,
            reference=None,
            latest_adjustment=None,
        )
        self.assertIsNone(result["target_rir"])
        self.assertTrue(result["quality_based"])

    def test_quality_based_implement_weight_is_not_rounded_like_a_barbell(self):
        result = validate_set_plan(
            {
                "exercise": "Рывок гири",
                "weight_kg": 24,
                "reps": 3,
                "target_rir": None,
                "rest_sec": 120,
                "quality_based": True,
            },
            increment_kg=2.5,
            reference=None,
            latest_adjustment=None,
        )
        self.assertEqual(result["weight_kg"], 24)

    def test_fixed_implement_strength_weight_is_preserved(self):
        result = validate_set_plan(
            {
                "exercise": "Жим гири одной рукой",
                "weight_kg": 16,
                "reps": 6,
                "target_rir": 2,
                "rest_sec": 120,
            },
            increment_kg=2.5,
            reference={"weight_kg": 16, "reps": 6, "rir": 2},
            latest_adjustment=None,
            round_to_increment=False,
        )
        self.assertEqual(result["weight_kg"], 16)

    def test_working_load_requires_baseline(self):
        with self.assertRaises(ValidationError):
            validate_set_plan(
                {"exercise": "Жим", "weight_kg": 100, "reps": 5, "target_rir": 2, "rest_sec": 180},
                increment_kg=2.5,
                reference=None,
                latest_adjustment=None,
            )

    def test_loaded_warmup_requires_baseline(self):
        with self.assertRaises(ValidationError):
            validate_set_plan(
                {
                    "exercise": "Жим",
                    "weight_kg": 1000,
                    "reps": 5,
                    "target_rir": 5,
                    "rest_sec": 60,
                    "is_warmup": True,
                },
                increment_kg=2.5,
                reference=None,
                latest_adjustment=None,
            )

    def test_working_load_requires_known_baseline_rir(self):
        with self.assertRaises(ValidationError):
            validate_set_plan(
                {"exercise": "Жим", "weight_kg": 100, "reps": 5, "target_rir": 2, "rest_sec": 180},
                increment_kg=2.5,
                reference={"weight_kg": 100, "reps": 5, "rir": None},
                latest_adjustment=None,
            )

    def test_plan_is_clamped_to_reference(self):
        result = validate_set_plan(
            {"exercise": "Жим", "weight_kg": 150, "reps": 5, "target_rir": 2, "rest_sec": 180},
            increment_kg=2.5,
            reference={"weight_kg": 100, "reps": 5, "rir": 2},
            latest_adjustment=None,
        )
        self.assertLess(result["weight_kg"], 150)

    def test_weighted_pullup_uses_total_mass_but_returns_added_load(self):
        result = validate_set_plan(
            {"exercise": "Подтягивания с весом", "weight_kg": 25, "reps": 5, "target_rir": 2, "rest_sec": 180},
            increment_kg=2.5,
            reference={"weight_kg": 20, "bodyweight_kg": 80, "reps": 5, "rir": 2},
            latest_adjustment=None,
        )
        self.assertEqual(result["weight_kg"], 22.5)


if __name__ == "__main__":
    unittest.main()
