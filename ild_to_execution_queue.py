import argparse
import sys
from pathlib import Path

from ild_loader import DEFAULT_ILD_PATH, load_ild_file, validate_input_path
from projection_executor_stub import (
    OUTPUT_DIR as EXECUTION_OUTPUT_DIR,
    ensure_output_dir,
    print_execution_summary,
    save_execution_json_with_prefix,
    validate_args as validate_execution_args,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert an ILDA file into a hardware-agnostic execution queue."
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
    return parser.parse_args()


def validate_args(args):
    if args.frame_index <= 0:
        raise ValueError("frame-index 必须大于 0")
    if args.point_step_ms < 0:
        raise ValueError("point-step-ms 不能小于 0")
    if args.blank_step_ms < 0:
        raise ValueError("blank-step-ms 不能小于 0")

    class ExecutionArgs:
        pass

    execution_args = ExecutionArgs()
    execution_args.dwell_ms = args.point_step_ms
    execution_args.travel_ms = args.blank_step_ms
    execution_args.settle_ms = 0
    execution_args.repeat = args.repeat
    execution_args.laser_power = args.laser_power
    execution_args.device_name = args.device_name
    validate_execution_args(execution_args)


def select_frame(frames, frame_index):
    if frame_index > len(frames):
        raise RuntimeError(
            f"请求的 frame-index={frame_index} 超出范围，当前文件共有 {len(frames)} 帧"
        )
    return frames[frame_index - 1]


def build_step(record, step_index, cycle_index, args):
    action = "laser_off" if record["blanked"] else "laser_on"
    duration_ms = args.blank_step_ms if record["blanked"] else args.point_step_ms

    step = {
        "step_index": step_index,
        "cycle_index": cycle_index,
        "action": action,
        "target_label": f"ilda_point_{step_index}",
        "marker_id": -1,
        "point_type": "ilda_point",
        "wall_mm": {
            "x": record["x"],
            "y": record["y"],
        },
        "duration_ms": duration_ms,
        "source": {
            "color_index": record["color_index"],
            "blanked": record["blanked"],
            "last_point": record["last_point"],
            "z": record["z"],
        },
    }

    if not record["blanked"]:
        step["laser_power"] = args.laser_power

    return step


def build_execution_payload(input_path, frame, args):
    steps = []
    step_index = 1

    for cycle_index in range(1, args.repeat + 1):
        for record in frame["records"]:
            steps.append(build_step(record, step_index, cycle_index, args))
            step_index += 1

    total_duration_ms = sum(step["duration_ms"] for step in steps)
    header = frame["header"]

    return {
        "device_name": args.device_name,
        "source_projection_targets": str(input_path),
        "reference_marker": {
            "id": None,
            "marker_size_mm": None,
            "origin_mode": "ilda_native",
        },
        "coordinate_system": {
            "unit": "ilda_units",
            "x_axis": "right",
            "y_axis": "up",
            "plane": "ilda_frame_native",
        },
        "queue_config": {
            "repeat": args.repeat,
            "dwell_ms": args.point_step_ms,
            "travel_ms": args.blank_step_ms,
            "settle_ms": 0,
            "laser_power": args.laser_power,
        },
        "target_count": len(frame["records"]),
        "step_count": len(steps),
        "estimated_total_duration_ms": total_duration_ms,
        "ild_source": {
            "input_file": str(input_path),
            "frame_index": args.frame_index,
            "frame_name": header["frame_name"],
            "company_name": header["company_name"],
            "format_code": header["format_code"],
            "projector_number": header["projector_number"],
        },
        "steps": steps,
    }


def convert_ild_frame_to_execution_payload(
    input_path,
    frame,
    frame_index=1,
    point_step_ms=12,
    blank_step_ms=4,
    repeat=1,
    laser_power=1.0,
    device_name="laser_projector_ilda_stub",
):
    class Args:
        pass

    args = Args()
    args.input = str(input_path)
    args.frame_index = frame_index
    args.point_step_ms = point_step_ms
    args.blank_step_ms = blank_step_ms
    args.repeat = repeat
    args.laser_power = laser_power
    args.device_name = device_name
    validate_args(args)
    return build_execution_payload(input_path, frame, args)


def main():
    args = parse_args()

    try:
        validate_args(args)
        input_path = validate_input_path(args.input)
        frames = load_ild_file(input_path)
        frame = select_frame(frames, args.frame_index)

        payload = build_execution_payload(input_path, frame, args)
        print_execution_summary(payload)

        output_dir = ensure_output_dir(EXECUTION_OUTPUT_DIR)
        prefix = Path(args.input).stem + "_ilda"
        save_execution_json_with_prefix(payload, prefix, output_dir)

    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
