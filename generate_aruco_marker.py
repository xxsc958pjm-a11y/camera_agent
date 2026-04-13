import argparse
import sys
from pathlib import Path

import cv2
import numpy as np


OUTPUT_DIR = Path("outputs/aruco_markers")


def parse_args():
    parser = argparse.ArgumentParser(description="Generate an Aruco marker image.")
    parser.add_argument(
        "--id",
        type=int,
        default=23,
        help="Marker ID to generate. Default: 23",
    )
    parser.add_argument(
        "--dict",
        default="DICT_4X4_50",
        help="Aruco dictionary name. Default: DICT_4X4_50",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=600,
        help="Marker image size in pixels. Default: 600",
    )
    parser.add_argument(
        "--margin",
        type=int,
        default=120,
        help="White border margin in pixels around the marker. Default: 120",
    )
    return parser.parse_args()


def ensure_output_dir(output_dir=OUTPUT_DIR):
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def get_aruco_dictionary(dict_name):
    if not hasattr(cv2, "aruco"):
        raise RuntimeError("当前 OpenCV 环境缺少 aruco 模块，请安装 opencv-contrib-python。")

    if not hasattr(cv2.aruco, dict_name):
        raise ValueError(f"不支持的 Aruco 字典: {dict_name}")

    dictionary_id = getattr(cv2.aruco, dict_name)
    return cv2.aruco.getPredefinedDictionary(dictionary_id)


def generate_marker_image(dictionary, marker_id, size, margin):
    if marker_id < 0:
        raise ValueError("marker id 不能小于 0")
    if size <= 0:
        raise ValueError("size 必须大于 0")
    if margin < 0:
        raise ValueError("margin 不能小于 0")

    marker_image = cv2.aruco.generateImageMarker(dictionary, marker_id, size)
    canvas_size = size + margin * 2
    canvas = 255 * np.ones((canvas_size + 100, canvas_size), dtype="uint8")
    canvas[margin : margin + size, margin : margin + size] = marker_image

    cv2.putText(
        canvas,
        f"Aruco ID {marker_id}",
        (20, canvas_size + 55),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        0,
        2,
        cv2.LINE_AA,
    )
    return canvas


def build_output_path(marker_id, output_dir):
    return output_dir / f"aruco_id_{marker_id}.png"


def save_marker_image(image, output_path):
    success = cv2.imwrite(str(output_path), image)
    if not success:
        raise RuntimeError(f"保存 Aruco 图像失败: {output_path}")
    print(f"[INFO] 已生成 Aruco 标签图: {output_path}")


def main():
    args = parse_args()

    try:
        output_dir = ensure_output_dir()
        dictionary = get_aruco_dictionary(args.dict)
        image = generate_marker_image(dictionary, args.id, args.size, args.margin)
        output_path = build_output_path(args.id, output_dir)
        save_marker_image(image, output_path)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
