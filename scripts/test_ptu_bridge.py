from __future__ import annotations

from pathlib import Path
import json
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.ptu_adapter import PTUAdapter, PTUAdapterError


def main() -> int:
    adapter = PTUAdapter()
    try:
        print("[INFO] Testing PTU bridge adapter")
        print(json.dumps(adapter.connect(), ensure_ascii=False, indent=2))
        print("[INFO] PTU status")
        print(json.dumps(adapter.get_status(), ensure_ascii=False, indent=2))
        print("[INFO] Dry-run pan step")
        print(json.dumps(adapter.pan_step(10, execute=False), ensure_ascii=False, indent=2))
        print("[INFO] Dry-run tilt step")
        print(json.dumps(adapter.tilt_step(10, execute=False), ensure_ascii=False, indent=2))
        print("[INFO] Dry-run halt")
        print(json.dumps(adapter.halt(execute=False), ensure_ascii=False, indent=2))
        return 0
    except PTUAdapterError as exc:
        print(f"[ERROR] PTU bridge test failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
