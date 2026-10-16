import json
import io
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from coachbot.llm import FINALIZER_PROMPT, PLANNER_PROMPT, LLMClient


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()


class LLMTransportTests(unittest.TestCase):
    def test_request_has_provider_safe_user_agent(self):
        captured = {}

        def fake_open(request, timeout):
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            return FakeResponse()

        client = LLMClient("https://example.test/v1/chat/completions", "secret", "model")
        with patch("coachbot.llm.urlopen", fake_open):
            result = client._request([{"role": "user", "content": "hi"}], json_mode=False, temperature=0)

        self.assertEqual(result, "ok")
        self.assertEqual(captured["headers"]["User-agent"], "strength-coach-bot/0.1")

    def test_deepseek_disables_thinking_for_structured_output(self):
        captured = {}

        def fake_open(request, timeout):
            captured["payload"] = json.loads(request.data)
            return FakeResponse()

        client = LLMClient("https://api.deepseek.com/chat/completions", "secret", "deepseek-flash")
        with patch("coachbot.llm.urlopen", fake_open):
            client._request([{"role": "user", "content": "hi"}], json_mode=True, temperature=0)

        self.assertEqual(captured["payload"]["thinking"], {"type": "disabled"})

    def test_primary_failure_uses_fallback_provider(self):
        calls = []

        def fake_open(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise HTTPError(request.full_url, 429, "rate limit", {}, io.BytesIO(b"rate limit"))
            return FakeResponse()

        client = LLMClient(
            "https://primary.test/chat", "primary", "model-a",
            fallback_api_url="https://fallback.test/chat",
            fallback_api_key="fallback",
            fallback_model="model-b",
        )
        with patch("coachbot.llm.urlopen", fake_open):
            result = client._request([{"role": "user", "content": "hi"}], json_mode=False, temperature=0)

        self.assertEqual(result, "ok")
        self.assertEqual(calls, ["https://primary.test/chat", "https://fallback.test/chat"])

    def test_plan_repairs_one_invalid_json_response(self):
        client = LLMClient("https://example.test/chat", "secret", "model")
        responses = iter(['{"actions": [}', '{"actions": [], "draft_reply": "ok"}'])
        with patch.object(client, "_request", side_effect=lambda *_args, **_kwargs: next(responses)):
            result = client.plan({}, "hello")
        self.assertEqual(result, {"actions": [], "draft_reply": "ok"})

    def test_prompts_use_whole_workout_flow_and_hide_internal_rir_term(self):
        self.assertIn('"type":"prepare_workout"', PLANNER_PROMPT)
        self.assertIn('"type":"record_workout"', PLANNER_PROMPT)
        self.assertIn("Never show the acronym RIR", PLANNER_PROMPT)
        self.assertIn("Never write the acronym RIR", FINALIZER_PROMPT)
        self.assertIn("Never silently improvise an exercise", PLANNER_PROMPT)
        self.assertIn("high-response scenarios", PLANNER_PROMPT)
        self.assertIn("highest-volume week", PLANNER_PROMPT)
        self.assertIn("A peak-volume week is not a peak-intensity week", PLANNER_PROMPT)
        self.assertIn("peak number of direct working sets for that exercise", PLANNER_PROMPT)
        self.assertIn("Exclude\nwarm-ups", PLANNER_PROMPT)
        self.assertIn("Never present one fixed raw set count as a physiological maximum", PLANNER_PROMPT)
        self.assertIn("below 70%, 70-79% and at least 80%", PLANNER_PROMPT)
        self.assertIn("context-specific programming guardrail", PLANNER_PROMPT)
        self.assertIn("whole-session fatigue budget", PLANNER_PROMPT)
        self.assertIn("must not\nalso contain full-volume heavy bench press", PLANNER_PROMPT)
        self.assertIn("deadlifts and weighted pull-ups as meaningfully overlapping", PLANNER_PROMPT)
        self.assertIn("do not impose a categorical ban", PLANNER_PROMPT)
        self.assertIn('not as a measured "CNS load"', PLANNER_PROMPT)
        self.assertIn("Strength training itself does not make an athlete slow", PLANNER_PROMPT)
        self.assertIn("repeated partial-range work", PLANNER_PROMPT)
        self.assertIn("never invent a fixed push-to-pull", PLANNER_PROMPT)
        self.assertIn("Planks and other isometrics are\nnot explosive", PLANNER_PROMPT)

    def test_planner_supports_complete_information_answers_without_commands(self):
        self.assertIn("without requiring slash commands", PLANNER_PROMPT)
        self.assertIn("fully show the stored program", PLANNER_PROMPT)
        self.assertIn("all details the user requested", PLANNER_PROMPT)
        self.assertIn("up to 3500 characters", PLANNER_PROMPT)
        self.assertNotIn("draft_reply must be at most 300 characters", PLANNER_PROMPT)


if __name__ == "__main__":
    unittest.main()
