from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ptu.config import load_config
from ptu.controller import PTUController
from ptu.diagnostics import format_network_status
from ptu.discovery import summarize_discovery
from ptu.exceptions import PTUError


def main() -> int:
    config = load_config()
    controller = PTUController(config)

    try:
        network_status = controller.get_network_status()
        device_info = controller.get_device_info()
        discovery = controller.discover_control_api()
    except PTUError as exc:
        print(f"[ERROR] {exc}")
        return 1

    print("[CONFIG]")
    print(json.dumps(asdict(config), ensure_ascii=False, indent=2))
    print("[NETWORK]")
    print(format_network_status(network_status))
    print("[DEVICE]")
    print(json.dumps(asdict(device_info), ensure_ascii=False, indent=2))
    print("[DISCOVERY]")
    print(summarize_discovery(discovery))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
