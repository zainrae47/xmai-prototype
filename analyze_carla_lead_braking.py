from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev
from typing import Any

from xmai.pipeline import CONDITIONS


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "carla.json"
OUTPUT_DIR = ROOT / "output" / "carla_lead_braking"
REQUIRED_SUMMARY_FIELDS = {
    "seed",
    "condition",
    "ego_spawn_transform",
    "lead_spawn_transform",
    "initial_ego_speed_mps",
    "initial_lead_speed_mps",
    "initial_clearance_m",
    "minimum_clearance_m",
    "minimum_ttc_s",
    "first_non_continue_response_s",
    "mean_decision_latency_ms",
    "generated_explanations",
    "valid_explanations",
    "collision_count",
}


def rounded_mean(values: list[float], digits: int = 6) -> float | None:
    return round(mean(values), digits) if values else None


def rounded_sd(values: list[float], digits: int = 6) -> float:
    return round(stdev(values), digits) if len(values) > 1 else 0.0


def maximum_spread(values: list[float]) -> float:
    return max(values) - min(values) if values else math.inf


def read_action_sequence(condition: str, seed: int) -> list[str]:
    path = OUTPUT_DIR / f"ticks_{condition}_seed{seed}.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return [row["selected_action"] for row in csv.DictReader(stream)]


