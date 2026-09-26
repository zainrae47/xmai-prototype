from __future__ import annotations

from time import perf_counter
from typing import Any

from .agents import (
    DecisionAgent,
    ExplainabilityAgent,
    PredictionAgent,
    RiskAgent,
    SceneStateAgent,
    make_event,
    select_action,
    validate_explanation,
)
from .models import Action, PipelineResult, SceneState, Thresholds


CONDITIONS = ("centralised_baseline", "xmai_decision", "xmai_explanation")


class XMAIPipeline:
    def __init__(self, thresholds: Thresholds | None = None) -> None:
        self.thresholds = thresholds or Thresholds()
        self.scene_agent = SceneStateAgent()
        self.risk_agent = RiskAgent(self.thresholds)
        self.prediction_agent = PredictionAgent()
        self.decision_agent = DecisionAgent(self.thresholds)
        self.explanation_agent = ExplainabilityAgent(self.thresholds)

    def run(self, scene: SceneState, condition: str) -> PipelineResult:
        if condition not in CONDITIONS:
            raise ValueError(f"Unknown condition: {condition}")

        run_id = (
            f"{condition}:{scene.scenario_id}:{scene.seed}:"
            f"step-{scene.decision_step:04d}"
        )
        started = perf_counter()

        scene_event = self.scene_agent.process(run_id, scene)
        risk_event = self.risk_agent.assess(run_id, scene, scene_event)
        prediction_event = self.prediction_agent.predict(run_id, scene, scene_event, risk_event)

        if condition == "centralised_baseline":
            selected, candidates, reasons = select_action(
                risk_event["payload"], prediction_event["payload"], self.thresholds
            )
            evidence_id = f"{run_id}:baseline_decision"
            baseline_event = make_event(
                run_id=run_id,
                scene=scene,
                agent="centralised_baseline",
                evidence_id=evidence_id,
                inputs=[],
                payload={
                    "evidence_id": evidence_id,
                    "selected_action": selected.value,
                    "candidate_actions": candidates,
                    "reason_codes": reasons,
                },
            )
            trace = [baseline_event]
            decision_event = baseline_event
            explanation_event = None
            explanation_valid = None
            validation_errors: list[str] = []
        else:
            decision_event = self.decision_agent.decide(
                run_id, scene, risk_event, prediction_event
            )
            selected = Action(decision_event["payload"]["selected_action"])
            trace = [scene_event, risk_event, prediction_event, decision_event]
            explanation_event: dict[str, Any] | None = None
            explanation_valid: bool | None = None
            validation_errors = []

            if condition == "xmai_explanation":
                explanation_event = self.explanation_agent.explain(
                    run_id, scene, risk_event, prediction_event, decision_event
                )
                trace.append(explanation_event)
                explanation_valid, validation_errors = validate_explanation(
                    trace, decision_event, explanation_event
                )

        latency_ms = (perf_counter() - started) * 1000.0
        return PipelineResult(
            run_id=run_id,
            condition=condition,
            scenario_id=scene.scenario_id,
            seed=scene.seed,
            selected_action=selected,
            trace=trace,
            explanation=explanation_event["payload"] if explanation_event else None,
            explanation_valid=explanation_valid,
            validation_errors=validation_errors,
            latency_ms=latency_ms,
        )
