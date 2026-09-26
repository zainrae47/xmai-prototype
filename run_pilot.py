from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from xmai.models import Thresholds
from xmai.pipeline import CONDITIONS, XMAIPipeline
from xmai.scenarios import SCENARIO_IDS, action_is_safe, create_scene, required_action


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "default.json"
OUTPUT_DIR = ROOT / "output"


def load_thresholds() -> Thresholds:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return Thresholds(
        stop_ttc_s=float(config["stop_ttc_s"]),
        slow_ttc_s=float(config["slow_ttc_s"]),
        near_distance_m=float(config["near_distance_m"]),
    )


def main() -> None:
    thresholds = load_thresholds()
    pipeline = XMAIPipeline(thresholds)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    evidence_records: list[dict[str, object]] = []

    for scenario_id in SCENARIO_IDS:
        for seed in range(10):
            scene = create_scene(scenario_id, seed)
            expected = required_action(scene, thresholds)
            rotation = seed % len(CONDITIONS)
            condition_order = CONDITIONS[rotation:] + CONDITIONS[:rotation]
            for condition in condition_order:
                result = pipeline.run(scene, condition)
                rows.append(
                    {
                        "run_id": result.run_id,
                        "scenario_id": scenario_id,
                        "seed": seed,
                        "condition": condition,
                        "selected_action": result.selected_action.value,
                        "required_action": expected.value,
                        "hazard_avoided": action_is_safe(result.selected_action, expected),
                        "latency_ms": round(result.latency_ms, 6),
                        "trace_steps": len(result.trace),
                        "explanation_valid": result.explanation_valid,
                    }
                )
                evidence_records.append(result.to_dict())

    results_path = OUTPUT_DIR / "pilot_results.csv"
    with results_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    evidence_path = OUTPUT_DIR / "evidence.jsonl"
    with evidence_path.open("w", encoding="utf-8") as stream:
        for record in evidence_records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    summaries: dict[str, dict[str, object]] = {}
    for condition in CONDITIONS:
        condition_rows = [row for row in rows if row["condition"] == condition]
        summaries[condition] = {
            "runs": len(condition_rows),
            "hazards_avoided": sum(bool(row["hazard_avoided"]) for row in condition_rows),
            "mean_latency_ms": round(
                sum(float(row["latency_ms"]) for row in condition_rows) / len(condition_rows), 6
            ),
            "valid_explanations": sum(row["explanation_valid"] is True for row in condition_rows),
        }

    summary = {
        "pilot_type": "deterministic scripted-state pilot; not a CARLA result",
        "latency_note": "Single-process pilot timings are engineering checks, not CARLA performance evidence.",
        "thresholds": asdict(thresholds),
        "scenario_count": len(SCENARIO_IDS),
        "seeds_per_scenario": 10,
        "total_runs": len(rows),
        "conditions": summaries,
    }
    summary_path = OUTPUT_DIR / "pilot_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"Results: {results_path}")
    print(f"Evidence: {evidence_path}")


if __name__ == "__main__":
    main()
