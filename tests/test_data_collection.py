from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from run_carla_lead_braking import (
    configure_deterministic_world,
    release_data_collection_world,
    run_condition,
    update_aggregate_summary,
    write_run_outputs,
)


def fake_world(map_name: str = "Carla/Maps/Town01") -> Mock:
    world = Mock()
    world.get_map.return_value.name = map_name
    world.get_settings.return_value = SimpleNamespace(
        synchronous_mode=False, fixed_delta_seconds=None, no_rendering_mode=False
    )
    return world


class DataCollectionTests(unittest.TestCase):
    def test_rendering_is_disabled_on_current_or_new_map(self) -> None:
        for current_map in ("Town01", "Town10HD_Opt"):
            with self.subTest(current_map=current_map):
                client = Mock()
                client.get_world.return_value = fake_world(current_map)
                client.load_world.return_value = fake_world()
                world = configure_deterministic_world(client, "Town01", 0.05)
                settings = world.get_settings.return_value
                self.assertTrue(settings.no_rendering_mode)
                self.assertTrue(settings.synchronous_mode)
                self.assertEqual(settings.fixed_delta_seconds, 0.05)
                world.apply_settings.assert_called_once_with(settings)
                world.tick.assert_called_once()
                if current_map == "Town01":
                    client.load_world.assert_not_called()
                else:
                    client.load_world.assert_called_once_with("Town01")

    def test_cleanup_releases_stepping_without_restarting_graphics(self) -> None:
        world = fake_world()
        settings = world.get_settings.return_value
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = 0.05
        settings.no_rendering_mode = True
        release_data_collection_world(world)
        self.assertFalse(settings.synchronous_mode)
        self.assertIsNone(settings.fixed_delta_seconds)
        self.assertTrue(settings.no_rendering_mode)
        world.apply_settings.assert_called_once_with(settings)

    def test_failed_run_keeps_rendering_disabled(self) -> None:
        client = Mock()
        world = fake_world()
        client.get_world.return_value = world
        with patch("run_carla_lead_braking.choose_spawn_pair", side_effect=RuntimeError("No spawn pair")):
            with self.assertRaisesRegex(RuntimeError, "No spawn pair"):
                run_condition(
                    carla=Mock(), client=client,
                    config={"lead_braking": {"initial_gap_m": 28},
                            "fixed_delta_seconds": 0.05, "experiment_map": "Town01"},
                    pipeline=Mock(), condition="xmai_explanation", seed=0,
                )
        settings = world.get_settings.return_value
        self.assertFalse(settings.synchronous_mode)
        self.assertIsNone(settings.fixed_delta_seconds)
        self.assertTrue(settings.no_rendering_mode)

    def test_separate_output_directory_contains_only_this_sessions_data(self) -> None:
        summary = {"condition": "xmai_explanation", "seed": 0,
                   "data_source": "carla_actor_state", "no_rendering_mode": True}
        ticks = [{"decision_step": 0, "ego_speed_mps": 10.0}]
        evidence = [{"run_id": "test-run", "selected_action": "CONTINUE"}]
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / "separate session"
            write_run_outputs(summary, ticks, evidence, session)
            aggregate = update_aggregate_summary("test-version", session)
            self.assertEqual(json.loads(aggregate.read_text())["conditions"], [summary])
            with (session / "ticks_xmai_explanation_seed0.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            self.assertEqual(float(rows[0]["ego_speed_mps"]), 10.0)
            self.assertEqual(
                json.loads((session / "evidence_xmai_explanation_seed0.jsonl").read_text()),
                evidence[0],
            )
            self.assertEqual({p.suffix for p in session.iterdir()}, {".csv", ".json", ".jsonl"})


if __name__ == "__main__":
    unittest.main()
