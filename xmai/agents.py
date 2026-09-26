from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Action, SceneState, Thresholds


def make_event(
    *,
    run_id: str,
    scene: SceneState,
    agent: str,
    evidence_id: str,
    inputs: list[str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "scenario_id": scene.scenario_id,
        "seed": scene.seed,
        "decision_step": scene.decision_step,
        "timestamp_s": scene.timestamp_s,
        "agent": agent,
        "input_evidence_ids": inputs,
        "output_evidence_ids": [evidence_id],
        "payload": payload,
        "status": "ok",
    }


@dataclass
class SceneStateAgent:
    def process(self, run_id: str, scene: SceneState) -> dict[str, Any]:
        evidence_id = f"{run_id}:scene"
        return make_event(
            run_id=run_id,
            scene=scene,
            agent="scene_state",
            evidence_id=evidence_id,
            inputs=[],
            payload={"evidence_id": evidence_id, "scene_state": scene.to_dict()},
        )


@dataclass
class RiskAgent:
    thresholds: Thresholds

    def assess(self, run_id: str, scene: SceneState, scene_event: dict[str, Any]) -> dict[str, Any]:
        actor = scene.relevant_actor
        closing_speed = max(scene.ego_speed_mps - actor.speed_mps, 0.0)

        if actor.actor_type == "pedestrian":
            hazard_type = "pedestrian"
        elif scene.junction_conflict:
            hazard_type = "junction_conflict"
        else:
            hazard_type = "lead_brake"

        severity = "low"
        reason_codes: list[str] = []
        if actor.in_ego_path or scene.junction_conflict:
            reason_codes.append("ACTOR_IN_EGO_PATH")
        if actor.distance_m <= self.thresholds.near_distance_m:
            reason_codes.append("ACTOR_NEAR_EGO")
            severity = "high"
        elif actor.distance_m <= self.thresholds.near_distance_m * 2:
            severity = "medium"
        if actor.visibility == "partial":
            reason_codes.append("VISIBILITY_UNCERTAIN")
            severity = (
                "high"
                if actor.distance_m <= self.thresholds.near_distance_m * 2
                else "medium"
            )
        if closing_speed > 4.0:
            reason_codes.append("HIGH_CLOSING_SPEED")
            if actor.distance_m <= 18.0:
                severity = "high"
            elif actor.distance_m <= 30.0 and severity == "low":
                severity = "medium"

        evidence_id = f"{run_id}:risk"
        return make_event(
            run_id=run_id,
            scene=scene,
            agent="risk",
            evidence_id=evidence_id,
            inputs=scene_event["output_evidence_ids"],
            payload={
                "evidence_id": evidence_id,
                "hazard_id": f"{run_id}:hazard",
                "hazard_type": hazard_type,
                "severity": severity,
                "reason_codes": reason_codes,
                "supporting_evidence_ids": scene_event["output_evidence_ids"],
            },
        )


@dataclass
class PredictionAgent:
    def predict(
        self,
        run_id: str,
        scene: SceneState,
        scene_event: dict[str, Any],
        risk_event: dict[str, Any],
    ) -> dict[str, Any]:
        actor = scene.relevant_actor
        conflict_relevant = actor.in_ego_path or scene.junction_conflict

        if not conflict_relevant or scene.ego_speed_mps <= 0:
            time_to_conflict_s: float | None = None
        elif actor.actor_type == "vehicle" and not scene.junction_conflict:
            closing_speed = max(scene.ego_speed_mps - actor.speed_mps, 0.0)
            time_to_conflict_s = actor.distance_m / closing_speed if closing_speed > 0 else None
        else:
            time_to_conflict_s = actor.distance_m / scene.ego_speed_mps

        evidence_id = f"{run_id}:prediction"
        return make_event(
            run_id=run_id,
            scene=scene,
            agent="prediction",
            evidence_id=evidence_id,
            inputs=scene_event["output_evidence_ids"] + risk_event["output_evidence_ids"],
            payload={
                "evidence_id": evidence_id,
                "actor_id": actor.actor_id,
                "horizon_s": 5.0,
                "time_to_conflict_s": round(time_to_conflict_s, 4) if time_to_conflict_s is not None else None,
                "confidence": 0.75 if actor.visibility == "partial" else 0.95,
            },
        )


def select_action(
    risk_payload: dict[str, Any], prediction_payload: dict[str, Any], thresholds: Thresholds
) -> tuple[Action, list[dict[str, Any]], list[str]]:
    severity = risk_payload["severity"]
    ttc = prediction_payload["time_to_conflict_s"]

    if severity == "high" or (ttc is not None and ttc <= thresholds.stop_ttc_s):
        selected = Action.CONTROLLED_STOP
        selected_reasons = list(risk_payload["reason_codes"])
        if ttc is not None and ttc <= thresholds.stop_ttc_s:
            selected_reasons.append("TIME_TO_CONFLICT_BELOW_STOP_THRESHOLD")
    elif severity == "medium" or (ttc is not None and ttc <= thresholds.slow_ttc_s):
        selected = Action.SLOW_YIELD
        selected_reasons = list(risk_payload["reason_codes"])
        if ttc is not None and ttc <= thresholds.slow_ttc_s:
            selected_reasons.append("TIME_TO_CONFLICT_BELOW_SLOW_THRESHOLD")
    else:
        selected = Action.CONTINUE
        selected_reasons = ["CONTINUE_WITHIN_SAFE_MARGIN"]

    candidates = []
    for action in Action:
        candidates.append(
            {
                "action": action.value,
                "accepted": action == selected,
                "reason_codes": selected_reasons if action == selected else ["REJECTED_BY_POLICY"],
            }
        )
    return selected, candidates, selected_reasons


@dataclass
class DecisionAgent:
    thresholds: Thresholds

    def decide(
        self,
        run_id: str,
        scene: SceneState,
        risk_event: dict[str, Any],
        prediction_event: dict[str, Any],
    ) -> dict[str, Any]:
        selected, candidates, reasons = select_action(
            risk_event["payload"], prediction_event["payload"], self.thresholds
        )
        evidence_id = f"{run_id}:decision"
        return make_event(
            run_id=run_id,
            scene=scene,
            agent="decision",
            evidence_id=evidence_id,
            inputs=risk_event["output_evidence_ids"] + prediction_event["output_evidence_ids"],
            payload={
                "evidence_id": evidence_id,
                "candidate_actions": candidates,
                "selected_action": selected.value,
                "reason_codes": reasons,
                "supporting_evidence_ids": risk_event["output_evidence_ids"]
                + prediction_event["output_evidence_ids"],
            },
        )


@dataclass
class ExplainabilityAgent:
    thresholds: Thresholds

    def explain(
        self,
        run_id: str,
        scene: SceneState,
        risk_event: dict[str, Any],
        prediction_event: dict[str, Any],
        decision_event: dict[str, Any],
    ) -> dict[str, Any]:
        risk = risk_event["payload"]
        prediction = prediction_event["payload"]
        decision = decision_event["payload"]
        action = Action(decision["selected_action"])
        ttc = prediction["time_to_conflict_s"]
        ttc_phrase = (
            f" The predicted time to conflict was {ttc:.2f} seconds."
            if ttc is not None
            else " No immediate time-to-conflict value was available."
        )

        causal = (
            f"The vehicle selected {action.value.replace('_', ' ').lower()} because the "
            f"{risk['hazard_type'].replace('_', ' ')} hazard was assessed as {risk['severity']} risk."
            f"{ttc_phrase}"
        )
        if action == Action.CONTROLLED_STOP:
            counterfactual = (
                "If the hazard had not been high risk and the predicted time to conflict had exceeded "
                f"{self.thresholds.slow_ttc_s:.2f} seconds, the vehicle could have continued."
            )
        elif action == Action.SLOW_YIELD:
            counterfactual = (
                f"If the predicted time to conflict had fallen to {self.thresholds.stop_ttc_s:.2f} "
                "seconds or below, the vehicle would have selected a controlled stop."
            )
        else:
            counterfactual = (
                "If an actor had entered the ego path or the predicted time to conflict had fallen below "
                f"{self.thresholds.slow_ttc_s:.2f} seconds, the vehicle would have slowed or stopped."
            )

        evidence_id = f"{run_id}:explanation"
        payload = {
            "evidence_id": evidence_id,
            "selected_action": action.value,
            "causal_text": causal,
            "counterfactual_text": counterfactual,
            "claim_evidence_map": [
                {
                    "claim": "selected action",
                    "evidence_ids": decision_event["output_evidence_ids"],
                },
                {
                    "claim": "hazard type and severity",
                    "evidence_ids": risk_event["output_evidence_ids"],
                },
                {
                    "claim": "time to conflict and configured counterfactual",
                    "evidence_ids": prediction_event["output_evidence_ids"]
                    + decision_event["output_evidence_ids"],
                },
            ],
        }
        return make_event(
            run_id=run_id,
            scene=scene,
            agent="explainability",
            evidence_id=evidence_id,
            inputs=risk_event["output_evidence_ids"]
            + prediction_event["output_evidence_ids"]
            + decision_event["output_evidence_ids"],
            payload=payload,
        )


def validate_explanation(
    trace: list[dict[str, Any]], decision_event: dict[str, Any], explanation_event: dict[str, Any]
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    explanation = explanation_event["payload"]
    known_evidence = {
        evidence_id
        for event in trace
        if event["agent"] != "explainability"
        for evidence_id in event["output_evidence_ids"]
    }

    if explanation["selected_action"] != decision_event["payload"]["selected_action"]:
        errors.append("Explanation action does not match the decision action.")
    if not explanation["causal_text"].strip() or not explanation["counterfactual_text"].strip():
        errors.append("Causal and counterfactual explanations are both required.")

    for mapping in explanation["claim_evidence_map"]:
        evidence_ids = mapping.get("evidence_ids", [])
        if not evidence_ids:
            errors.append(f"Claim has no evidence: {mapping.get('claim', '<unknown>')}")
        unknown = sorted(set(evidence_ids) - known_evidence)
        if unknown:
            errors.append(f"Claim cites unknown evidence IDs: {unknown}")

    return not errors, errors
