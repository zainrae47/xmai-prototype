from __future__ import annotations

from math import sqrt
from typing import Any

from .models import ActorState, SceneState


def _speed_mps(actor: Any) -> float:
    velocity = actor.get_velocity()
    return sqrt(velocity.x**2 + velocity.y**2 + velocity.z**2)


def _actor_type(type_id: str) -> str:
    if type_id.startswith("walker.pedestrian"):
        return "pedestrian"
    if type_id.startswith("vehicle"):
        return "vehicle"
    return "obstacle"


def capture_scene_state(
    *,
    world: Any,
    ego_vehicle: Any,
    scenario_id: str,
    seed: int,
    timestamp_s: float,
    decision_step: int = 0,
    lane_half_width_m: float = 2.5,
    maximum_actor_distance_m: float = 80.0,
) -> SceneState:
    """Convert a live CARLA world snapshot into the prototype's bounded scene state.

    The adapter chooses the nearest vehicle or pedestrian in front of the ego
    vehicle. It is a deterministic ground-truth adapter for initial experiments,
    not a learned perception model.
    """

    ego_transform = ego_vehicle.get_transform()
    ego_location = ego_transform.location
    forward = ego_transform.get_forward_vector()
    right = ego_transform.get_right_vector()

    candidates: list[tuple[float, float, Any]] = []
    for actor in world.get_actors():
        if actor.id == ego_vehicle.id:
            continue
        if not (
            actor.type_id.startswith("vehicle")
            or actor.type_id.startswith("walker.pedestrian")
        ):
            continue

        location = actor.get_location()
        relative_x = location.x - ego_location.x
        relative_y = location.y - ego_location.y
        relative_z = location.z - ego_location.z
        forward_distance = relative_x * forward.x + relative_y * forward.y
        lateral_distance = relative_x * right.x + relative_y * right.y
        euclidean_distance = sqrt(relative_x**2 + relative_y**2 + relative_z**2)

        if 0.0 < forward_distance <= maximum_actor_distance_m:
            candidates.append((euclidean_distance, lateral_distance, actor))

    if not candidates:
        raise RuntimeError("No relevant vehicle or pedestrian was found in front of the ego vehicle.")

    distance_m, lateral_distance_m, relevant_actor = min(candidates, key=lambda item: item[0])
    ego_waypoint = world.get_map().get_waypoint(ego_location)
    actor_waypoint = world.get_map().get_waypoint(relevant_actor.get_location())
    junction_conflict = bool(
        ego_waypoint
        and actor_waypoint
        and ego_waypoint.is_junction
        and actor_waypoint.is_junction
        and ego_waypoint.road_id != actor_waypoint.road_id
    )

    return SceneState(
        scenario_id=scenario_id,
        seed=seed,
        timestamp_s=timestamp_s,
        ego_speed_mps=round(_speed_mps(ego_vehicle), 4),
        relevant_actor=ActorState(
            actor_id=str(relevant_actor.id),
            actor_type=_actor_type(relevant_actor.type_id),
            distance_m=round(distance_m, 4),
            speed_mps=round(_speed_mps(relevant_actor), 4),
            in_ego_path=abs(lateral_distance_m) <= lane_half_width_m,
            visibility="clear",
        ),
        junction_conflict=junction_conflict,
        decision_step=decision_step,
    )
