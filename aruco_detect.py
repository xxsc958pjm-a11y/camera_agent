import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from aruco_runtime import (
    StableMarkerTracker,
    attach_pose_estimates,
    filter_marker_results,
)
from aruco_to_wall_coords import (
    OUTPUT_DIR as WALL_OUTPUT_DIR,
    convert_detection_to_wall_payload,
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


OUTPUT_DIR = Path("outputs/aruco_detect")
WINDOW_NAME = "Aruco Detection"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Detect Aruco markers from an image or a live camera stream."
    )
    parser.add_argument(
        "--image",
        help="Path to the input image, for example: images/test.jpg",
    )
    parser.add_argument(
        "--camera",
        type=int,
        default=DEFAULT_CAMERA_INDEX,
        help="Camera index for real-time detection. Default: 8",
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
        help="Enable wall coordinate export using this physical marker size in millimeters",
    )
    parser.add_argument(
        "--origin-marker-id",
        type=int,
        help="Marker ID used as wall origin. Default: first detected marker",
    )
    parser.add_argument(
        "--origin",
        choices=["top_left", "center"],
        default="top_left",
        help="Wall coordinate origin on the reference marker. Default: top_left",
    )
    parser.add_argument(
        "--target-marker-ids",
        nargs="+",
        type=int,
        help="Optional marker IDs to keep as valid detections",
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

    return args


def ensure_output_dir(output_dir=OUTPUT_DIR):
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def validate_image_path(image_path):
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"图像路径不存在: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"输入路径不是文件: {path}")
    return path


def load_image(image_path):
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"图像读取失败: {image_path}")
    return image


def load_optional_matrix_json(path_text):
    if path_text is None:
        return None

    path = Path(path_text)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"标定文件不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def get_pose_inputs(args):
    return {
        "camera_matrix": load_optional_matrix_json(args.camera_matrix),
        "dist_coeffs": load_optional_matrix_json(args.dist_coeffs),
    }


def get_aruco_dictionary(dict_name):
    if not hasattr(cv2, "aruco"):
        raise RuntimeError(
            "当前 OpenCV 环境缺少 aruco 模块，请安装 opencv-contrib-python。"
        )

    if not hasattr(cv2.aruco, dict_name):
        raise ValueError(f"不支持的 Aruco 字典: {dict_name}")

    dictionary_id = getattr(cv2.aruco, dict_name)
    return cv2.aruco.getPredefinedDictionary(dictionary_id)


def create_detector_parameters():
    if hasattr(cv2.aruco, "DetectorParameters"):
        return cv2.aruco.DetectorParameters()
    return cv2.aruco.DetectorParameters_create()


def detect_markers(image, dictionary):
    parameters = create_detector_parameters()

    if hasattr(cv2.aruco, "ArucoDetector"):
        detector = cv2.aruco.ArucoDetector(dictionary, parameters)
        corners, ids, rejected = detector.detectMarkers(image)
    else:
        corners, ids, rejected = cv2.aruco.detectMarkers(
            image, dictionary, parameters=parameters
        )

    return corners, ids, rejected


def compute_center(points):
    center_x = int(points[:, 0].mean())
    center_y = int(points[:, 1].mean())
    return center_x, center_y


def build_marker_result(marker_corners, marker_id):
    points = marker_corners.reshape((4, 2)).astype(int)
    return {
        "id": int(marker_id),
        "corners": [(int(x), int(y)) for x, y in points],
        "center": compute_center(points),
    }


def collect_marker_results(corners, ids):
    if ids is None or len(corners) == 0:
        return []

    return [
        build_marker_result(marker_corners, marker_id)
        for marker_corners, marker_id in zip(corners, ids.flatten())
    ]


def draw_marker_info(image, marker_result):
    corners = marker_result["corners"]
    center = marker_result["center"]
    marker_id = marker_result["id"]
    point_array = np.array(corners, dtype=np.int32)

    cv2.polylines(
        image,
        [point_array.astype(int)],
        isClosed=True,
        color=(0, 255, 0),
        thickness=2,
    )

    labels = ["TL", "TR", "BR", "BL"]
    for index, point in enumerate(corners):
        point_tuple = (int(point[0]), int(point[1]))
        cv2.circle(image, point_tuple, 5, (0, 0, 255), -1)
        cv2.putText(
            image,
            f"{labels[index]}{point_tuple}",
            (point_tuple[0] + 8, point_tuple[1] - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 0),
            1,
            cv2.LINE_AA,
        )

    cv2.circle(image, center, 6, (255, 0, 0), -1)
    cv2.putText(
        image,
        f"ID {marker_id}",
        (corners[0][0], corners[0][1] - 12),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        f"C{center}",
        (center[0] + 8, center[1] + 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 0, 0),
        1,
        cv2.LINE_AA,
    )


