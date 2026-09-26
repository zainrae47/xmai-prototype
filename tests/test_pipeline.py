from __future__ import annotations

import unittest

from xmai.models import Action, ActorState, SceneState, Thresholds
from xmai.pipeline import CONDITIONS, XMAIPipeline
from xmai.scenarios import SCENARIO_IDS, create_scene


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = XMAIPipeline(Thresholds())

    def test_scripted_scenes_are_reproducible(self) -> None:
        for scenario_id in SCENARIO_IDS:
            self.assertEqual(create_scene(scenario_id, 7), create_scene(scenario_id, 7))

    def test_matched_conditions_choose_same_action(self) -> None:
        for scenario_id in SCENARIO_IDS:
            for seed in range(10):
                scene = create_scene(scenario_id, seed)
                actions = {self.pipeline.run(scene, condition).selected_action for condition in CONDITIONS}
                self.assertEqual(len(actions), 1)

    def test_high_risk_pedestrian_produces_stop(self) -> None:
        scene = SceneState(
            scenario_id="pedestrian_crossing",
            seed=99,
            timestamp_s=0.0,
            ego_speed_mps=8.0,
            relevant_actor=ActorState(
                actor_id="pedestrian",
                actor_type="pedestrian",
                distance_m=8.0,
                speed_mps=1.0,
                in_ego_path=True,
                visibility="partial",
            ),
        )
        result = self.pipeline.run(scene, "xmai_explanation")
        self.assertEqual(result.selected_action, Action.CONTROLLED_STOP)
        self.assertTrue(result.explanation_valid)

    def test_distant_low_risk_actor_can_produce_continue(self) -> None:
        scene = SceneState(
            scenario_id="lead_vehicle_braking",
            seed=100,
            timestamp_s=0.0,
            ego_speed_mps=10.0,
            relevant_actor=ActorState(
                actor_id="lead-vehicle",
                actor_type="vehicle",
                distance_m=50.0,
                speed_mps=8.0,
                in_ego_path=True,
                visibility="clear",
            ),
        )
        result = self.pipeline.run(scene, "xmai_explanation")
        self.assertEqual(result.selected_action, Action.CONTINUE)
        self.assertTrue(result.explanation_valid)

    def test_explanations_only_cite_known_evidence(self) -> None:
        scene = create_scene("junction_conflict", 3)
        result = self.pipeline.run(scene, "xmai_explanation")
        self.assertTrue(result.explanation_valid)
        self.assertEqual(result.validation_errors, [])
        known = {
            evidence_id
            for event in result.trace
            if event["agent"] != "explainability"
            for evidence_id in event["output_evidence_ids"]
        }
        cited = {
            evidence_id
            for mapping in result.explanation["claim_evidence_map"]
            for evidence_id in mapping["evidence_ids"]
        }
        self.assertTrue(cited <= known)

    def test_trace_lengths_match_conditions(self) -> None:
        scene = create_scene("lead_vehicle_braking", 0)
        self.assertEqual(len(self.pipeline.run(scene, "centralised_baseline").trace), 1)
        self.assertEqual(len(self.pipeline.run(scene, "xmai_decision").trace), 4)
        self.assertEqual(len(self.pipeline.run(scene, "xmai_explanation").trace), 5)

    def test_decision_step_makes_live_evidence_ids_unique(self) -> None:
        first = create_scene("lead_vehicle_braking", 0)
        second = SceneState(
            scenario_id=first.scenario_id,
            seed=first.seed,
            timestamp_s=0.05,
            ego_speed_mps=first.ego_speed_mps,
            relevant_actor=first.relevant_actor,
            junction_conflict=first.junction_conflict,
            decision_step=1,
        )
        first_result = self.pipeline.run(first, "xmai_explanation")
        second_result = self.pipeline.run(second, "xmai_explanation")
        self.assertNotEqual(first_result.run_id, second_result.run_id)
        first_ids = {
            evidence_id
            for event in first_result.trace
            for evidence_id in event["output_evidence_ids"]
        }
        second_ids = {
            evidence_id
            for event in second_result.trace
            for evidence_id in event["output_evidence_ids"]
        }
        self.assertTrue(first_ids.isdisjoint(second_ids))


if __name__ == "__main__":
    unittest.main()
