from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "carla.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cache_path = ROOT / config["cache_directory"]
    cache_path.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("CARLA_CACHE_DIR", str(cache_path))

    import carla

    client = carla.Client(config["host"], int(config["rpc_port"]))
    client.set_timeout(args.timeout)
    try:
        world = client.get_world()
    except RuntimeError as error:
        print(f"CARLA is not ready: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    result = {
        "client_version": client.get_client_version(),
        "server_version": client.get_server_version(),
        "map": world.get_map().name,
        "actors": len(world.get_actors()),
        "status": "connected",
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
