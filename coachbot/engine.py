from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any


class ValidationError(ValueError):
    pass


def _positive(value: float, name: str, allow_zero: bool = False) -> float:
    value = float(value)
    if value < 0 or (value == 0 and not allow_zero):
        raise ValidationError(f"{name} must be {'non-negative' if allow_zero else 'positive'}")
    return value


def epley(weight_kg: float, reps: int) -> float:
    weight = _positive(weight_kg, "weight_kg")
    if not 1 <= int(reps) <= 12:
        raise ValidationError("Epley estimate requires 1-12 reps")
    return weight * (1 + int(reps) / 30)


def epley_with_rir(weight_kg: float, reps: int, rir: float) -> float:
    weight = _positive(weight_kg, "weight_kg")
    reps = int(reps)
    rir = float(rir)
    if not 1 <= reps <= 12 or not 0 <= rir <= 5:
        raise ValidationError("RIR-adjusted estimate requires 1-12 reps and RIR 0-5")
    return weight * (1 + (reps + rir) / 30)


def brzycki(weight_kg: float, reps: int) -> float:
    weight = _positive(weight_kg, "weight_kg")
    if not 1 <= int(reps) <= 12:
        raise ValidationError("Brzycki estimate requires 1-12 reps")
    return weight * 36 / (37 - int(reps))


def target_weight(e1rm_kg: float, reps: int, target_rir: float) -> float:
    maximum = _positive(e1rm_kg, "e1rm_kg")
    reps = int(reps)
    target_rir = float(target_rir)
    if not 1 <= reps <= 20 or not 0 <= target_rir <= 5:
        raise ValidationError("Target requires 1-20 reps and RIR 0-5")
    return maximum / (1 + (reps + target_rir) / 30)


def round_load(weight_kg: float, increment_kg: float, direction: str = "nearest") -> float:
    weight = Decimal(str(_positive(weight_kg, "weight_kg", allow_zero=True)))
    increment = Decimal(str(_positive(increment_kg, "increment_kg")))
    rounding = ROUND_FLOOR if direction == "down" else ROUND_HALF_UP
    return float((weight / increment).quantize(Decimal("1"), rounding=rounding) * increment)


def tonnage(sets: list[dict[str, Any]]) -> float:
    return sum(float(item.get("weight_kg") or 0) * int(item.get("reps") or 0) for item in sets)


@dataclass(frozen=True)
class SetAdjustment:
    action: str
    recommended_weight_kg: float | None
    maximum_weight_kg: float | None
    reason_code: str
    explanation: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def adjust_after_set(
    *,
    weight_kg: float,
    actual_reps: int,
    planned_reps: int,
    actual_rir: float | None,
    target_rir: float,
    technique_ok: bool,
    pain: bool,
    quality_ok: bool | None = None,
    increment_kg: float = 2.5,
) -> SetAdjustment:
    weight = _positive(weight_kg, "weight_kg", allow_zero=True)
    actual_reps = int(actual_reps)
    planned_reps = int(planned_reps)
    actual_rir = None if actual_rir is None else float(actual_rir)
    target_rir = float(target_rir)
    if actual_reps < 0 or planned_reps < 1 or (actual_rir is not None and not 0 <= actual_rir <= 10) or not 0 <= target_rir <= 5:
        raise ValidationError("Invalid set result")

    if pain:
        return SetAdjustment("stop", None, 0, "pain", "Stop the painful exercise and do not increase load.")

    if not technique_ok:
        reduced = round_load(weight * 0.95, increment_kg, "down")
        return SetAdjustment("reduce", reduced, weight, "technique", "Technique degraded; load cannot increase.")

    if quality_ok is False:
        return SetAdjustment("stop", None, weight, "quality_drop", "Explosive quality or speed dropped; end the series.")

    if actual_reps < planned_reps or actual_rir is not None and actual_rir <= 0:
        reduced = round_load(weight * 0.95, increment_kg, "down")
        return SetAdjustment("reduce", reduced, weight, "missed_target", "Reps were missed or no reserve remained.")

    if actual_rir is None:
        return SetAdjustment("hold", weight, weight, "rir_unknown", "RIR is unknown; load cannot increase automatically.")

    if actual_rir >= target_rir + 2:
        increased = round_load(weight + increment_kg, increment_kg)
        maximum = round_load(weight * 1.05, increment_kg, "down")
        increased = min(increased, maximum)
        return SetAdjustment("increase", increased, maximum, "underload", "The set was clearly easier than the target.")

    return SetAdjustment("hold", weight, weight, "target_met", "The set matched the intended effort closely enough.")


def validate_set_plan(
    plan: dict[str, Any],
    *,
    increment_kg: float,
    reference: dict[str, Any] | None,
    latest_adjustment: dict[str, Any] | None,
    round_to_increment: bool = True,
) -> dict[str, Any]:
    exercise = str(plan.get("exercise") or "").strip()
    if not exercise:
        raise ValidationError("exercise is required")
    reps = int(plan.get("reps") or 0)
    target_rir_value = plan.get("target_rir", 2)
    quality_based = bool(plan.get("quality_based", False))
    target_rir = None if target_rir_value is None else float(target_rir_value)
    rest_sec = int(plan.get("rest_sec") or 0)
    weight = _positive(plan.get("weight_kg", 0), "weight_kg", allow_zero=True)
    is_warmup = bool(plan.get("is_warmup", False))
    if not 1 <= reps <= 30 or (target_rir is not None and not 0 <= target_rir <= 5) or not 15 <= rest_sec <= 900:
        raise ValidationError("Plan requires reps 1-30, RIR 0-5 and rest 15-900 seconds")
    if target_rir is None and not quality_based:
        raise ValidationError("RIR can be empty only for quality-based explosive work")
    def normalize_load(value: float) -> float:
        return round_load(value, increment_kg, "down") if round_to_increment else float(value)

    if not quality_based:
        weight = normalize_load(weight)

    if latest_adjustment:
        if latest_adjustment["action"] == "stop":
            raise ValidationError("The exercise was stopped by the safety rule")
        maximum = latest_adjustment.get("maximum_weight_kg")
        if maximum is not None and weight > float(maximum):
            weight = normalize_load(float(maximum))
    elif quality_based:
        pass
    elif reference and not is_warmup:
        if reference.get("rir") is None:
            raise ValidationError("A baseline with known RIR is required before prescribing a working weight")
        reference_weight = float(reference["weight_kg"])
        bodyweight = reference.get("bodyweight_kg")
        calculation_weight = reference_weight + float(bodyweight or 0)
        estimate = epley_with_rir(
            calculation_weight, int(reference["reps"]), float(reference.get("rir") or 0)
        )
        target_system_load = target_weight(estimate, reps, target_rir)
        allowed_external_load = round(
            max(0, target_system_load * 1.025 - float(bodyweight or 0)), 8
        )
        maximum = normalize_load(allowed_external_load)
        weight = min(weight, maximum)
    elif is_warmup and reference:
        weight = min(weight, normalize_load(float(reference["weight_kg"])))
    elif is_warmup and weight > 0:
        raise ValidationError("A confirmed baseline is required before prescribing a loaded warm-up")
    elif not reference and not is_warmup:
        raise ValidationError("A confirmed baseline is required before prescribing a working weight")

    return {
        "exercise": exercise,
        "weight_kg": weight,
        "reps": reps,
        "target_rir": target_rir,
        "rest_sec": rest_sec,
        "is_warmup": is_warmup,
        "quality_based": quality_based,
        "rationale": str(plan.get("rationale") or "")[:1000],
        "evidence_ids": [str(item) for item in plan.get("evidence_ids", [])][:10],
    }
