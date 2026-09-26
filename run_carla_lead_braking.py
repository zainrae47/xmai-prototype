from __future__ import annotations

import csv
import argparse
import hashlib
import json
import math
import os
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from xmai.carla_adapter import capture_scene_state
from xmai.models import Action, Thresholds
from xmai.pipeline import CONDITIONS, XMAIPipeline


ROOT = Path(__file__).resolve().parent
CARLA_CONFIG_PATH = ROOT / "config" / "carla.json"
POLICY_CONFIG_PATH = ROOT / "config" / "default.json"
OUTPUT_DIR = ROOT / "output" / "carla_lead_braking"


def load_configuration() -> tuple[dict[str, Any], Thresholds]:
    carla_config = json.loads(CARLA_CONFIG_PATH.read_text(encoding="utf-8"))
    policy_config = json.loads(POLICY_CONFIG_PATH.read_text(encoding="utf-8"))
    thresholds = Thresholds(
        stop_ttc_s=float(policy_config["stop_ttc_s"]),
        slow_ttc_s=float(policy_config["slow_ttc_s"]),
        near_distance_m=float(policy_config["near_distance_m"]),
    )
    return carla_config, thresholds


def speed_mps(actor: Any) -> float:
    velocity = actor.get_velocity()
    return math.sqrt(velocity.x**2 + velocity.y**2 + velocity.z**2)


def vector_at_speed(carla: Any, actor: Any, requested_speed_mps: float) -> Any:
    forward = actor.get_transform().get_forward_vector()
    return carla.Vector3D(
        x=forward.x * requested_speed_mps,
        y=forward.y * requested_speed_mps,
        z=forward.z * requested_speed_mps,
    )


def percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def action_sequence_hash(actions: list[str]) -> str:
    encoded = json.dumps(actions, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def clone_transform(carla: Any, transform: Any, height_offset_m: float = 0.4) -> Any:
    return carla.Transform(
        carla.Location(
            x=transform.location.x,
            y=transform.location.y,
            z=transform.location.z + height_offset_m,
        ),
        carla.Rotation(
            pitch=transform.rotation.pitch,
            yaw=transform.rotation.yaw,
            roll=transform.rotation.roll,
        ),
    )


def choose_spawn_pair(carla: Any, world_map: Any, gap_m: float, seed: int) -> tuple[Any, Any]:
    pairs: list[tuple[Any, Any]] = []
    for spawn_transform in world_map.get_spawn_points():
        waypoint = world_map.get_waypoint(
            spawn_transform.location,
            project_to_road=True,
            lane_type=carla.LaneType.Driving,
        )
        if waypoint is None or waypoint.is_junction:
            continue
        for lead_waypoint in waypoint.next(gap_m):
            if (
                not lead_waypoint.is_junction
                and lead_waypoint.road_id == waypoint.road_id
                and lead_waypoint.lane_id == waypoint.lane_id
            ):
                pairs.append(
                    (
                        clone_transform(carla, waypoint.transform),
                        clone_transform(carla, lead_waypoint.transform),
                    )
                )
                break
    if not pairs:
        raise RuntimeError("No suitable same-lane spawn pair was found for the braking scenario.")
    pairs.sort(
        key=lambda pair: (
            round(pair[0].location.x, 3),
            round(pair[0].location.y, 3),
            round(pair[0].location.z, 3),
            round(pair[0].rotation.yaw, 3),
        )
    )
    return pairs[seed % len(pairs)]


def transform_record(transform: Any) -> dict[str, float]:
    return {
        "x": round(transform.location.x, 4),
        "y": round(transform.location.y, 4),
        "z": round(transform.location.z, 4),
        "pitch": round(transform.rotation.pitch, 4),
        "yaw": round(transform.rotation.yaw, 4),
        "roll": round(transform.rotation.roll, 4),
    }


def prepare_blueprint(library: Any, preferred_id: str, role_name: str) -> Any:
    try:
        blueprint = library.find(preferred_id)
    except RuntimeError:
        candidates = [
            item
            for item in library.filter("vehicle.*")
            if not item.has_attribute("number_of_wheels")
            or int(item.get_attribute("number_of_wheels")) == 4
        ]
        if not candidates:
            raise RuntimeError("No four-wheel vehicle blueprint is available.")
        blueprint = sorted(candidates, key=lambda item: item.id)[0]
    if blueprint.has_attribute("role_name"):
        blueprint.set_attribute("role_name", role_name)
    if blueprint.has_attribute("color"):
        colors = blueprint.get_attribute("color").recommended_values
        if colors:
            blueprint.set_attribute("color", colors[0])
    return blueprint


def apply_action(carla: Any, ego_vehicle: Any, action: Action) -> None:
    if action == Action.CONTINUE:
        control = carla.VehicleControl(throttle=0.35, brake=0.0, steer=0.0)
    elif action == Action.SLOW_YIELD:
        control = carla.VehicleControl(throttle=0.0, brake=0.35, steer=0.0)
    else:
        control = carla.VehicleControl(throttle=0.0, brake=1.0, steer=0.0)
    ego_vehicle.apply_control(control)


def configure_deterministic_world(client: Any, map_name: str, delta_seconds: float) -> Any:
    world = client.get_world()
    current_map_name = world.get_map().name.rsplit("/", 1)[-1]
    if current_map_name != map_name:
        world = client.load_world(map_name)
    settings = world.get_settings()
    settings.synchronous_mode = True
    settings.fixed_delta_seconds = delta_seconds
    settings.no_rendering_mode = True
    world.apply_settings(settings)
    world.tick()
    return world


def release_data_collection_world(world: Any) -> None:
    # Release synchronous stepping without restarting graphics between runs.
    settings = world.get_settings()
    settings.synchronous_mode = False
    settings.fixed_delta_seconds = None
    settings.no_rendering_mode = True
    world.apply_settings(settings)


def run_condition(
    *,
    carla: Any,
    client: Any,
    config: dict[str, Any],
    pipeline: XMAIPipeline,
    condition: str,
    seed: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    scenario_config = config["lead_braking"]
    delta_seconds = float(config["fixed_delta_seconds"])
    world = configure_deterministic_world(client, config["experiment_map"], delta_seconds)
    spawned_actors: list[Any] = []
    collision_events: list[dict[str, Any]] = []

    try:
        world_map = world.get_map()
        ego_transform, lead_transform = choose_spawn_pair(
            carla, world_map, float(scenario_config["initial_gap_m"]), seed
        )
        library = world.get_blueprint_library()
        ego_blueprint = prepare_blueprint(library, "vehicle.tesla.model3", "hero")
        lead_blueprint = prepare_blueprint(library, "vehicle.audi.tt", "lead")

        spawn_commands = [
            carla.command.SpawnActor(ego_blueprint, ego_transform),
            carla.command.SpawnActor(lead_blueprint, lead_transform),
        ]
        responses = client.apply_batch_sync(spawn_commands, True)
        errors = [response.error for response in responses if response.error]
        if errors:
            raise RuntimeError(f"Vehicle spawn failed: {errors}")

        ego_vehicle = world.get_actor(responses[0].actor_id)
        lead_vehicle = world.get_actor(responses[1].actor_id)
        if ego_vehicle is None or lead_vehicle is None:
            raise RuntimeError("Spawned vehicle actors could not be retrieved.")
        spawned_actors.extend([ego_vehicle, lead_vehicle])

        collision_blueprint = library.find("sensor.other.collision")
        collision_sensor = world.spawn_actor(
            collision_blueprint, carla.Transform(), attach_to=ego_vehicle
        )
        spawned_actors.append(collision_sensor)

        def on_collision(event: Any) -> None:
            impulse = event.normal_impulse
            collision_events.append(
                {
                    "frame": event.frame,
                    "other_actor_id": event.other_actor.id,
                    "other_actor_type": event.other_actor.type_id,
                    "impulse": math.sqrt(impulse.x**2 + impulse.y**2 + impulse.z**2),
                }
            )

        collision_sensor.listen(on_collision)

        ego_initial_speed = float(scenario_config["ego_initial_speed_mps"])
        lead_initial_speed = float(scenario_config["lead_initial_speed_mps"])
        warmup_steps = round(float(scenario_config["warmup_seconds"]) / delta_seconds)
        for _ in range(warmup_steps):
            ego_vehicle.apply_control(
                carla.VehicleControl(throttle=0.0, brake=0.0, steer=0.0)
            )
            lead_vehicle.apply_control(
                carla.VehicleControl(throttle=0.0, brake=0.0, steer=0.0)
            )
            ego_vehicle.set_target_velocity(vector_at_speed(carla, ego_vehicle, ego_initial_speed))
            lead_vehicle.set_target_velocity(vector_at_speed(carla, lead_vehicle, lead_initial_speed))
            world.tick()

        # Reset the measured starting velocities immediately before the hazard.
        # This removes warm-up drift without advancing the synchronous world.
        ego_vehicle.set_target_velocity(vector_at_speed(carla, ego_vehicle, ego_initial_speed))
        lead_vehicle.set_target_velocity(vector_at_speed(carla, lead_vehicle, lead_initial_speed))
        ego_vehicle.set_target_angular_velocity(carla.Vector3D())
        lead_vehicle.set_target_angular_velocity(carla.Vector3D())

        lead_vehicle.apply_control(
            carla.VehicleControl(throttle=0.0, brake=1.0, steer=0.0)
        )

        tick_rows: list[dict[str, Any]] = []
        evidence_rows: list[dict[str, Any]] = []
        decision_latencies: list[float] = []
        action_counts: Counter[str] = Counter()
        min_clearance_m = math.inf
        min_ttc_s = math.inf
        first_response_s: float | None = None
        stop_streak = 0
        maximum_steps = round(
            float(scenario_config["maximum_response_seconds"]) / delta_seconds
        )

        for decision_step in range(maximum_steps):
            snapshot = world.get_snapshot()
            simulation_time_s = decision_step * delta_seconds
            scene = capture_scene_state(
                world=world,
                ego_vehicle=ego_vehicle,
                scenario_id="lead_vehicle_braking",
                seed=seed,
                timestamp_s=snapshot.timestamp.elapsed_seconds,
                decision_step=decision_step,
            )
            decision_result = pipeline.run(scene, condition)
            selected_action = decision_result.selected_action
            apply_action(carla, ego_vehicle, selected_action)

            ego_speed = speed_mps(ego_vehicle)
            lead_speed = speed_mps(lead_vehicle)
            center_distance = ego_vehicle.get_location().distance(lead_vehicle.get_location())
            vehicle_half_lengths = (
                ego_vehicle.bounding_box.extent.x + lead_vehicle.bounding_box.extent.x
            )
            clearance_m = max(0.0, center_distance - vehicle_half_lengths)
            closing_speed = max(ego_speed - lead_speed, 0.0)
            ttc_s = clearance_m / closing_speed if closing_speed > 0.01 else math.inf

            min_clearance_m = min(min_clearance_m, clearance_m)
            min_ttc_s = min(min_ttc_s, ttc_s)
            action_counts[selected_action.value] += 1
            decision_latencies.append(decision_result.latency_ms)
            if first_response_s is None and selected_action != Action.CONTINUE:
                first_response_s = simulation_time_s

            tick_rows.append(
                {
                    "condition": condition,
                    "seed": seed,
                    "decision_step": decision_step,
                    "simulation_time_s": round(simulation_time_s, 4),
                    "ego_speed_mps": round(ego_speed, 4),
                    "lead_speed_mps": round(lead_speed, 4),
                    "clearance_m": round(clearance_m, 4),
                    "ttc_s": round(ttc_s, 4) if math.isfinite(ttc_s) else None,
                    "selected_action": selected_action.value,
                    "decision_latency_ms": round(decision_result.latency_ms, 6),
                    "explanation_valid": decision_result.explanation_valid,
                    "collision_count": len(collision_events),
                }
            )
            evidence_rows.append(decision_result.to_dict())
            world.tick()

            if ego_speed < 0.2 and lead_speed < 0.2:
                stop_streak += 1
            else:
                stop_streak = 0
            if stop_streak >= 5 or collision_events:
                break

        valid_explanations = sum(
            row["explanation_valid"] is True for row in tick_rows
        )
        generated_explanations = sum(
            row["explanation_valid"] is not None for row in tick_rows
        )
        summary = {
            "condition": condition,
            "seed": seed,
            "map": world_map.name,
            "fixed_delta_seconds": delta_seconds,
            "data_source": "carla_actor_state",
            "no_rendering_mode": bool(world.get_settings().no_rendering_mode),
            "ego_spawn_transform": transform_record(ego_transform),
            "lead_spawn_transform": transform_record(lead_transform),
            "ticks": len(tick_rows),
            "initial_ego_speed_mps": tick_rows[0]["ego_speed_mps"],
            "initial_lead_speed_mps": tick_rows[0]["lead_speed_mps"],
            "initial_clearance_m": tick_rows[0]["clearance_m"],
            "initial_ttc_s": tick_rows[0]["ttc_s"],
            "collision_count": len(collision_events),
            "collision_avoided": len(collision_events) == 0,
            "minimum_clearance_m": round(min_clearance_m, 4),
            "minimum_ttc_s": round(min_ttc_s, 4) if math.isfinite(min_ttc_s) else None,
            "first_non_continue_response_s": first_response_s,
            "final_ego_speed_mps": tick_rows[-1]["ego_speed_mps"],
            "final_lead_speed_mps": tick_rows[-1]["lead_speed_mps"],
            "action_counts": dict(action_counts),
            "action_sequence_sha256": action_sequence_hash(
                [row["selected_action"] for row in tick_rows]
            ),
            "mean_decision_latency_ms": round(mean(decision_latencies), 6),
            "p95_decision_latency_ms": round(percentile_95(decision_latencies), 6),
            "generated_explanations": generated_explanations,
            "valid_explanations": valid_explanations,
            "collision_events": collision_events,
            "scenario_configuration": {
                "initial_gap_m": float(scenario_config["initial_gap_m"]),
                "ego_initial_speed_mps": ego_initial_speed,
                "lead_initial_speed_mps": lead_initial_speed,
                "warmup_seconds": float(scenario_config["warmup_seconds"]),
                "maximum_response_seconds": float(
                    scenario_config["maximum_response_seconds"]
                ),
            },
        }
        return summary, tick_rows, evidence_rows
    finally:
        for actor in spawned_actors:
            if actor is not None and actor.type_id.startswith("sensor"):
                actor.stop()
        actor_ids = [actor.id for actor in spawned_actors if actor is not None]
        if actor_ids:
            client.apply_batch_sync(
                [carla.command.DestroyActor(actor_id) for actor_id in actor_ids], True
            )
        release_data_collection_world(world)


def write_run_outputs(
    summary: dict[str, Any],
    tick_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    output_dir: Path = OUTPUT_DIR,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    condition = summary["condition"]
    seed = summary["seed"]
    stem = f"{condition}_seed{seed}"

    tick_path = output_dir / f"ticks_{stem}.csv"
    with tick_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(tick_rows[0]))
        writer.writeheader()
        writer.writerows(tick_rows)

    evidence_path = output_dir / f"evidence_{stem}.jsonl"
    with evidence_path.open("w", encoding="utf-8") as stream:
        for record in evidence_rows:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    summary_path = output_dir / f"summary_{stem}.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )


def update_aggregate_summary(carla_version: str, output_dir: Path = OUTPUT_DIR) -> Path:
    summaries = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(output_dir.glob("summary_*_seed*.json"))
    ]
    aggregate_path = output_dir / "summary.json"
    aggregate = {
        "experiment": "CARLA lead-vehicle braking proof-of-concept",
        "carla_version": carla_version,
        "research_status": "initial experiment; not final dissertation evidence",
        "conditions": summaries,
    }
    aggregate_path.write_text(
        json.dumps(aggregate, indent=2, sort_keys=True), encoding="utf-8"
    )
    return aggregate_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect CARLA vehicle state and decision evidence without rendering a scene."
    )
    parser.add_argument(
        "--condition",
        choices=CONDITIONS,
        help="Run one condition. Omit to run all conditions in one server session.",
    )
    parser.add_argument("--seed", type=int, help="Run one configured seed.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Directory for CSV/JSON/JSONL files. Use a separate directory for practice runs.",
    )
    args = parser.parse_args()

    carla_config, thresholds = load_configuration()
    cache_path = ROOT / carla_config["cache_directory"]
    cache_path.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("CARLA_CACHE_DIR", str(cache_path))

    import carla

    client = carla.Client(carla_config["host"], int(carla_config["rpc_port"]))
    client.set_timeout(120.0)
    pipeline = XMAIPipeline(thresholds)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Data collection only; scene rendering disabled. Saving to: {output_dir}", flush=True)

    selected_conditions = (args.condition,) if args.condition else CONDITIONS
    configured_seeds = [int(seed) for seed in carla_config["lead_braking"]["seeds"]]
    selected_seeds = [args.seed] if args.seed is not None else configured_seeds

    for seed in selected_seeds:
        for condition in selected_conditions:
            print(f"Running condition={condition}, seed={seed}...", flush=True)
            summary, tick_rows, evidence_rows = run_condition(
                carla=carla,
                client=client,
                config=carla_config,
                pipeline=pipeline,
                condition=condition,
                seed=int(seed),
            )
            write_run_outputs(summary, tick_rows, evidence_rows, output_dir)
            print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
            print(f"Saved run outputs for {condition}, seed={seed}.", flush=True)

    aggregate_path = update_aggregate_summary(client.get_server_version(), output_dir)
    print(f"Aggregate summary: {aggregate_path}")


if __name__ == "__main__":
    main()
