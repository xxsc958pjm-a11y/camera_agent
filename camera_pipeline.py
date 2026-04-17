import argparse
import csv
import sys
import time
import traceback
from pathlib import Path

import cv2

from aruco_detect import (
    OUTPUT_DIR as DETECT_OUTPUT_DIR,
    WINDOW_NAME,
    annotate_detection_result,
    build_detection_payload,
    collect_marker_results,
    detect_markers,
    draw_live_info,
    filter_and_enrich_results,
    format_results_text,
    get_aruco_dictionary,
    get_pose_inputs,
    load_image,
    save_detection_json,
    save_result_image,
    validate_image_path,
)
from aruco_runtime import StableMarkerTracker
from aruco_to_wall_coords import (
    DEFAULT_REFERENCE_MARKER_IDS,
    FIXED_WALL_HEIGHT_MM,
    FIXED_WALL_WIDTH_MM,
    OUTPUT_DIR as WALL_OUTPUT_DIR,
    REPROJECTION_ERROR_THRESHOLD_PX,
    convert_detection_to_wall_payload,
    compute_marker_wall_coords,
    print_wall_results,
    save_wall_json_with_prefix,
)
from camera_source import (
    DEFAULT_CAMERA_INDEX,
    DEFAULT_FRAME_HEIGHT,
    DEFAULT_FRAME_WIDTH,
    get_camera_debug_info,
    open_camera,
    read_bgr_frame,
)
from projection_executor_stub import (
    OUTPUT_DIR as EXECUTION_OUTPUT_DIR,
    convert_projection_to_execution_payload,
    print_execution_summary,
    save_execution_json_with_prefix,
)
from projection_targets import (
    OUTPUT_DIR as PROJECTION_OUTPUT_DIR,
    convert_wall_to_projection_payload,
    print_projection_targets,
    save_projection_json_with_prefix,
)
from wall_map_renderer import WallMapRenderer


