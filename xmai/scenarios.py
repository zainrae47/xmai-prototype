from __future__ import annotations

import random

from .models import Action, ActorState, SceneState, Thresholds


SCENARIO_IDS = ("lead_vehicle_braking", "pedestrian_crossing", "junction_conflict")


def create_scene(scenario_id: str, seed: int) -> SceneState:
    if scenario_id not in SCENARIO_IDS:
        raise ValueError(f"Unknown scenario: {scenario_id}")

    rng = random.Random(f"{scenario_id}:{seed}")
    if scenario_id == "lead_vehicle_braking":
        scene = SceneState(
            scenario_id=scenario_id,
            seed=seed,
            timestamp_s=0.0,
            ego_speed_mps=round(rng.uniform(9.0, 13.0), 3),
            relevant_actor=ActorState(
                actor_id="lead-vehicle",
                actor_type="vehicle",
                distance_m=round(rng.uniform(10.0, 50.0), 3),
                speed_mps=round(rng.uniform(1.0, 8.0), 3),
                in_ego_path=True,
            ),
        )
    elif scenario_id == "pedestrian_crossing":
        scene = SceneState(
            scenario_id=scenario_id,
            seed=seed,
            timestamp_s=0.0,
            ego_speed_mps=round(rng.uniform(6.0, 10.0), 3),
            relevant_actor=ActorState(
                actor_id="pedestrian",
                actor_type="pedestrian",
                distance_m=round(rng.uniform(7.0, 50.0), 3),
                speed_mps=round(rng.uniform(0.8, 1.8), 3),
                in_ego_path=True,
                visibility="partial" if rng.random() < 0.4 else "clear",
            ),
        )
    else:
        scene = SceneState(
            scenario_id=scenario_id,
            seed=seed,
            timestamp_s=0.0,
            ego_speed_mps=round(rng.uniform(7.0, 11.0), 3),
            relevant_actor=ActorState(
                actor_id="cross-traffic-vehicle",
                actor_type="vehicle",
                distance_m=round(rng.uniform(9.0, 60.0), 3),
                speed_mps=round(rng.uniform(4.0, 9.0), 3),
                in_ego_path=True,
            ),
            junction_conflict=True,
        )
    return scene


def required_action(scene: SceneState, thresholds: Thresholds) -> Action:
    actor = scene.relevant_actor
    if actor.actor_type == "vehicle" and not scene.junction_conflict:
        closing_speed = max(scene.ego_speed_mps - actor.speed_mps, 0.0)
        ttc = actor.distance_m / closing_speed if closing_speed > 0 else None
    else:
        ttc = actor.distance_m / scene.ego_speed_mps if scene.ego_speed_mps > 0 else None

    if (
        actor.visibility == "partial"
        and actor.in_ego_path
        and actor.distance_m <= thresholds.near_distance_m * 2
    ):
        return Action.CONTROLLED_STOP
    if actor.distance_m <= thresholds.near_distance_m:
        return Action.CONTROLLED_STOP
    if ttc is not None and ttc <= thresholds.stop_ttc_s:
        return Action.CONTROLLED_STOP
    if actor.in_ego_path or scene.junction_conflict:
        if ttc is not None and ttc <= thresholds.slow_ttc_s:
            return Action.SLOW_YIELD
    return Action.CONTINUE


def action_is_safe(selected: Action, required: Action) -> bool:
    conservatism = {
        Action.CONTINUE: 0,
        Action.SLOW_YIELD: 1,
        Action.CONTROLLED_STOP: 2,
    }
    return conservatism[selected] >= conservatism[required]