def load_summaries(configured_seeds: list[int]) -> dict[int, dict[str, dict[str, Any]]]:
    selected = set(configured_seeds)
    by_seed: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for path in sorted(OUTPUT_DIR.glob("summary_*_seed*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if not REQUIRED_SUMMARY_FIELDS.issubset(record):
            continue
        seed = int(record["seed"])
        condition = record["condition"]
        if seed in selected and condition in CONDITIONS:
            by_seed[seed][condition] = record
    return dict(by_seed)


def analyze() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    scenario_config = config["lead_braking"]
    configured_seeds = [int(seed) for seed in scenario_config["seeds"]]
    speed_tolerance = float(scenario_config["initial_speed_tolerance_mps"])
    clearance_tolerance = float(scenario_config["initial_clearance_tolerance_m"])
    by_seed = load_summaries(configured_seeds)

    missing_runs: list[dict[str, Any]] = []
    seed_checks: list[dict[str, Any]] = []
    for seed in configured_seeds:
        records = by_seed.get(seed, {})
        missing = [condition for condition in CONDITIONS if condition not in records]
        for condition in missing:
            missing_runs.append({"seed": seed, "condition": condition})
        if missing:
            seed_checks.append(
                {
                    "seed": seed,
                    "complete": False,
                    "missing_conditions": missing,
                    "spawn_match": False,
                    "initial_state_match": False,
                    "action_sequence_match": False,
                    "collision_outcome_match": False,
                    "comparable": False,
                }
            )
            continue

        ordered = [records[condition] for condition in CONDITIONS]
        ego_spawns = {json.dumps(item["ego_spawn_transform"], sort_keys=True) for item in ordered}
        lead_spawns = {json.dumps(item["lead_spawn_transform"], sort_keys=True) for item in ordered}
        initial_ego_speeds = [float(item["initial_ego_speed_mps"]) for item in ordered]
        initial_lead_speeds = [float(item["initial_lead_speed_mps"]) for item in ordered]
        initial_clearances = [float(item["initial_clearance_m"]) for item in ordered]
        sequences = [read_action_sequence(condition, seed) for condition in CONDITIONS]

        spawn_match = len(ego_spawns) == 1 and len(lead_spawns) == 1
        initial_state_match = (
            maximum_spread(initial_ego_speeds) <= speed_tolerance
            and maximum_spread(initial_lead_speeds) <= speed_tolerance
            and maximum_spread(initial_clearances) <= clearance_tolerance
        )
        action_sequence_match = all(sequences) and all(
            sequence == sequences[0] for sequence in sequences[1:]
        )
        collision_outcome_match = len(
            {int(item["collision_count"]) for item in ordered}
        ) == 1
        seed_checks.append(
            {
                "seed": seed,
                "complete": True,
                "missing_conditions": [],
                "spawn_match": spawn_match,
                "initial_state_match": initial_state_match,
                "maximum_initial_ego_speed_spread_mps": round(
                    maximum_spread(initial_ego_speeds), 6
                ),
                "maximum_initial_lead_speed_spread_mps": round(
                    maximum_spread(initial_lead_speeds), 6
                ),
                "maximum_initial_clearance_spread_m": round(
                    maximum_spread(initial_clearances), 6
                ),
                "action_sequence_match": action_sequence_match,
                "collision_outcome_match": collision_outcome_match,
                "comparable": spawn_match and initial_state_match,
            }
        )

    condition_summaries: list[dict[str, Any]] = []
    for condition in CONDITIONS:
        records = [
            by_seed[seed][condition]
            for seed in configured_seeds
            if seed in by_seed and condition in by_seed[seed]
        ]
        clearance_values = [float(item["minimum_clearance_m"]) for item in records]
        ttc_values = [
            float(item["minimum_ttc_s"])
            for item in records
            if item["minimum_ttc_s"] is not None
        ]
        response_values = [
            float(item["first_non_continue_response_s"])
            for item in records
            if item["first_non_continue_response_s"] is not None
        ]
        latency_values = [float(item["mean_decision_latency_ms"]) for item in records]
        total_explanations = sum(int(item["generated_explanations"]) for item in records)
        valid_explanations = sum(int(item["valid_explanations"]) for item in records)
        collision_count = sum(int(item["collision_count"]) for item in records)
        condition_summaries.append(
            {
                "condition": condition,
                "runs": len(records),
                "collisions": collision_count,
                "collision_avoidance_rate": round(
                    (len(records) - sum(int(item["collision_count"]) > 0 for item in records))
                    / len(records),
                    4,
                )
                if records
                else None,
                "minimum_clearance_mean_m": rounded_mean(clearance_values),
                "minimum_clearance_sd_m": rounded_sd(clearance_values),
                "minimum_clearance_worst_m": round(min(clearance_values), 6)
                if clearance_values
                else None,
                "minimum_ttc_mean_s": rounded_mean(ttc_values),
                "minimum_ttc_sd_s": rounded_sd(ttc_values),
                "minimum_ttc_worst_s": round(min(ttc_values), 6) if ttc_values else None,
                "response_time_mean_s": rounded_mean(response_values),
                "response_time_sd_s": rounded_sd(response_values),
                "mean_pipeline_latency_ms": rounded_mean(latency_values),
                "pipeline_latency_sd_ms": rounded_sd(latency_values),
                "generated_explanations": total_explanations,
                "valid_explanations": valid_explanations,
                "explanation_validity_rate": round(
                    valid_explanations / total_explanations, 4
                )
                if total_explanations
                else None,
            }
        )

    complete_checks = [item for item in seed_checks if item["complete"]]
    comparable_checks = [item for item in complete_checks if item["comparable"]]
    action_matched_checks = [
        item for item in complete_checks if item["action_sequence_match"]
    ]
    result = {
        "experiment": "CARLA lead-vehicle braking repeated evaluation",
        "carla_version": config["version"],
        "map": config["experiment_map"],
        "fixed_delta_seconds": config["fixed_delta_seconds"],
        "configured_seeds": configured_seeds,
        "expected_runs": len(configured_seeds) * len(CONDITIONS),
        "completed_runs": sum(len(records) for records in by_seed.values()),
        "missing_runs": missing_runs,
        "complete_seed_count": len(complete_checks),
        "comparable_seed_count": len(comparable_checks),
        "action_sequence_matched_seed_count": len(action_matched_checks),
        "initial_state_tolerances": {
            "speed_mps": speed_tolerance,
            "clearance_m": clearance_tolerance,
        },
        "condition_summaries": condition_summaries,
        "seed_checks": seed_checks,
        "analysis_status": (
            "complete"
            if not missing_runs and len(comparable_checks) == len(configured_seeds)
            else "complete_with_reproducibility_flags"
            if not missing_runs
            else "incomplete"
        ),
    }
    return result


def format_value(value: Any, digits: int = 4) -> str:
    if value is None:
        return "Not applicable"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def write_outputs(analysis: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "analysis.json").write_text(
        json.dumps(analysis, indent=2, sort_keys=True), encoding="utf-8"
    )

    lines = [
        "# CARLA Lead-Braking Repeated Evaluation",
        "",
        "## Evaluation status",
        "",
        f"- Analysis status: `{analysis['analysis_status']}`",
        f"- Completed runs: {analysis['completed_runs']} of {analysis['expected_runs']}",
        f"- Complete seed sets: {analysis['complete_seed_count']} of {len(analysis['configured_seeds'])}",
        f"- Initial-state comparable seed sets: {analysis['comparable_seed_count']} of {len(analysis['configured_seeds'])}",
        f"- Identical action-sequence seed sets: {analysis['action_sequence_matched_seed_count']} of {len(analysis['configured_seeds'])}",
        "",
        "## Condition-level results",
        "",
        "| Condition | Runs | Collisions | Avoidance rate | Mean minimum clearance (m) | Mean minimum TTC (s) | Mean response (s) | Mean pipeline latency (ms) | Explanation validity |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in analysis["condition_summaries"]:
        avoidance = (
            f"{100 * item['collision_avoidance_rate']:.1f}%"
            if item["collision_avoidance_rate"] is not None
            else "Not applicable"
        )
        explanation = (
            f"{item['valid_explanations']}/{item['generated_explanations']} "
            f"({100 * item['explanation_validity_rate']:.1f}%)"
            if item["explanation_validity_rate"] is not None
            else "Not generated"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    item["condition"],
                    str(item["runs"]),
                    str(item["collisions"]),
                    avoidance,
                    format_value(item["minimum_clearance_mean_m"]),
                    format_value(item["minimum_ttc_mean_s"]),
                    format_value(item["response_time_mean_s"]),
                    format_value(item["mean_pipeline_latency_ms"], 6),
                    explanation,
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            "These results are from a bounded CARLA simulation using simulator ground-truth state. They evaluate the implemented decision, evidence, and explanation pipeline; they do not demonstrate real-world vehicle safety or learned perception performance.",
            "",
            "A seed is considered comparable when all three conditions use identical recorded spawn transforms and their initial ego speed, lead speed, and clearance fall within the configured tolerances. Action-sequence mismatches are reported rather than discarded.",
            "",
        ]
    )
    if analysis["missing_runs"]:
        lines.extend(["## Missing runs", ""])
        for item in analysis["missing_runs"]:
            lines.append(f"- Seed {item['seed']}: {item['condition']}")
        lines.append("")
    (OUTPUT_DIR / "analysis_report.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a non-zero exit status when runs are missing or initial-state checks fail.",
    )
    args = parser.parse_args()
    analysis = analyze()
    write_outputs(analysis)
    print(json.dumps(analysis, indent=2, sort_keys=True))
    if args.strict and analysis["analysis_status"] != "complete":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