DEBUG_WALL_PREFIX = "[DEBUG_WALL]"
STATUS_PREFIX = "[STATUS]"
DEBUG_WALL_INPUT_LOG_LIMIT = 3
DEBUG_WALL_EXCEPTION_LOG_LIMIT = 3
_debug_wall_input_logs = 0
_debug_wall_exception_logs = 0
TRACKING_LOG_DIR = Path("outputs/logs")
EMA_ALPHA = 0.35
MAPPING_STATUS_PRINT_DELTA_PX = 0.25
STATUS_FRAME_INTERVAL = 10


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the full camera pipeline: detection, wall coords, and projection targets."
    )
    parser.add_argument(
        "--image",
        help="Path to the input image, for example: images/test.jpg",
    )
    parser.add_argument(
        "--camera",
        type=int,
        default=DEFAULT_CAMERA_INDEX,
        help="Camera index for real-time pipeline. Default: 8",
    )
    parser.add_argument(
        "--dict",
        default="DICT_4X4_50",
        help="Aruco dictionary name. Default: DICT_4X4_50",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_FRAME_WIDTH,
        help="Camera width for live mode. Default: 640",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=DEFAULT_FRAME_HEIGHT,
        help="Camera height for live mode. Default: 480",
    )
    parser.add_argument(
        "--marker-size-mm",
        type=float,
        required=True,
        help="Physical size of the reference Aruco marker in millimeters",
    )
    parser.add_argument(
        "--origin-marker-id",
        type=int,
        help="Marker ID used as wall origin. Default: first detected stable marker",
    )
    parser.add_argument(
        "--reference-marker-ids",
        nargs="+",
        type=int,
        help=(
            "Optional fixed reference markers for whole-wall mapping. "
            f"Recommended: {' '.join(str(marker_id) for marker_id in DEFAULT_REFERENCE_MARKER_IDS)}"
        ),
    )
    parser.add_argument(
        "--origin",
        choices=["top_left", "center", "bottom_left"],
        default="top_left",
        help="Wall coordinate origin on the reference marker. Default: top_left",
    )
    parser.add_argument(
        "--target-type",
        choices=["centers", "corners", "all"],
        default="centers",
        help="Projection target type. Default: centers",
    )
    parser.add_argument(
        "--target-marker-ids",
        nargs="+",
        type=int,
        help="Optional marker IDs to keep as valid detections and projection targets",
    )
    parser.add_argument(
        "--min-stable-frames",
        type=int,
        default=3,
        help="Frames required before a detection is considered stable. Default: 3",
    )
    parser.add_argument(
        "--stable-center-threshold-px",
        type=float,
        default=30.0,
        help="Max allowed center movement between frames. Default: 30 px",
    )
    parser.add_argument(
        "--label-prefix",
        default="target",
        help="Prefix used when naming projection targets. Default: target",
    )
    parser.add_argument(
        "--export-execution-queue",
        action="store_true",
        help="Also export a projection execution queue JSON",
    )
    parser.add_argument(
        "--show-wall-map",
        action="store_true",
        help="Show real-time wall map visualization in dual-view mode (camera left, wall map right)",
    )
    parser.add_argument(
        "--dwell-ms",
        type=int,
        default=500,
        help="Execution queue dwell time per target in milliseconds. Default: 500",
    )
    parser.add_argument(
        "--travel-ms",
        type=int,
        default=150,
        help="Execution queue travel time between targets in milliseconds. Default: 150",
    )
    parser.add_argument(
        "--settle-ms",
        type=int,
        default=80,
        help="Execution queue settling time after movement in milliseconds. Default: 80",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Execution queue repeat count. Default: 1",
    )
    parser.add_argument(
        "--laser-power",
        type=float,
        default=1.0,
        help="Execution queue normalized laser power in [0.0, 1.0]. Default: 1.0",
    )
    parser.add_argument(
        "--device-name",
        default="laser_projector_stub",
        help="Execution queue device name. Default: laser_projector_stub",
    )
    parser.add_argument(
        "--camera-matrix",
        help="Optional path to camera matrix JSON for future pose estimation",
    )
    parser.add_argument(
        "--dist-coeffs",
        help="Optional path to distortion coefficients JSON for future pose estimation",
    )

    args = parser.parse_args()
    if args.image is not None and "--camera" in sys.argv:
        parser.error("--image 和 --camera 不能同时使用。")
    if args.reference_marker_ids and len(args.reference_marker_ids) < 2:
        parser.error("--reference-marker-ids 至少需要 2 个 marker IDs。")
    return args


