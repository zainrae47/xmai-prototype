from __future__ import annotations

import unittest
from dataclasses import dataclass

from xmai.carla_adapter import capture_scene_state


@dataclass
class Vector:
    x: float
    y: float
    z: float = 0.0


@dataclass
class Transform:
    location: Vector

    def get_forward_vector(self) -> Vector:
        return Vector(1.0, 0.0)

    def get_right_vector(self) -> Vector:
        return Vector(0.0, 1.0)


class Actor:
    def __init__(self, actor_id: int, type_id: str, location: Vector, speed_mps: float) -> None:
        self.id = actor_id
        self.type_id = type_id
        self._transform = Transform(location)
        self._velocity = Vector(speed_mps, 0.0)

    def get_transform(self) -> Transform:
        return self._transform

    def get_location(self) -> Vector:
        return self._transform.location

    def get_velocity(self) -> Vector:
        return self._velocity


@dataclass
class Waypoint:
    is_junction: bool
    road_id: int


class Map:
    def get_waypoint(self, location: Vector) -> Waypoint:
        return Waypoint(is_junction=location.x >= 30.0, road_id=int(location.y) + 1)


class World:
    def __init__(self, actors: list[Actor]) -> None:
        self._actors = actors
        self._map = Map()

    def get_actors(self) -> list[Actor]:
        return self._actors

    def get_map(self) -> Map:
        return self._map


class CarlaAdapterTests(unittest.TestCase):
    def test_nearest_actor_in_front_is_selected(self) -> None:
        ego = Actor(1, "vehicle.ego", Vector(0.0, 0.0), 10.0)
        ahead = Actor(2, "vehicle.other", Vector(20.0, 1.0), 5.0)
        farther = Actor(3, "walker.pedestrian.0001", Vector(30.0, 0.5), 1.0)
        behind = Actor(4, "vehicle.other", Vector(-5.0, 0.0), 5.0)
        world = World([ego, ahead, farther, behind])

        state = capture_scene_state(
            world=world,
            ego_vehicle=ego,
            scenario_id="lead_vehicle_braking",
            seed=1,
            timestamp_s=0.0,
        )

        self.assertEqual(state.relevant_actor.actor_id, "2")
        self.assertEqual(state.relevant_actor.actor_type, "vehicle")
        self.assertTrue(state.relevant_actor.in_ego_path)
        self.assertAlmostEqual(state.ego_speed_mps, 10.0)

    def test_actor_outside_lane_is_not_marked_in_path(self) -> None:
        ego = Actor(1, "vehicle.ego", Vector(0.0, 0.0), 8.0)
        actor = Actor(2, "walker.pedestrian.0001", Vector(15.0, 4.0), 1.0)
        world = World([ego, actor])

        state = capture_scene_state(
            world=world,
            ego_vehicle=ego,
            scenario_id="pedestrian_crossing",
            seed=2,
            timestamp_s=0.0,
        )

        self.assertEqual(state.relevant_actor.actor_type, "pedestrian")
        self.assertFalse(state.relevant_actor.in_ego_path)


if __name__ == "__main__":
    unittest.main()

