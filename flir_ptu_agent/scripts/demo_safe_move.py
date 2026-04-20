from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ptu.config import load_config
from ptu.controller import PTUController
from ptu.exceptions import PTUControlNotImplementedError, PTUError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe PTU movement demo.")
    parser.add_argument("--axis", choices=["pan", "tilt"], default="pan")
    parser.add_argument("--step", type=int, default=10, help="Small movement step.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually send the request. Without this flag the script stays in dry-run mode.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config()
    controller = PTUController(config)

    try:
        if args.axis == "pan":
            result = controller.safe_pan_step(step=args.step, dry_run=not args.execute)
        else:
            result = controller.safe_tilt_step(step=args.step, dry_run=not args.execute)
    except PTUControlNotImplementedError as exc:
        print("[WARN] 未实现真实运动控制，因为未确认 HTTP endpoint。")
        print(f"[WARN] {exc}")
        return 2
    except PTUError as exc:
        print(f"[ERROR] {exc}")
        return 1

    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