def annotate_detection_result(image, results):
    annotated = image.copy()
    for marker_result in results:
        draw_marker_info(annotated, marker_result)
    return annotated


def build_detection_payload(
    results,
    frame_shape,
    dictionary_name,
    source,
    marker_size_mm=None,
    camera_matrix=None,
    dist_coeffs=None,
):
    height, width = frame_shape[:2]
    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "dictionary": dictionary_name,
        "image_size": {
            "width": width,
            "height": height,
        },
        "pose_estimation": {
            "marker_size_mm": marker_size_mm,
            "camera_matrix": camera_matrix,
            "dist_coeffs": dist_coeffs,
        },
        "marker_count": len(results),
        "markers": [
            {
                "id": result["id"],
                "corners": [
                    {"x": corner[0], "y": corner[1]} for corner in result["corners"]
                ],
                "center": {
                    "x": result["center"][0],
                    "y": result["center"][1],
                },
                "pose": result.get("pose"),
            }
            for result in results
        ],
    }


def format_results_text(results, title="[INFO] 检测结果"):
    if not results:
        return "[INFO] 未检测到 Aruco 标签。"

    lines = [f"{title}: {len(results)} 个 Aruco 标签"]
    for result in results:
        lines.append(f"  - ID: {result['id']}")
        lines.append(f"    四个角点: {result['corners']}")
        lines.append(f"    中心点: {result['center']}")
    return "\n".join(lines)


def print_detection_results(results, title="[INFO] 检测结果"):
    print(format_results_text(results, title=title))


def build_output_path(prefix, output_dir):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return output_dir / f"{prefix}_aruco_{timestamp}.png"


def build_json_output_path(prefix, output_dir):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return output_dir / f"{prefix}_aruco_{timestamp}.json"


def save_result_image(image, prefix, output_dir):
    output_path = build_output_path(prefix, output_dir)
    success = cv2.imwrite(str(output_path), image)
    if not success:
        raise RuntimeError(f"保存结果图失败: {output_path}")
    print(f"[INFO] 已保存结果图: {output_path}")


def save_detection_json(payload, prefix, output_dir):
    output_path = build_json_output_path(prefix, output_dir)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[INFO] 已保存检测结果 JSON: {output_path}")


def export_wall_coordinates(
    detection_payload,
    prefix,
    marker_size_mm,
    origin_marker_id,
    origin_mode,
):
    if marker_size_mm is None:
        return None

    try:
        wall_output_dir = ensure_output_dir(WALL_OUTPUT_DIR)
        wall_payload = convert_detection_to_wall_payload(
            detection_payload=detection_payload,
            marker_size_mm=marker_size_mm,
            origin_marker_id=origin_marker_id,
            origin_mode=origin_mode,
        )
        print_wall_results(wall_payload)
        return save_wall_json_with_prefix(wall_payload, prefix, wall_output_dir)
    except Exception as exc:
        print(f"[WARN] 墙面坐标导出失败: {exc}")
        return None


def show_image_result(image, image_path, output_dir, payload):
    print("[INFO] 按 'q' 退出窗口，按 's' 保存结果图。")

    while True:
        cv2.imshow(WINDOW_NAME, image)
        key = cv2.waitKey(0) & 0xFF

        if key == ord("q"):
            print("[INFO] Quit.")
            break
        if key == ord("s"):
            save_result_image(image, image_path.stem, output_dir)
            save_detection_json(payload, image_path.stem, output_dir)

    cv2.destroyAllWindows()


