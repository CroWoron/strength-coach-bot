from __future__ import annotations

import re
from typing import Any


def _profile(
    exercise_id: str,
    name: str,
    aliases: tuple[str, ...],
    fatigue: str,
    tags: tuple[str, ...],
    boxing_value: str = "contextual",
) -> dict[str, Any]:
    return {
        "id": exercise_id,
        "name": name,
        "aliases": aliases,
        "systemic_fatigue": fatigue,
        "tags": tags,
        "boxing_value": boxing_value,
    }


EXERCISE_CATALOG = (
    _profile("back_squat", "присед со штангой", ("присед со штангой", "присед", "back squat"), "high", ("compound", "legs", "axial", "trunk"), "useful"),
    _profile("front_squat", "фронтальный присед", ("фронтальный присед", "front squat"), "high", ("compound", "legs", "axial", "trunk"), "useful"),
    _profile("conventional_deadlift", "классическая становая тяга", ("классическая становая", "становая тяга", "становая", "conventional deadlift", "deadlift"), "high", ("compound", "posterior_chain", "axial", "grip", "trunk"), "useful"),
    _profile("sumo_deadlift", "становая тяга сумо", ("становая сумо", "тяга сумо", "sumo deadlift"), "high", ("compound", "posterior_chain", "axial", "grip", "trunk"), "useful"),
    _profile("deficit_deadlift", "становая с дефицита", ("становая с дефицита", "тяга с дефицита", "deficit deadlift"), "high", ("compound", "posterior_chain", "axial", "grip", "trunk", "soreness")),
    _profile("rack_pull", "тяга с плинтов", ("тяга с плинтов", "рэковая тяга", "rack pull"), "high", ("compound", "posterior_chain", "axial", "grip")),
    _profile("romanian_deadlift", "румынская тяга", ("румынская тяга", "румынская становая", "romanian deadlift", "rdl"), "high", ("compound", "posterior_chain", "grip", "soreness"), "useful"),
    _profile("good_morning", "наклоны со штангой", ("наклоны со штангой", "гудмонинг", "good morning"), "high", ("compound", "posterior_chain", "axial", "trunk", "soreness")),
    _profile("olympic_lift", "тяжелоатлетельское движение", ("толчок штанги", "рывок штанги", "взятие на грудь", "clean and jerk", "power clean", "snatch"), "high", ("compound", "power", "technical", "axial", "grip"), "useful"),
    _profile("farmer_carry", "фермерская прогулка", ("фермерская прогулка", "прогулка фермера", "farmer carry"), "high", ("compound", "grip", "trunk", "axial"), "contextual"),
    _profile("bench_press", "жим лёжа", ("жим лежа", "bench press"), "medium", ("compound", "push", "chest", "shoulder", "triceps"), "useful"),
    _profile("incline_press", "наклонный жим", ("жим на наклонной", "наклонный жим", "incline press"), "medium", ("compound", "push", "chest", "shoulder", "triceps")),
    _profile("dumbbell_press", "жим гантелей", ("жим гантелей лежа", "жим гантелей", "dumbbell press"), "medium", ("compound", "push", "chest", "shoulder")),
    _profile("overhead_press", "жим стоя", ("жим стоя", "армейский жим", "overhead press", "military press"), "medium", ("compound", "push", "shoulder", "triceps", "trunk"), "contextual"),
    _profile("push_press", "швунг жимовой", ("швунг", "push press"), "medium", ("compound", "power", "push", "shoulder", "legs"), "useful"),
    _profile("weighted_dip", "отжимания на брусьях", ("отжимания на брусьях", "брусья с весом", "weighted dip", "dips"), "medium", ("compound", "push", "shoulder", "chest", "triceps"), "contextual"),
    _profile("weighted_pullup", "подтягивания с весом", ("подтягивания с весом", "weighted pull-up", "weighted pullup"), "medium", ("compound", "pull", "grip", "lats", "biceps"), "useful"),
    _profile("pullup", "подтягивания", ("подтягивания", "pull-up", "pullup", "chin-up", "chinup"), "medium", ("compound", "pull", "grip", "lats", "biceps"), "useful"),
    _profile("barbell_row", "тяга штанги в наклоне", ("тяга штанги в наклоне", "тяга в наклоне", "pendlay row", "barbell row"), "medium", ("compound", "pull", "grip", "lats", "posterior_chain", "trunk"), "contextual"),
    _profile("bulgarian_split_squat", "болгарский сплит-присед", ("болгарский присед", "болгарский сплит", "bulgarian split squat"), "medium", ("compound", "legs", "unilateral", "balance", "soreness"), "useful"),
    _profile("lunge", "выпады", ("выпады", "lunge"), "medium", ("compound", "legs", "unilateral", "balance", "soreness"), "useful"),
    _profile("leg_press", "жим ногами", ("жим ногами", "leg press"), "medium", ("compound", "legs"), "contextual"),
    _profile("hip_thrust", "ягодичный мост", ("ягодичный мост", "хип траст", "hip thrust"), "medium", ("compound", "posterior_chain", "glutes"), "useful"),
    _profile("kettlebell_swing", "мах гирей", ("мах гирей", "махи гирей", "kettlebell swing"), "medium", ("compound", "power", "posterior_chain", "grip"), "useful"),
    _profile("kettlebell_snatch", "рывок гири", ("рывок гири", "kettlebell snatch"), "medium", ("compound", "power", "shoulder", "grip", "posterior_chain"), "useful"),
    _profile("chest_supported_row", "тяга с опорой грудью", ("тяга с опорой грудью", "chest supported row"), "low", ("compound", "pull", "lats", "biceps"), "useful"),
    _profile("lat_pulldown", "тяга верхнего блока", ("тяга верхнего блока", "lat pulldown"), "low", ("compound", "pull", "lats", "biceps"), "contextual"),
    _profile("cable_row", "горизонтальная тяга блока", ("горизонтальная тяга", "тяга нижнего блока", "cable row"), "low", ("compound", "pull", "lats", "biceps"), "contextual"),
    _profile("pushup", "отжимания", ("отжимания от пола", "отжимания", "push-up", "pushup"), "low", ("compound", "push", "chest", "shoulder", "triceps"), "useful"),
    _profile("box_jump", "прыжок на тумбу", ("прыжок на тумбу", "запрыгивание на тумбу", "выпрыгивание", "box jump"), "low", ("power", "legs", "quality_limited"), "useful"),
    _profile("medicine_ball_throw", "бросок медбола", ("бросок медбола", "медбол", "medicine ball throw"), "low", ("power", "push", "trunk", "quality_limited"), "useful"),
    _profile("face_pull", "тяга к лицу", ("тяга к лицу", "фейс пул", "face pull"), "low", ("shoulder", "upper_back"), "useful"),
    _profile("external_rotation", "наружная ротация плеча", ("наружная ротация", "внешняя ротация", "external rotation"), "low", ("shoulder", "rotator_cuff"), "useful"),
    _profile("lateral_raise", "разведения в стороны", ("разведения в стороны", "махи в стороны", "lateral raise"), "low", ("shoulder", "isolation"), "contextual"),
    _profile("biceps_curl", "сгибания на бицепс", ("сгибание рук", "подъем на бицепс", "бицепс", "biceps curl"), "low", ("arms", "isolation"), "contextual"),
    _profile("triceps_extension", "разгибания на трицепс", ("разгибание рук", "трицепс на блоке", "triceps extension", "pushdown"), "low", ("arms", "isolation"), "contextual"),
    _profile("leg_curl", "сгибание ног", ("сгибание ног", "leg curl"), "low", ("legs", "isolation", "hamstrings"), "contextual"),
    _profile("leg_extension", "разгибание ног", ("разгибание ног", "leg extension"), "low", ("legs", "isolation", "quadriceps"), "contextual"),
    _profile("calf_raise", "подъёмы на носки", ("подъем на носки", "икры", "calf raise"), "low", ("legs", "isolation", "calves"), "contextual"),
    _profile("pallof_press", "жим Палоффа", ("жим палоффа", "палофф", "pallof press"), "low", ("trunk", "anti_rotation"), "useful"),
    _profile("plank", "планка", ("планка", "plank", "dead bug"), "low", ("trunk", "isometric"), "useful"),
)


