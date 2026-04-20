from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ptu.config import load_config
from ptu.controller import PTUController
from ptu.exceptions import PTUError


def main() -> int:
    config = load_config()
    controller = PTUController(config)

    try:
        status = controller.get_network_status()
        response = controller.client.fetch_root_page()
        title = controller.get_device_info().page_title
    except PTUError as exc:
        print(f"[ERROR] {exc}")
        return 1

    print(f"base_url = {config.base_url}")
    print(f"port_80_reachable = {status.port_80_reachable}")
    print(f"http_status = {response.status_code}")
    print(f"title = {title}")
    print(f"page_length = {len(response.text)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