def draw_live_info(frame, frame_count, start_time, stable_results=None):
    elapsed = time.time() - start_time
    fps = frame_count / elapsed if elapsed > 0 else 0.0
    height, width = frame.shape[:2]
    stable_ids = (
        ",".join(str(result["id"]) for result in stable_results) if stable_results else "-"
    )

    info_lines = [
        f"Resolution: {width}x{height}",
        f"Frame: {frame_count}",
        f"FPS: {fps:.2f}",
        f"Stable IDs: {stable_ids}",
        "Press 's' to save frame",
        "Press 'q' to quit",
    ]

    y = 30
    for line in info_lines:
        cv2.putText(
            frame,
            line,
            (10, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )
        y += 30

    return frame


def filter_and_enrich_results(results, args, pose_inputs):
    filtered_results = filter_marker_results(results, args.target_marker_ids)
    return attach_pose_estimates(
        filtered_results,
        marker_size_mm=args.marker_size_mm,
        camera_matrix=pose_inputs["camera_matrix"],
        dist_coeffs=pose_inputs["dist_coeffs"],
    )


def run_image_mode(args, dictionary_name, dictionary, output_dir, pose_inputs):
    image_path = validate_image_path(args.image)
    image = load_image(image_path)
    corners, ids, _ = detect_markers(image, dictionary)
    results = collect_marker_results(corners, ids)
    effective_results = filter_and_enrich_results(results, args, pose_inputs)
    annotated_image = annotate_detection_result(image, effective_results)
    payload = build_detection_payload(
        results=effective_results,
        frame_shape=image.shape,
        dictionary_name=dictionary_name,
        source=str(image_path),
        marker_size_mm=args.marker_size_mm,
        camera_matrix=pose_inputs["camera_matrix"],
        dist_coeffs=pose_inputs["dist_coeffs"],
    )
    print_detection_results(effective_results)
    save_detection_json(payload, image_path.stem, output_dir)
    export_wall_coordinates(
        detection_payload=payload,
        prefix=image_path.stem,
        marker_size_mm=args.marker_size_mm,
        origin_marker_id=args.origin_marker_id,
        origin_mode=args.origin,
    )
    show_image_result(annotated_image, image_path, output_dir, payload)


def run_camera_mode(args, dictionary_name, dictionary, output_dir, pose_inputs):
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
    frame_count = 0
    start_time = time.time()
    last_printed_text = None

    print("[INFO] 实时检测已启动，按 'q' 退出，按 's' 保存当前稳定结果。")

    try:
        while True:
            ret, frame = read_bgr_frame(cap)
            if not ret or frame is None:
                print("[ERROR] 读取摄像头帧失败")
                break

            frame_count += 1
            if frame_count == 1:
                print(f"first frame shape = {frame.shape}")

            corners, ids, _ = detect_markers(frame, dictionary)
            raw_results = collect_marker_results(corners, ids)
            filtered_results = filter_and_enrich_results(raw_results, args, pose_inputs)
            stable_results, events = tracker.update(filtered_results)

            for event in events:
                print(event)

            annotated_frame = annotate_detection_result(frame, filtered_results)
            display_frame = draw_live_info(
                annotated_frame,
                frame_count,
                start_time,
                stable_results=stable_results,
            )

            if stable_results:
                result_text = format_results_text(
                    stable_results,
                    title="[INFO] 当前稳定检测结果",
                )
                if result_text != last_printed_text:
                    print(result_text)
                    last_printed_text = result_text

            cv2.imshow(WINDOW_NAME, display_frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                print("[INFO] Quit.")
                break
            if key == ord("s"):
                export_results = stable_results or filtered_results
                payload = build_detection_payload(
                    results=export_results,
                    frame_shape=frame.shape,
                    dictionary_name=dictionary_name,
                    source=f"camera:{args.camera}",
                    marker_size_mm=args.marker_size_mm,
                    camera_matrix=pose_inputs["camera_matrix"],
                    dist_coeffs=pose_inputs["dist_coeffs"],
                )
                save_result_image(display_frame, "camera", output_dir)
                save_detection_json(payload, "camera", output_dir)
                export_wall_coordinates(
                    detection_payload=payload,
                    prefix="camera",
                    marker_size_mm=args.marker_size_mm,
                    origin_marker_id=args.origin_marker_id,
                    origin_mode=args.origin,
                )

    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("[INFO] Camera released, windows closed.")


def main():
    args = parse_args()

    try:
        dictionary_name = args.dict
        dictionary = get_aruco_dictionary(dictionary_name)
        output_dir = ensure_output_dir()
        pose_inputs = get_pose_inputs(args)

        if args.image is not None:
            run_image_mode(args, dictionary_name, dictionary, output_dir, pose_inputs)
        else:
            run_camera_mode(args, dictionary_name, dictionary, output_dir, pose_inputs)

    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
