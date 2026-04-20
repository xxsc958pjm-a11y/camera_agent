from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ptu.config import get_artifacts_dir, load_config
from ptu.controller import PTUController
from ptu.exceptions import PTUError
from ptu.models import PTUCalibrationStepResult, PTUCalibrationSummary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calibrate PTU step responses with safe small moves.")
    parser.add_argument("--config", help="Optional config path.")
    parser.add_argument("--axis", choices=["pan", "tilt"], required=True)
    parser.add_argument(
        "--steps",
        required=True,
        help="Comma-separated list of requested steps, for example: 5,10,20,30",
    )
    parser.add_argument(
        "--pause-sec",
        type=float,
        default=0.35,
        help="Pause after motion before halt. Default: 0.35",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually send PTU move commands. Without this flag the script stays in dry-run mode.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    steps = parse_steps(args.steps)
    config = load_config(args.config)
    controller = PTUController(config)

    summary = PTUCalibrationSummary(
        axis=args.axis,
        dry_run=not args.execute,
        requested_steps=steps,
        notes=[
            "Requested step size does not necessarily match final PP/TP delta one-to-one.",
            "Each execute sample is followed by a halt command.",
        ],
    )

    output_dir = get_artifacts_dir(config) / "calibration"
    output_dir.mkdir(parents=True, exist_ok=True)

    if not args.execute:
        for step in steps:
            result = PTUCalibrationStepResult(
                axis=args.axis,
                requested_step=step,
                success=True,
                dry_run=True,
                response_text=f"Dry run only. Would execute {args.axis} step {step} and then halt.",
            )
            summary.results.append(result)
        json_path, md_path = save_summary(output_dir, summary)
        print(f"[INFO] Dry-run calibration plan saved to {json_path}")
        print(f"[INFO] Markdown summary saved to {md_path}")
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
        return 0

    for step in steps:
        try:
            move_result = (
                controller.safe_pan_step(step=step, dry_run=False)
                if args.axis == "pan"
                else controller.safe_tilt_step(step=step, dry_run=False)
            )
            time.sleep(max(args.pause_sec, 0.0))
            halt_result = controller.halt(dry_run=False)
            result = PTUCalibrationStepResult(
                axis=args.axis,
                requested_step=step,
                success=True,
                dry_run=False,
                before_status=move_result.before_status,
                after_status=move_result.after_status,
                delta_PP=_delta(move_result.before_status, move_result.after_status, "PP"),
                delta_TP=_delta(move_result.before_status, move_result.after_status, "TP"),
                response_text=move_result.response_text,
                halt_response_text=halt_result.response_text,
            )
            summary.results.append(result)
        except PTUError as exc:
            summary.results.append(
                PTUCalibrationStepResult(
                    axis=args.axis,
                    requested_step=step,
                    success=False,
                    dry_run=False,
                    error=str(exc),
                )
            )
            summary.stopped_on_error = True
            summary.notes.append(f"Calibration stopped on step {step} because of an error.")
            break

    json_path, md_path = save_summary(output_dir, summary)
    print(f"[INFO] Calibration JSON saved to {json_path}")
    print(f"[INFO] Calibration Markdown saved to {md_path}")
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    return 0 if not summary.stopped_on_error else 1


def parse_steps(raw: str) -> list[int]:
    values: list[int] = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        try:
            values.append(int(text))
        except ValueError as exc:
            raise SystemExit(f"[ERROR] Invalid step value: {text}") from exc
    if not values:
        raise SystemExit("[ERROR] --steps must contain at least one integer.")
    return values


def save_summary(output_dir: Path, summary: PTUCalibrationSummary) -> tuple[Path, Path]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"{timestamp}_{summary.axis}_calibration.json"
    md_path = output_dir / f"{timestamp}_{summary.axis}_calibration.md"
    latest_json = output_dir / "latest_summary.json"
    latest_md = output_dir / "latest_summary.md"
    json_text = json.dumps(asdict(summary), ensure_ascii=False, indent=2)
    md_text = render_markdown(summary)
    json_path.write_text(json_text, encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    latest_json.write_text(json_text, encoding="utf-8")
    latest_md.write_text(md_text, encoding="utf-8")
    return json_path, md_path


def render_markdown(summary: PTUCalibrationSummary) -> str:
    lines = [
        f"# PTU {summary.axis} calibration summary",
        "",
        f"- dry_run: `{summary.dry_run}`",
        f"- requested_steps: `{summary.requested_steps}`",
        f"- stopped_on_error: `{summary.stopped_on_error}`",
        "",
        "| requested_step | success | delta_PP | delta_TP |",
        "| --- | --- | --- | --- |",
    ]
    for result in summary.results:
        lines.append(
            f"| {result.requested_step} | {result.success} | "
            f"{result.delta_PP if result.delta_PP is not None else '-'} | "
            f"{result.delta_TP if result.delta_TP is not None else '-'} |"
        )
    if summary.notes:
        lines.extend(["", "## Notes", ""])
        for note in summary.notes:
            lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def _delta(before_status: dict[str, object], after_status: dict[str, object], key: str) -> int | None:
    before = _parse_int(before_status.get(key))
    after = _parse_int(after_status.get(key))
    if before is None or after is None:
        return None
    return after - before


def _parse_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
