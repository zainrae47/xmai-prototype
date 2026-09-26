from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class Action(str, Enum):
    CONTINUE = "CONTINUE"
    SLOW_YIELD = "SLOW_YIELD"
    CONTROLLED_STOP = "CONTROLLED_STOP"


@dataclass(frozen=True)
class Thresholds:
    stop_ttc_s: float = 2.5
    slow_ttc_s: float = 5.0
    near_distance_m: float = 12.0


@dataclass(frozen=True)
class ActorState:
    actor_id: str
    actor_type: str
    distance_m: float
    speed_mps: float
    in_ego_path: bool
    visibility: str = "clear"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SceneState:
    scenario_id: str
    seed: int
    timestamp_s: float
    ego_speed_mps: float
    relevant_actor: ActorState
    junction_conflict: bool = False
    decision_step: int = 0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["relevant_actor"] = self.relevant_actor.to_dict()
        return value


@dataclass(frozen=True)
class PipelineResult:
    run_id: str
    condition: str
    scenario_id: str
    seed: int
    selected_action: Action
    trace: list[dict[str, Any]]
    explanation: dict[str, Any] | None
    explanation_valid: bool | None
    validation_errors: list[str]
    latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "condition": self.condition,
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "selected_action": self.selected_action.value,
            "trace": self.trace,
            "explanation": self.explanation,
            "explanation_valid": self.explanation_valid,
            "validation_errors": self.validation_errors,
            "latency_ms": self.latency_ms,
        }
