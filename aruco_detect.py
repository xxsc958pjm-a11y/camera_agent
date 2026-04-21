import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from aruco_to_wall_coords import (
    OUTPUT_DIR as WALL_OUTPUT_DIR,
    convert_detection_to_wall_payload,
    print_wall_results,
    save_wall_json_with_prefix,
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
        help="Camera index for real-time detection, for example: 0",
    )
    parser.add_argument(
        "--dict",
        default="DICT_4X4_50",
        help="Aruco dictionary name. Default: DICT_4X4_50",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1280,
        help="Camera width for live mode. Default: 1280",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=720,
        help="Camera height for live mode. Default: 720",
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
        choices=["top_left", "center", "bottom_left"],
        default="top_left",
        help="Wall coordinate origin on the reference marker. Default: top_left",
    )

    args = parser.parse_args()

    if args.image is None and args.camera is None:
        parser.error("请至少提供 --image 或 --camera 其中一种输入方式。")

    if args.image is not None and args.camera is not None:
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


def open_camera(camera_index=0, width=None, height=None):
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"无法打开摄像头: {camera_index}")

    if width is not None:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    if height is not None:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(
        f"[INFO] Camera opened: index={camera_index}, size={actual_width}x{actual_height}"
    )
    return cap


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
        parameters = cv2.aruco.DetectorParameters()
    else:
        parameters = cv2.aruco.DetectorParameters_create()

    _set_detector_parameter(
        parameters,
        "cornerRefinementMethod",
        getattr(cv2.aruco, "CORNER_REFINE_SUBPIX", None),
    )
    _set_detector_parameter(parameters, "cornerRefinementWinSize", 5)
    _set_detector_parameter(parameters, "cornerRefinementMaxIterations", 30)
    _set_detector_parameter(parameters, "cornerRefinementMinAccuracy", 0.01)
    _set_detector_parameter(parameters, "adaptiveThreshWinSizeMin", 5)
    _set_detector_parameter(parameters, "adaptiveThreshWinSizeMax", 23)
    _set_detector_parameter(parameters, "adaptiveThreshWinSizeStep", 4)
    _set_detector_parameter(parameters, "minMarkerPerimeterRate", 0.02)
    _set_detector_parameter(parameters, "polygonalApproxAccuracyRate", 0.04)
    return parameters


def _set_detector_parameter(parameters, name, value):
    if value is None:
        return
    if hasattr(parameters, name):
        setattr(parameters, name, value)


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


def draw_marker_info(image, marker_corners, marker_id):
    points = marker_corners.reshape((4, 2)).astype(int)
    center = compute_center(points)

    cv2.polylines(image, [points], isClosed=True, color=(0, 255, 0), thickness=2)

    labels = ["TL", "TR", "BR", "BL"]
    for index, point in enumerate(points):
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
        (points[0][0], points[0][1] - 12),
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

    return {
        "id": int(marker_id),
        "corners": [(int(x), int(y)) for x, y in points],
        "center": center,
    }


def annotate_detection_result(image, corners_or_results, ids=None):
    """
    Annotate image with detected marker information.
    
    Supports two calling conventions:
    1. Old API: annotate_detection_result(image, corners, ids) -> (annotated, results)
    2. New API: annotate_detection_result(image, results) -> annotated
    
    Args:
        image: Input image
        corners_or_results: Either corners array (old API) or results list (new API)
        ids: Marker IDs (only for old API)
    
    Returns:
        If old API (ids is provided): (annotated_image, results)
        If new API (ids is None): annotated_image
    """
    annotated = image.copy()
    
    # Check which API is being used based on whether ids is provided
    if ids is not None:
        # Old API: corners_or_results contains corners, ids is provided
        corners = corners_or_results
        results = []
        
        if ids is None or len(corners) == 0:
            return annotated, results
        
        for marker_corners, marker_id in zip(corners, ids.flatten()):
            marker_result = draw_marker_info(annotated, marker_corners, marker_id)
            results.append(marker_result)
        
        return annotated, results
    else:
        # New API: corners_or_results contains results list
        results = corners_or_results
        
        if not results:
            return annotated
        
        for result in results:
            marker_id = result["id"]
            # Convert corners back to the format expected by draw_marker_info
            corners_list = result["corners"]
            
            # Reconstruct marker_corners array
            marker_corners = np.array([corners_list], dtype=np.float32).reshape(1, 4, 2)[0]
            
            # Draw on image (but don't update result, just use it to draw)
            draw_marker_info(annotated, marker_corners, marker_id)
        
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
    payload = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "dictionary": dictionary_name,
        "image_size": {
            "width": width,
            "height": height,
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
            }
            for result in results
        ],
    }

    if marker_size_mm is not None:
        payload["marker_size_mm"] = marker_size_mm
    if camera_matrix is not None:
        payload["camera_matrix"] = camera_matrix
    if dist_coeffs is not None:
        payload["dist_coeffs"] = dist_coeffs

    return payload