def normalize_exercise_name(name: str) -> str:
    return " ".join(re.sub(r"[^a-zа-я0-9]+", " ", name.casefold().replace("ё", "е")).split())


def exercise_profile(name: str) -> dict[str, Any] | None:
    normalized = normalize_exercise_name(name)
    matches = []
    for profile in EXERCISE_CATALOG:
        for alias in profile["aliases"]:
            normalized_alias = normalize_exercise_name(alias)
            if normalized_alias and normalized_alias in normalized:
                matches.append((len(normalized_alias), profile))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def catalog_for_llm() -> list[dict[str, Any]]:
    boxing_watch_by_tag = {
        "axial": "Keep hard doses away from important footwork or sparring sessions.",
        "posterior_chain": "Monitor leg and trunk freshness for footwork and rotation.",
        "grip": "Monitor forearm and grip fatigue before bag work and sparring.",
        "shoulder": "Preserve pain-free shoulder and scapular motion; reduce dose if punching quality changes.",
        "soreness": "Avoid a novel or high-eccentric dose close to hard boxing.",
        "isolation": "Accessory only; high volume may add fatigue or body mass with limited boxing transfer.",
    }
    return [
        {
            "name": item["name"],
            "systemic_fatigue_prior": item["systemic_fatigue"],
            "stress_tags": item["tags"],
            "boxing_value": item["boxing_value"],
            "boxing_watch": [boxing_watch_by_tag[tag] for tag in item["tags"] if tag in boxing_watch_by_tag],
        }
        for item in EXERCISE_CATALOG
    ]