def ensure_output_dir(output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def debug_wall_repr(value, limit=240):
    text = repr(value)
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def debug_wall_log(message):
    print(f"{DEBUG_WALL_PREFIX} {message}")


def status_log(message):
    print(f"{STATUS_PREFIX} {message}")


def extract_xy(point):
    if point is None:
        return None
    if isinstance(point, dict):
        x_value = point.get("x")
        y_value = point.get("y")
    elif isinstance(point, (tuple, list)) and len(point) >= 2:
        x_value, y_value = point[0], point[1]
    else:
        return None

    if x_value is None or y_value is None:
        return None

    return float(x_value), float(y_value)


def log_wall_map_input(reference_info, stable_results, wall_markers):
    global _debug_wall_input_logs

    if _debug_wall_input_logs >= DEBUG_WALL_INPUT_LOG_LIMIT:
        return

    stable_ids = [result.get("id") for result in stable_results]
    debug_wall_log(
        "camera_pipeline input "
        f"ref={reference_info} "
        f"stable_ids={stable_ids} "
        f"markers_type={type(wall_markers)} "
        f"marker_count={len(wall_markers)}"
    )

    for marker in list(wall_markers)[:2]:
        marker_id = marker.get("id")
        wall_mm = marker.get("wall_mm")
        center = wall_mm.get("center") if isinstance(wall_mm, dict) else wall_mm
        corners = wall_mm.get("corners") if isinstance(wall_mm, dict) else None

        debug_wall_log(
            f"marker {marker_id} wall_mm type={type(wall_mm)} "
            f"value={debug_wall_repr(wall_mm)}"
        )
        debug_wall_log(
            f"marker {marker_id} center type={type(center)} "
            f"value={debug_wall_repr(center)}"
        )

        if isinstance(corners, (list, tuple)) and corners:
            debug_wall_log(
                f"marker {marker_id} corners type={type(corners)} len={len(corners)}"
            )
            debug_wall_log(
                f"marker {marker_id} corner0 type={type(corners[0])} "
                f"value={debug_wall_repr(corners[0])}"
            )
        else:
            debug_wall_log(
                f"marker {marker_id} corners type={type(corners)} "
                f"value={debug_wall_repr(corners)}"
            )

    _debug_wall_input_logs += 1


def log_wall_map_exception(stage, object_name, obj):
    global _debug_wall_exception_logs

    if _debug_wall_exception_logs >= DEBUG_WALL_EXCEPTION_LOG_LIMIT:
        return

    debug_wall_log(f"exception stage={stage}")
    debug_wall_log(f"exception object={object_name}")
    debug_wall_log(f"exception object type={type(obj)}")
    debug_wall_log(f"exception object repr={debug_wall_repr(obj, limit=400)}")
    traceback_tail = traceback.format_exc().splitlines()[-6:]
    for line in traceback_tail:
        debug_wall_log(f"traceback {line}")

    _debug_wall_exception_logs += 1


def get_stable_reference_markers(results, reference_marker_ids):
    markers_by_id = {result.get("id"): result for result in results}
    found_markers = [
        markers_by_id[marker_id]
        for marker_id in reference_marker_ids
        if marker_id in markers_by_id
    ]
    missing_ids = [
        marker_id
        for marker_id in reference_marker_ids
        if marker_id not in markers_by_id
    ]
    return found_markers, missing_ids


def compose_dual_view(camera_frame, wall_frame):
    target_height = min(camera_frame.shape[0], wall_frame.shape[0])
    camera_resized = cv2.resize(
        camera_frame,
        (
            int(camera_frame.shape[1] * target_height / camera_frame.shape[0]),
            target_height,
        ),
    )
    wall_resized = cv2.resize(
        wall_frame,
        (
            int(wall_frame.shape[1] * target_height / wall_frame.shape[0]),
            target_height,
        ),
    )
    return cv2.hconcat([camera_resized, wall_resized])


def build_wall_status_panel(wall_map_renderer, messages):
    if isinstance(messages, str):
        messages = [messages]

    panel = wall_map_renderer.create_canvas()
    wall_map_renderer.draw_wall_outline(panel)
    wall_map_renderer.draw_grid(panel, step_mm=200)
    wall_map_renderer.draw_axes(panel)

    y = 80
    for message in messages:
        cv2.putText(
            panel,
            message,
            (30, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
        y += 40

    return panel


def get_wall_center(marker, prefer_filtered=True):
    if prefer_filtered:
        filtered_center = extract_xy(marker.get("filtered_wall_center_mm"))
        if filtered_center is not None:
            return filtered_center

    raw_center = extract_xy(marker.get("raw_wall_center_mm"))
    if raw_center is not None:
        return raw_center

    wall_mm = marker.get("wall_mm")
    if isinstance(wall_mm, dict):
        center = extract_xy(wall_mm.get("center"))
        if center is not None:
            return center
    return extract_xy(wall_mm)


def apply_wall_center_filter(
    wall_markers,
    filter_state,
    reference_marker_ids=None,
    alpha=EMA_ALPHA,
):
    reference_marker_ids = set(reference_marker_ids or [])
    filtered_markers = []

    for marker in wall_markers:
        marker_copy = dict(marker)
        wall_mm = marker_copy.get("wall_mm")
        if isinstance(wall_mm, dict):
            marker_copy["wall_mm"] = dict(wall_mm)

        raw_center = get_wall_center(marker_copy, prefer_filtered=False)
        if raw_center is not None:
            marker_copy["raw_wall_center_mm"] = {
                "x": round(float(raw_center[0]), 3),
                "y": round(float(raw_center[1]), 3),
            }

        marker_id = marker_copy.get("id")
        if raw_center is None:
            filtered_markers.append(marker_copy)
            continue

        if marker_id in reference_marker_ids:
            marker_copy["filtered_wall_center_mm"] = {
                "x": round(float(raw_center[0]), 3),
                "y": round(float(raw_center[1]), 3),
            }
            filtered_markers.append(marker_copy)
            continue

        previous_center = filter_state.get(marker_id)
        if previous_center is None:
            filtered_center = raw_center
        else:
            filtered_center = (
                alpha * raw_center[0] + (1.0 - alpha) * previous_center[0],
                alpha * raw_center[1] + (1.0 - alpha) * previous_center[1],
            )

        filter_state[marker_id] = filtered_center
        marker_copy["filtered_wall_center_mm"] = {
            "x": round(float(filtered_center[0]), 3),
            "y": round(float(filtered_center[1]), 3),
        }
        filtered_markers.append(marker_copy)

    return filtered_markers


def build_mapping_info(
    reference_ids_detected,
    mapping_valid,
    reprojection_error_px=None,
):
    return {
        "reference_ids_detected": [int(marker_id) for marker_id in reference_ids_detected],
        "mapping_valid": bool(mapping_valid),
        "reprojection_error_px": (
            round(float(reprojection_error_px), 3)
            if reprojection_error_px is not None
            else None
        ),
        "reprojection_error_threshold_px": REPROJECTION_ERROR_THRESHOLD_PX,
    }


def should_print_mapping_status(previous_status, current_status):
    if previous_status is None:
        return True

    if previous_status["reference_ids_detected"] != current_status["reference_ids_detected"]:
        return True

    if previous_status["mapping_valid"] != current_status["mapping_valid"]:
        return True

    previous_error = previous_status["reprojection_error_px"]
    current_error = current_status["reprojection_error_px"]
    if previous_error is None or current_error is None:
        return previous_error != current_error

    return abs(previous_error - current_error) >= MAPPING_STATUS_PRINT_DELTA_PX


def print_mapping_status(current_status):
    status_log(
        f"reference_ids_detected = {current_status['reference_ids_detected']}"
    )
    status_log(f"mapping_valid = {current_status['mapping_valid']}")
    if current_status["reprojection_error_px"] is not None:
        status_log(
            f"reprojection_error_px = {current_status['reprojection_error_px']:.2f}"
        )


def should_print_status_block(
    previous_status,
    current_status,
    frame_count,
    last_status_frame,
):
    if should_print_mapping_status(previous_status, current_status):
        return True
    if last_status_frame is None:
        return True
    return (frame_count - last_status_frame) >= STATUS_FRAME_INTERVAL


def print_marker_statuses(wall_markers, reference_marker_ids=None):
    active_reference_ids = set(reference_marker_ids or [])
    for marker in wall_markers:
        marker_id = marker.get("id")
        if marker_id in active_reference_ids:
            continue

        raw_center = get_wall_center(marker, prefer_filtered=False)
        filtered_center = get_wall_center(marker, prefer_filtered=True)

        if raw_center is not None:
            status_log(
                f"marker {marker_id} raw_wall_center_mm = "
                f"({raw_center[0]:.1f}, {raw_center[1]:.1f})"
            )

        if filtered_center is not None:
            status_log(
                f"marker {marker_id} filtered_wall_center_mm = "
                f"({filtered_center[0]:.1f}, {filtered_center[1]:.1f})"
            )


class WallTrackingCsvLogger:
    FIELDNAMES = [
        "timestamp",
        "frame_idx",
        "mapping_valid",
        "reprojection_error_px",
        "reference_ids_detected",
        "marker_id",
        "marker_type",
        "center_px_x",
        "center_px_y",
        "raw_wall_x_mm",
        "raw_wall_y_mm",
        "filtered_wall_x_mm",
        "filtered_wall_y_mm",
    ]

    def __init__(self, output_dir=TRACKING_LOG_DIR):
        ensure_output_dir(output_dir)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.output_path = output_dir / f"wall_tracking_{timestamp}.csv"
        self._file = self.output_path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self.FIELDNAMES)
        self._writer.writeheader()
        self._file.flush()

    def log_frame(
        self,
        frame_idx,
        stable_results,
        wall_markers,
        mapping_info,
        reference_marker_ids=None,
    ):
        if not stable_results:
            return

        wall_markers_by_id = {marker["id"]: marker for marker in wall_markers}
        reference_marker_ids = set(reference_marker_ids or [])
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        reference_ids_detected = ",".join(
            str(marker_id) for marker_id in mapping_info.get("reference_ids_detected", [])
        )

        for result in stable_results:
            marker_id = result["id"]
            wall_marker = wall_markers_by_id.get(marker_id)
            raw_center = get_wall_center(wall_marker, prefer_filtered=False) if wall_marker else None
            filtered_center = get_wall_center(wall_marker, prefer_filtered=True) if wall_marker else None
            center_px = extract_xy(result.get("center"))

            self._writer.writerow(
                {
                    "timestamp": timestamp,
                    "frame_idx": frame_idx,
                    "mapping_valid": mapping_info.get("mapping_valid", False),
                    "reprojection_error_px": mapping_info.get("reprojection_error_px"),
                    "reference_ids_detected": reference_ids_detected,
                    "marker_id": marker_id,
                    "marker_type": (
                        "reference" if marker_id in reference_marker_ids else "tracked"
                    ),
                    "center_px_x": center_px[0] if center_px else None,
                    "center_px_y": center_px[1] if center_px else None,
                    "raw_wall_x_mm": raw_center[0] if raw_center else None,
                    "raw_wall_y_mm": raw_center[1] if raw_center else None,
                    "filtered_wall_x_mm": filtered_center[0] if filtered_center else None,
                    "filtered_wall_y_mm": filtered_center[1] if filtered_center else None,
                }
            )

        self._file.flush()

    def close(self):
        self._file.close()


def export_pipeline_outputs(detection_payload, image_to_save, prefix, args):
    detect_output_dir = ensure_output_dir(DETECT_OUTPUT_DIR)
    wall_output_dir = ensure_output_dir(WALL_OUTPUT_DIR)
    projection_output_dir = ensure_output_dir(PROJECTION_OUTPUT_DIR)
    execution_output_dir = ensure_output_dir(EXECUTION_OUTPUT_DIR)

    save_result_image(image_to_save, prefix, detect_output_dir)
    save_detection_json(detection_payload, prefix, detect_output_dir)

    wall_payload = convert_detection_to_wall_payload(
        detection_payload=detection_payload,
        marker_size_mm=args.marker_size_mm,
        origin_marker_id=args.origin_marker_id,
        origin_mode=args.origin,
        reference_marker_ids=args.reference_marker_ids,
    )
    print_wall_results(wall_payload)
    if not wall_payload.get("mapping_valid", True):
        print("[WARN] 当前帧映射无效，已跳过 wall_coords / projection_targets 导出。")
        return
    save_wall_json_with_prefix(wall_payload, prefix, wall_output_dir)

    projection_payload = convert_wall_to_projection_payload(
        wall_payload=wall_payload,
        target_type=args.target_type,
        marker_ids=args.target_marker_ids,
        label_prefix=args.label_prefix,
    )
    print_projection_targets(projection_payload)
    save_projection_json_with_prefix(projection_payload, prefix, projection_output_dir)

    if args.export_execution_queue:
        execution_payload = convert_projection_to_execution_payload(
            projection_payload=projection_payload,
            dwell_ms=args.dwell_ms,
            travel_ms=args.travel_ms,
            settle_ms=args.settle_ms,
            repeat=args.repeat,
            laser_power=args.laser_power,
            device_name=args.device_name,
        )
        print_execution_summary(execution_payload)
        save_execution_json_with_prefix(
            execution_payload, prefix, execution_output_dir
        )


def run_image_mode(args):
    image_path = validate_image_path(args.image)
    dictionary = get_aruco_dictionary(args.dict)
    pose_inputs = get_pose_inputs(args)
    image = load_image(image_path)

    corners, ids, _ = detect_markers(image, dictionary)
    raw_results = collect_marker_results(corners, ids)
    effective_results = filter_and_enrich_results(raw_results, args, pose_inputs)
    annotated_image = annotate_detection_result(image, effective_results)
    detection_payload = build_detection_payload(
        results=effective_results,
        frame_shape=image.shape,
        dictionary_name=args.dict,
        source=str(image_path),
        marker_size_mm=args.marker_size_mm,
        camera_matrix=pose_inputs["camera_matrix"],
        dist_coeffs=pose_inputs["dist_coeffs"],
    )

    print(format_results_text(effective_results))
    export_pipeline_outputs(
        detection_payload=detection_payload,
        image_to_save=annotated_image,
        prefix=image_path.stem,
        args=args,
    )

    print("[INFO] 按 'q' 退出窗口，按 's' 重新保存当前结果。")
    while True:
        cv2.imshow(WINDOW_NAME, annotated_image)
        key = cv2.waitKey(0) & 0xFF

        if key == ord("q"):
            print("[INFO] Quit.")
            break
        if key == ord("s"):
            export_pipeline_outputs(
                detection_payload=detection_payload,
                image_to_save=annotated_image,
                prefix=image_path.stem,
                args=args,
            )

    cv2.destroyAllWindows()


def run_camera_mode(args):
    dictionary = get_aruco_dictionary(args.dict)
    pose_inputs = get_pose_inputs(args)

    cap = open_camera(
        camera_index=args.camera,
        width=args.width,
        height=args.height,
    )
    debug_info = get_camera_debug_info(cap, args.camera)
    print(f"requested camera index = {debug_info['requested_camera_index']}")
    print(f"cap.isOpened() = {debug_info['opened']}")
    print(f"actual width = {debug_info['actual_width']}")
    print(f"actual height = {debug_info['actual_height']}")
    print(f"actual fourcc = {debug_info['actual_fourcc']}")

    if not cap.isOpened():
        raise RuntimeError(f"无法打开摄像头: {args.camera}")

    tracker = StableMarkerTracker(
        min_stable_frames=args.min_stable_frames,
        max_center_jump_px=args.stable_center_threshold_px,
    )
    reference_marker_ids = args.reference_marker_ids
    if reference_marker_ids:
        reference_marker_ids = [int(marker_id) for marker_id in reference_marker_ids]
    frame_count = 0
    start_time = time.time()
    last_result_text = None
    last_mapping_status = None
    last_status_frame = None
    wall_filter_state = {}
    wall_tracking_logger = None

    # For --show-wall-map mode
    wall_map_renderer = None

    if args.show_wall_map:
        wall_map_renderer = WallMapRenderer(
            wall_width_mm=FIXED_WALL_WIDTH_MM,
            wall_height_mm=FIXED_WALL_HEIGHT_MM,
            canvas_width=800,
            canvas_height=600,
        )
        print("[INFO] --show-wall-map 已启用，将显示双视图（左：相机画面，右：墙面坐标图）")

    if reference_marker_ids:
        wall_tracking_logger = WallTrackingCsvLogger()
        status_log(f"csv_log = {wall_tracking_logger.output_path}")

    print("[INFO] 实时 pipeline 已启动，按 'q' 退出，按 's' 导出当前整套结果。")

    try:
        while True:
            ret, frame = read_bgr_frame(cap)
            if not ret or frame is None:
                print("[ERROR] 读取摄像头帧失败")
                break

            frame_count += 1
            corners, ids, _ = detect_markers(frame, dictionary)
            raw_results = collect_marker_results(corners, ids)
            filtered_results = filter_and_enrich_results(raw_results, args, pose_inputs)
            stable_results, events = tracker.update(filtered_results)

            for event in events:
                print(event)

            fixed_reference_mode = bool(reference_marker_ids)
            stable_origin_result = None
            stable_reference_markers = []
            missing_reference_ids = []

            if fixed_reference_mode:
                stable_reference_markers, missing_reference_ids = (
                    get_stable_reference_markers(stable_results, reference_marker_ids)
                )
                mapping_ready = not missing_reference_ids
            else:
                if args.origin_marker_id is None and stable_results:
                    stable_origin_result = stable_results[0]
                elif args.origin_marker_id is not None:
                    stable_origin_result = next(
                        (
                            result
                            for result in stable_results
                            if result["id"] == args.origin_marker_id
                        ),
                        None,
                    )

                if stable_origin_result is not None and args.origin_marker_id is None:
                    args.origin_marker_id = stable_origin_result["id"]

                mapping_ready = stable_origin_result is not None

            if stable_results:
                result_text = format_results_text(
                    stable_results,
                    title="[INFO] 当前稳定检测结果",
                )
                if result_text != last_result_text:
                    print(result_text)
                    if fixed_reference_mode and mapping_ready:
                        print(
                            f"[INFO] reference markers 已稳定: IDs {reference_marker_ids}"
                        )
                    elif stable_origin_result is not None:
                        print(
                            f"[INFO] origin marker 已稳定: ID {stable_origin_result['id']}"
                        )
                    last_result_text = result_text

            annotated_frame = annotate_detection_result(frame, filtered_results)
            display_frame = draw_live_info(
                annotated_frame.copy(),
                frame_count,
                start_time,
                stable_results=stable_results,
            )

            # Handle --show-wall-map mode
            window_name = WINDOW_NAME
            mapping_info = None
            wall_markers = []

            if fixed_reference_mode:
                mapping_info = build_mapping_info(
                    reference_ids_detected=[marker["id"] for marker in stable_reference_markers],
                    mapping_valid=False,
                )

            if mapping_ready:
                current_wall_stage = "pre_render"
                current_wall_object_name = "stable_results"
                current_wall_object = stable_results
                try:
                    current_wall_stage = "compute_marker_wall_coords"
                    wall_markers, mapping_info = compute_marker_wall_coords(
                        marker_list=stable_results,
                        reference_marker=stable_origin_result,
                        marker_size_mm=args.marker_size_mm,
                        origin_mode=args.origin,
                        reference_marker_ids=reference_marker_ids,
                        return_mapping_info=True,
                    )
                    if mapping_info["mapping_valid"]:
                        wall_markers = apply_wall_center_filter(
                            wall_markers,
                            filter_state=wall_filter_state,
                            reference_marker_ids=reference_marker_ids,
                            alpha=EMA_ALPHA,
                        )
                        current_wall_object_name = "wall_markers"
                        current_wall_object = wall_markers
                        log_wall_map_input(
                            reference_info=reference_marker_ids or args.origin_marker_id,
                            stable_results=stable_results,
                            wall_markers=wall_markers,
                        )

                except Exception as exc:
                    print(f"[WARN] Wall map rendering failed: {exc}")
                    log_wall_map_exception(
                        stage=current_wall_stage,
                        object_name=current_wall_object_name,
                        obj=current_wall_object,
                    )

            if mapping_info and should_print_status_block(
                last_mapping_status,
                mapping_info,
                frame_count,
                last_status_frame,
            ):
                print_mapping_status(mapping_info)
                print_marker_statuses(
                    wall_markers,
                    reference_marker_ids=reference_marker_ids,
                )
                last_mapping_status = dict(mapping_info)
                last_status_frame = frame_count

            if wall_tracking_logger is not None:
                wall_tracking_logger.log_frame(
                    frame_idx=frame_count,
                    stable_results=stable_results,
                    wall_markers=wall_markers,
                    mapping_info=mapping_info or build_mapping_info([], False),
                    reference_marker_ids=reference_marker_ids,
                )

            if args.show_wall_map and mapping_info and mapping_info.get("mapping_valid"):
                try:
                    current_wall_stage = "render_wall_map"
                    current_wall_object_name = "wall_markers"
                    current_wall_object = wall_markers
                    wall_map_image = wall_map_renderer.render_wall_map(
                        markers=wall_markers,
                        reference_marker_id=args.origin_marker_id,
                        reference_marker_ids=reference_marker_ids,
                        marker_size_mm=int(args.marker_size_mm),
                        mapping_info=mapping_info,
                    )
                    current_wall_stage = "compose_dual_view"
                    current_wall_object_name = "wall_map_image"
                    current_wall_object = wall_map_image
                    display_frame = compose_dual_view(display_frame, wall_map_image)
                    window_name = "Camera + Wall Map"
                except Exception as exc:
                    print(f"[WARN] Wall map rendering failed: {exc}")
                    log_wall_map_exception(
                        stage=current_wall_stage,
                        object_name=current_wall_object_name,
                        obj=current_wall_object,
                    )
            elif args.show_wall_map:
                if fixed_reference_mode:
                    reference_label = ", ".join(
                        str(marker_id) for marker_id in reference_marker_ids
                    )
                    status_lines = [
                        f"Waiting for reference markers ({reference_label})..."
                    ]
                    if stable_reference_markers:
                        status_lines.append(
                            f"Visible: {[marker['id'] for marker in stable_reference_markers]}"
                        )
                    if missing_reference_ids:
                        status_lines.append(
                            f"Reference markers incomplete: missing {missing_reference_ids}"
                        )
                    elif mapping_info and not mapping_info["mapping_valid"]:
                        status_lines = [
                            "Mapping invalid",
                            f"Visible refs: {mapping_info['reference_ids_detected']}",
                        ]
                        reprojection_error_px = mapping_info.get("reprojection_error_px")
                        if reprojection_error_px is not None:
                            status_lines.append(
                                f"Reproj err: {reprojection_error_px:.2f} px"
                            )
                        status_lines.append(
                            "Wall coords paused until mapping is valid"
                        )
                else:
                    status_lines = ["Waiting for reference marker to stabilize..."]

                wall_status_panel = build_wall_status_panel(
                    wall_map_renderer,
                    status_lines,
                )
                display_frame = compose_dual_view(display_frame, wall_status_panel)
                window_name = "Camera + Wall Map"

            cv2.imshow(window_name, display_frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                print("[INFO] Quit.")
                break

            if key == ord("s"):
                if fixed_reference_mode and not mapping_ready:
                    print("[INFO] reference markers 尚未完整稳定，跳过当前导出。")
                    continue

                if fixed_reference_mode and mapping_info and not mapping_info["mapping_valid"]:
                    print("[INFO] 当前映射无效，跳过当前导出。")
                    continue

                if not fixed_reference_mode and stable_origin_result is None:
                    print("[INFO] origin marker 尚未稳定，跳过当前导出。")
                    continue

                detection_payload = build_detection_payload(
                    results=stable_results,
                    frame_shape=frame.shape,
                    dictionary_name=args.dict,
                    source=f"camera:{args.camera}",
                    marker_size_mm=args.marker_size_mm,
                    camera_matrix=pose_inputs["camera_matrix"],
                    dist_coeffs=pose_inputs["dist_coeffs"],
                )
                try:
                    export_pipeline_outputs(
                        detection_payload=detection_payload,
                        image_to_save=display_frame,
                        prefix="camera",
                        args=args,
                    )
                except Exception as exc:
                    print(f"[WARN] 当前帧导出失败: {exc}")

    finally:
        cap.release()
        if wall_tracking_logger is not None:
            wall_tracking_logger.close()
        cv2.destroyAllWindows()
        print("[INFO] Camera released, windows closed.")


def main():
    args = parse_args()

    try:
        if args.image is not None:
            run_image_mode(args)
        else:
            run_camera_mode(args)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