def format_results_text(results, title=None):
    if not results:
        return title or "[INFO] 未检测到 Aruco 标签。"

    lines = [title or f"[INFO] 检测到 {len(results)} 个 Aruco 标签:"]
    for result in results:
        lines.append(f"  - ID: {result['id']}")
        lines.append(f"    四个角点: {result['corners']}")
        lines.append(f"    中心点: {result['center']}")
    return "\n".join(lines)


def print_detection_results(results):
    print(format_results_text(results))


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

    info_lines = [
        f"Resolution: {width}x{height}",
        f"Frame: {frame_count}",
        f"FPS: {fps:.2f}",
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


def run_image_mode(args, dictionary_name, dictionary, output_dir):
    image_arg = args.image
    image_path = validate_image_path(image_arg)
    image = load_image(image_path)
    corners, ids, _ = detect_markers(image, dictionary)
    annotated_image, results = annotate_detection_result(image, corners, ids)
    payload = build_detection_payload(
        results=results,
        frame_shape=image.shape,
        dictionary_name=dictionary_name,
        source=str(image_path),
    )
    print_detection_results(results)
    save_detection_json(payload, image_path.stem, output_dir)
    export_wall_coordinates(
        detection_payload=payload,
        prefix=image_path.stem,
        marker_size_mm=args.marker_size_mm,
        origin_marker_id=args.origin_marker_id,
        origin_mode=args.origin,
    )
    show_image_result(annotated_image, image_path, output_dir, payload)


def run_camera_mode(args, dictionary_name, dictionary, output_dir):
    camera_index = args.camera
    width = args.width
    height = args.height
    cap = open_camera(camera_index=camera_index, width=width, height=height)
    frame_count = 0
    start_time = time.time()
    last_result_text = None

    print("[INFO] 实时检测已启动，按 'q' 退出，按 's' 保存当前结果帧。")

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                print("[ERROR] 读取摄像头帧失败")
                break

            frame_count += 1
            corners, ids, _ = detect_markers(frame, dictionary)
            annotated_frame, results = annotate_detection_result(frame, corners, ids)
            display_frame = draw_live_info(annotated_frame, frame_count, start_time)
            payload = build_detection_payload(
                results=results,
                frame_shape=frame.shape,
                dictionary_name=dictionary_name,
                source=f"camera:{camera_index}",
            )

            result_text = format_results_text(results)
            if result_text != last_result_text:
                print(result_text)
                last_result_text = result_text

            cv2.imshow(WINDOW_NAME, display_frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                print("[INFO] Quit.")
                break
            if key == ord("s"):
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


def collect_marker_results(corners, ids):
    """
    Convert detected Aruco markers (corners and ids) into result list format.
    
    Args:
        corners: List of detected corners from cv2.aruco
        ids: Array of detected marker IDs from cv2.aruco
    
    Returns:
        List of marker result dicts with 'id', 'corners', 'center' fields
    """
    results = []
    
    if ids is None or len(corners) == 0:
        return results
    
    for marker_corners, marker_id in zip(corners, ids.flatten()):
        # Create a dummy image for draw_marker_info (we don't need it but the function modifies it)
        dummy_image = np.zeros((1, 1, 3), dtype=np.uint8)
        marker_result = draw_marker_info(dummy_image, marker_corners, marker_id)
        results.append(marker_result)
    
    return results


def get_pose_inputs(args):
    """
    Extract camera matrix and distortion coefficients from args.
    
    Args:
        args: Parsed command-line arguments
    
    Returns:
        Dict with 'camera_matrix' and 'dist_coeffs' keys (both None for now)
    """
    # For now, return None for both - pose estimation is not yet implemented
    camera_matrix = None
    dist_coeffs = None
    
    # In the future, these could be loaded from files specified in:
    # args.camera_matrix
    # args.dist_coeffs
    
    return {
        "camera_matrix": camera_matrix,
        "dist_coeffs": dist_coeffs,
    }


def filter_and_enrich_results(raw_results, args, pose_inputs):
    """
    Filter and enrich marker results based on args and pose inputs.
    
    Args:
        raw_results: Raw marker results from collect_marker_results
        args: Parsed command-line arguments
        pose_inputs: Dict with camera matrix and distortion coefficients
    
    Returns:
        Filtered and enriched marker results
    """
    from aruco_runtime import filter_marker_results, attach_pose_estimates
    
    # Filter by marker IDs if specified
    filtered_results = filter_marker_results(
        raw_results,
        target_marker_ids=args.target_marker_ids if hasattr(args, 'target_marker_ids') else None
    )
    
    # Attach pose estimates
    enriched_results = attach_pose_estimates(
        filtered_results,
        marker_size_mm=args.marker_size_mm if hasattr(args, 'marker_size_mm') else None,
        camera_matrix=pose_inputs.get("camera_matrix"),
        dist_coeffs=pose_inputs.get("dist_coeffs"),
    )
    
    return enriched_results


def main():
    args = parse_args()

    try:
        dictionary_name = args.dict
        dictionary = get_aruco_dictionary(dictionary_name)
        output_dir = ensure_output_dir()

        if args.image is not None:
            run_image_mode(args, dictionary_name, dictionary, output_dir)
        else:
            run_camera_mode(args, dictionary_name, dictionary, output_dir)

    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
