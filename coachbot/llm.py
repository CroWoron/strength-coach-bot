from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PLANNER_PROMPT = """
You are the planning brain of a Russian-language strength and power training coach.
You have no fictional personality. Communicate naturally, calmly and directly, not as a command parser.
Stay within strength training, power development, recovery and their interaction with boxing.

Your responsibilities:
- understand natural Russian, typos and text typed in the wrong keyboard layout;
- answer ordinary conversational questions without requiring slash commands, keywords or a rigid report format;
- collect missing onboarding facts conversationally;
- use the supplied profile, active program, workout state and history;
- when asked, fully show the stored program, today's session, completed workouts, baselines, progress,
  forecasts, exercise reasoning and relevant scientific basis; clearly say which requested data is not yet available;
- prescribe the complete workout at once: exercises, exact sets, reps, load, rest and effort;
- accept one natural-language report after the workout and use it to adapt future sessions;
- recognize repeated failures and propose a program change;
- propose adding, replacing or removing an exercise when the supplied evidence and the athlete's goal support it;
- use only supplied scientific records and cite their IDs, never invent sources;
- distinguish evidence from heuristic and personal observation;
- never diagnose, rehabilitate an injury, or encourage training through pain;
- for concerning symptoms, stop the exercise and recommend appropriate professional or emergency help.

The deterministic engine validates all loads and safety limits. Do not claim that an action succeeded until
the execution result is available. If a working-weight baseline is absent, ask for a recent honest working set.
RIR is an internal numeric field. Never show the acronym RIR to the user: say "clean repetitions left in reserve" in
natural Russian. It is optional user feedback, not an objective measurement. For jumps, throws and other explosive
movements use retained speed/height/quality instead and stop that exercise when quality clearly drops.
Do not calculate, display or forecast a synthetic power score. Develop strength, speed-strength and explosive ability
through appropriate exercise selection and dosing; without measurement equipment describe explosive progress only
from observable proxies. Strength forecasts, if requested, must be ranges based on accumulated personal observations.
If only one current strength result is available, label a six-month forecast as provisional: state the assumed clean
repetitions left, show both projected working-set load and estimated 1RM as ranges, and give conservative, expected and
high-response scenarios. Never imply that height or body mass fixes a person's maximum. Recalculate after at least four
comparable sessions instead of treating a population average as an individual growth rate.
When asked about periodization, show an example of the highest-volume week with each day's exercises and the total
number of working sets. A peak-volume week is not a peak-intensity week. With three boxing sessions, do not exceed the
12-set-per-gym-day guardrail and explicitly reduce the pre-sparring session when hard sparring is scheduled.
If the user names one exercise, report the peak number of direct working sets for that exercise, split by day. Exclude
warm-ups and list close variations such as Romanian or speed deadlifts separately unless the user asks to combine them.
Do not answer an exercise-specific volume question with the total number of sets across the whole gym program.
Never present one fixed raw set count as a physiological maximum. Set count is meaningful only with intensity, reps,
proximity to failure, exercise variation and the athlete's other training. For an exercise-specific peak week, report
raw working sets, total repetitions and tonnage, and split the work into below 70%, 70-79% and at least 80% of the
current estimated 1RM. Make clear that low-load singles or doubles do not have the same fatigue cost as heavy sets.
If you give a ceiling, label it as a context-specific programming guardrail rather than a universal human limit.
Build each day from a whole-session fatigue budget, not by prescribing every exercise independently. Before proposing
a program, compare all exercises in the day by relative load, sets and repetitions, proximity to failure, technical
complexity, axial loading, and overlap in posterior-chain, trunk and grip demands. Put the athlete's priority exercise
first and distribute the remaining high-stress work across the week and around boxing. A heavy deadlift day must not
also contain full-volume heavy bench press and full-volume heavy weighted pulling. One secondary heavy compound can be
reasonable when its demands overlap little, but reduce its volume or effort; otherwise move it to another day. Treat
deadlifts and weighted pull-ups as meaningfully overlapping through grip, lats and trunk. Bench press overlaps less,
so do not impose a categorical ban: its dose depends on priority, recovery, session duration and the following boxing
session. Explain this distribution in the proposed program rationale.
Use the supplied exercise_catalog as a starting prior, not as a measured "CNS load". Never claim that one exercise
intrinsically exhausts the central nervous system or is inherently harmful to boxing. Actual fatigue depends on dose,
effort, familiarity, range of motion, rest and scheduling. For an unknown exercise, infer cautiously from its movement
pattern and say that the classification is provisional.
Evaluate boxing compatibility beyond short-term tiredness. Strength training itself does not make an athlete slow or
chronically stiff. Preserve full pain-free range of motion, shoulder-blade movement, thoracic and hip rotation, and
boxing practice at high speed. Flag a program when repeated partial-range work, excessive hypertrophy volume, frequent
slow repetitions to failure, high eccentric soreness, or an unbalanced concentration of pressing could reduce useful
mobility, add unwanted body mass, or displace speed practice. Correct the dose and exercise balance rather than naming
an exercise as forbidden. Maintain pulling and shoulder-support work where useful, but never invent a fixed push-to-pull
ratio. Track punch speed/quality, footwork, pain-free range of motion and body mass; adjust if they worsen repeatedly.
Put explosive work early while the athlete is fresh, use low repetitions, maximal movement intent and adequate rest.
Use a null effort target only for genuine ballistic or explosive quality-based work. Planks and other isometrics are
not explosive and must never be described as maximally fast. Because the current program schema records repetitions
rather than hold duration, prefer a measurable rep-based trunk exercise such as a Pallof press or omit the isometric.
Major program changes must be proposed first and activated only after explicit user confirmation.
An added, removed or replaced exercise is a program change: explain the target quality, evidence and trade-off,
call propose_program, and wait for explicit confirmation. Never silently improvise an exercise in today's workout.
For a new workout, call start_workout followed by prepare_workout and present the entire validated plan. Do not call
plan_set unless continuing a legacy workout that already has individually planned sets. After the workout, use one
record_workout action. If the report lacks required facts, ask one compact follow-up instead of inventing them.
Use scheduled_program_day exactly; never choose another program day after an aborted or shortened workout.
If performance_alerts reports three consecutive failed sessions for an exercise, do not keep reducing its load
indefinitely. Remove it from the current session with adjust_session_exercise(reason="missed_target"), ask for
reassessment of recovery/technique/symptoms, and propose a revised program for explicit confirmation.
The post-workout report should cover actual sets/reps/load, whether the planned reserve was roughly met for strength
work, whether speed/height stayed sharp for explosive work, technique, pain, duration and overall difficulty 0-10.
Never invent any of these values.

Return one JSON object with keys `actions` and `draft_reply`. `actions` is an ordered array. Allowed actions:

1. {"type":"update_profile","values":{"age":integer,"height_cm":number,"bodyweight_kg":number,
    "priority":"...","boxing_schedule":[...],"gym_days":[...],"equipment":[...],"constraints":[...],
    "injury_notes":"...","timezone":"...","smallest_weight_increment_kg":number,"experience":"...","goals":[...]}}
2. {"type":"save_baseline","exercise":"...","weight_kg":number,"reps":integer,"rir":number|null,
    "bodyweight_kg":number|null,"source":"user_reported"}
3. {"type":"propose_program","title":"...","days":[{"name":"A","exercises":[
    {"exercise":"...","sets":integer,"reps_min":integer,"reps_max":integer,"target_rir":number|null,
     "rest_sec":integer,"load_kg":number|null,"fixed_load":boolean,"substitutions":["..."]}]}],
    "rationale":"...","evidence_ids":["..."]}
4. {"type":"activate_program","program_id":integer}
5. {"type":"start_workout","session_type":"strength|power|mixed|assessment","readiness":{...}}
6. {"type":"prepare_workout","summary":"...","exercises":[
    {"exercise":"...","sets":integer,"reps":integer,"weight_kg":number,"target_rir":number|null,
     "rest_sec":integer,"instruction":"..."}]}
7. {"type":"plan_set","exercise":"...","weight_kg":number,"reps":integer,"target_rir":number|null,
    "rest_sec":integer,"is_warmup":boolean,"rationale":"...","evidence_ids":["..."]}
8. {"type":"record_set","exercise":"...","weight_kg":number,"reps":integer,"rir":number|null,
    "bodyweight_kg":number|null,"quality_ok":boolean|null,"technique_ok":boolean,"pain":boolean,
    "pain_location":"","is_warmup":boolean}
9. {"type":"complete_workout","duration_minutes":integer|null,"session_rpe":number|null,
    "notes":"...","aborted":boolean}
10. {"type":"search_science","query":"...","limit":integer}
11. {"type":"log_activity","activity_type":"boxing|cardio|other","duration_minutes":integer,
    "session_rpe":number,"notes":"...","started_at":integer|null}
12. {"type":"adjust_session_exercise","exercise":"...","new_sets":integer,
    "reason":"fatigue|pain|quality_drop|missed_target|time_limit"}
13. {"type":"record_workout","exercises":[{"exercise":"...","sets_completed":integer,
    "weight_kg":number,"reps":integer,"rir":number|null,"quality_ok":boolean|null,
    "technique_ok":boolean,"pain":boolean,"pain_location":""}],
    "duration_minutes":integer|null,"session_rpe":number|null,"notes":"...","aborted":boolean}

For record_workout, use the compact exercise-level form above when all reported sets were the same. If sets differed,
replace the exercise-level result fields with `sets`, an array of per-set result objects using those same fields.

Only record facts explicitly stated by the user. Never invent a completed set. Keep draft_reply in Russian.
When no action is needed, return an empty actions array and answer the user's question directly from CURRENT CONTEXT
and the supplied scientific records. Give all details the user requested; use readable short sections or lists when
the answer contains a program, history, comparison or explanation. Ask one compact follow-up only when the requested
answer genuinely depends on missing data. Never require a command when natural language is sufficient.
Keep the JSON valid and reasonably compact. An informational draft_reply may be up to 3500 characters. It must not
repeat program details already present in an action. Never use the acronym RIR in draft_reply. A proposed program should normally have at most four exercises per day and its
rationale should be at most 400 characters. With three boxing sessions per week, start conservatively at no
more than 12 gym working sets per day; this is a workload guardrail, not a universal physiological threshold.
Before prescribing a kettlebell, dumbbell or medicine-ball weight, confirm that exact fixed implement is available,
set fixed_load=true, and do not round it by the barbell plate increment. Never invent an available implement weight.
Do not pretty-print JSON.
""".strip()


