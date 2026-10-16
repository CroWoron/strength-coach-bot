import unittest

from coachbot.exercises import EXERCISE_CATALOG, catalog_for_llm, exercise_profile


class ExerciseCatalogTests(unittest.TestCase):
    def test_common_exercises_have_expected_fatigue_priors(self):
        self.assertEqual(exercise_profile("классическая становая тяга")["systemic_fatigue"], "high")
        self.assertEqual(exercise_profile("жим лёжа")["systemic_fatigue"], "medium")
        self.assertEqual(exercise_profile("сгибание рук с гантелями")["systemic_fatigue"], "low")

    def test_longest_alias_selects_specific_variation(self):
        self.assertEqual(exercise_profile("румынская тяга 80 кг")["id"], "romanian_deadlift")
        self.assertEqual(exercise_profile("подтягивания с весом 15 кг")["id"], "weighted_pullup")

    def test_catalog_describes_boxing_tradeoffs_without_harm_label(self):
        self.assertGreaterEqual(len(EXERCISE_CATALOG), 35)
        records = catalog_for_llm()
        self.assertTrue(any("shoulder" in item["stress_tags"] and item["boxing_watch"] for item in records))
        self.assertTrue(all(item["boxing_value"] in {"useful", "contextual"} for item in records))


if __name__ == "__main__":
    unittest.main()
