import argparse
import sys
from pathlib import Path

from ild_loader import (
    DEFAULT_ILD_PATH,
    OUTPUT_DIR as ILDA_OUTPUT_DIR,
    build_summary_payload,
    load_ild_file,
    print_summary,
    render_frame_preview,
    save_preview_image,
    save_summary_json,
    show_preview,
    validate_input_path,
)
from ild_to_execution_queue import (
    convert_ild_frame_to_execution_payload,
    select_frame,
)
from projection_executor_player import (
    play_steps,
    print_completion_summary,
    print_queue_summary,
)
from projection_executor_stub import (
    OUTPUT_DIR as EXECUTION_OUTPUT_DIR,
    ensure_output_dir,
    print_execution_summary,
    save_execution_json_with_prefix,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the ILDA laser pipeline: parse, preview, queue, and optional playback."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_ILD_PATH),
        help=f"Path to the ILD file. Default: {DEFAULT_ILD_PATH}",
    )
    parser.add_argument(
        "--frame-index",
        type=int,
        default=1,
        help="1-based frame index to export. Default: 1",
    )
    parser.add_argument(
        "--point-step-ms",
        type=int,
        default=12,
        help="Duration per visible laser point in milliseconds. Default: 12",
    )
    parser.add_argument(
        "--blank-step-ms",
        type=int,
        default=4,
        help="Duration per blanked move in milliseconds. Default: 4",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="How many times to repeat the ILDA frame. Default: 1",
    )
    parser.add_argument(
        "--laser-power",
        type=float,
        default=1.0,
        help="Normalized laser power value in range [0.0, 1.0]. Default: 1.0",
    )
    parser.add_argument(
        "--device-name",
        default="laser_projector_ilda_stub",
        help="Execution device name written into the queue metadata",
    )
    parser.add_argument(
        "--play",
        action="store_true",
        help="Immediately play the generated execution queue in terminal",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="When used with --play, print steps without waiting full duration",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Playback speed multiplier when used with --play. Default: 1.0",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        help="Optional maximum number of steps to play",
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="Do not open the ILDA preview window",
    )
    return parser.parse_args()


def save_ilda_outputs(input_path, frames):
    output_dir = ensure_output_dir(ILDA_OUTPUT_DIR)
    summary = build_summary_payload(input_path, frames)
    print_summary(summary)
    save_summary_json(summary, input_path, output_dir)

    preview = render_frame_preview(frames[0], summary)
    save_preview_image(preview, input_path, output_dir)
    return summary, preview


def export_execution_queue(input_path, frame, args):
    output_dir = ensure_output_dir(EXECUTION_OUTPUT_DIR)
    payload = convert_ild_frame_to_execution_payload(
        input_path=input_path,
        frame=frame,
        frame_index=args.frame_index,
        point_step_ms=args.point_step_ms,
        blank_step_ms=args.blank_step_ms,
        repeat=args.repeat,
        laser_power=args.laser_power,
        device_name=args.device_name,
    )
    print_execution_summary(payload)
    prefix = Path(args.input).stem + "_ilda"
    save_execution_json_with_prefix(payload, prefix, output_dir)
    return payload


def get_steps_to_play(payload, max_steps):
    if max_steps is None:
        return payload["steps"]
    return payload["steps"][:max_steps]


def play_execution_payload(payload, args):
    steps = get_steps_to_play(payload, args.max_steps)
    if not steps:
        raise RuntimeError("执行队列为空，无法播放")

    print_queue_summary(payload, steps, args.speed, args.dry_run)
    played_duration_ms, elapsed_wall_time_ms = play_steps(
        steps=steps,
        speed=args.speed,
        dry_run=args.dry_run,
    )
    print_completion_summary(played_duration_ms, elapsed_wall_time_ms, args.dry_run)


def main():
    args = parse_args()

    try:
        input_path = validate_input_path(args.input)
        frames = load_ild_file(input_path)
        summary, preview = save_ilda_outputs(input_path, frames)
        _ = summary

        frame = select_frame(frames, args.frame_index)
        execution_payload = export_execution_queue(input_path, frame, args)

        if args.play:
            play_execution_payload(execution_payload, args)

        if not args.no_window:
            show_preview(preview, input_path, ensure_output_dir(ILDA_OUTPUT_DIR))

    except KeyboardInterrupt:
        print("\n[INFO] Pipeline 已被手动中断。")
        sys.exit(130)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