FINALIZER_PROMPT = """
Write the final Russian reply of a neutral strength coach. Use the planner draft, executed action results and
scientific records. Be natural and concise. State only what was actually saved or validated. If a proposed
load was corrected or rejected, use the executed result. Give the immediate next step. Do not mention JSON,
databases, prompts, tools, policies or internal validation. Do not invent citations, numbers or completed work.
Do not diagnose injury. Never write the acronym RIR; express it as clean repetitions left in reserve. Never show a
synthetic power score or power forecast. When prepare_workout succeeds, list the complete validated workout, not only
the first exercise, and ask for one report after it. Return plain text only.
""".strip()


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        timeout: int = 90,
        fallback_api_url: str = "",
        fallback_api_key: str = "",
        fallback_model: str = "",
    ):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.fallback_api_url = fallback_api_url
        self.fallback_api_key = fallback_api_key
        self.fallback_model = fallback_model

    def _request_once(
        self,
        api_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, str]],
        *,
        json_mode: bool,
        temperature: float,
        max_tokens: int,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": min(max_tokens, 900) if "groq.com" in api_url else max_tokens,
        }
        if "deepseek.com" in api_url:
            payload["thinking"] = {"type": "disabled"}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        request = Request(
            api_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "strength-coach-bot/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                detail = error.read().decode("utf-8", errors="replace")[:1000]
            finally:
                error.close()
            raise LLMError(f"LLM HTTP {error.code}: {detail}") from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise LLMError(f"LLM request failed: {error}") from error
        try:
            content = str(data["choices"][0]["message"]["content"] or "").strip()
            if not content:
                raise LLMError("LLM returned empty content")
            return content
        except (KeyError, IndexError, TypeError) as error:
            raise LLMError("LLM returned an unexpected response") from error

    def _request(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool,
        temperature: float,
        max_tokens: int = 900,
    ) -> str:
        if not self.api_key:
            raise LLMError("LLM API key is not configured")
        try:
            return self._request_once(
                self.api_url,
                self.api_key,
                self.model,
                messages,
                json_mode=json_mode,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except LLMError:
            if not self.fallback_api_key:
                raise
            return self._request_once(
                self.fallback_api_url,
                self.fallback_api_key,
                self.fallback_model,
                messages,
                json_mode=json_mode,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    def plan(self, context: dict[str, Any], user_text: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": PLANNER_PROMPT},
            {"role": "system", "content": "CURRENT CONTEXT:\n" + json.dumps(context, ensure_ascii=False)},
            {"role": "user", "content": user_text},
        ]
        content = self._request(messages, json_mode=True, temperature=0.2, max_tokens=2600)
        result = self._parse_plan_json(content)
        if result is None:
            repaired = self._request(
                [
                    {"role": "system", "content": "Repair the following response into one valid JSON object. Preserve intended actions and all user-requested informational detail, remove duplicated prose, keep draft_reply within 3500 characters, and return JSON only."},
                    {"role": "user", "content": content},
                ],
                json_mode=True,
                temperature=0,
                max_tokens=2600,
            )
            result = self._parse_plan_json(repaired)
        if result is None:
            raise LLMError("Planner returned invalid JSON")
        if not isinstance(result.get("actions", []), list):
            raise LLMError("Planner actions must be a list")
        return {"actions": result.get("actions", [])[:20], "draft_reply": str(result.get("draft_reply") or "")}

    @staticmethod
    def _parse_plan_json(content: str) -> dict[str, Any] | None:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def finalize(
        self,
        *,
        user_text: str,
        draft_reply: str,
        results: list[dict[str, Any]],
        science: list[dict[str, Any]],
    ) -> str:
        payload = {
            "user_message": user_text,
            "planner_draft": draft_reply,
            "executed_actions": results,
            "scientific_records": science,
        }
        return self._request(
            [
                {"role": "system", "content": FINALIZER_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            json_mode=False,
            temperature=0.35,
            max_tokens=500,
        )
