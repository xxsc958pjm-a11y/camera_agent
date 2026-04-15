import argparse
import time
from pathlib import Path

import cv2

from camera_source import (
    DEFAULT_CAMERA_INDEX,
    DEFAULT_FRAME_HEIGHT,
    DEFAULT_FRAME_WIDTH,
    get_camera_debug_info,
    open_camera,
    read_bgr_frame,
)


WINDOW_NAME = "Camera Input Preview"


def parse_args():
    parser = argparse.ArgumentParser(description="Open a camera preview window.")
    parser.add_argument(
        "--camera",
        type=int,
        default=DEFAULT_CAMERA_INDEX,
        help="Camera index to open. Default: 8",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_FRAME_WIDTH,
        help="Camera width. Default: 640",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=DEFAULT_FRAME_HEIGHT,
        help="Camera height. Default: 480",
    )
    return parser.parse_args()


def ensure_output_dir(output_dir="outputs/captured_frames"):
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def draw_info(frame, frame_count, start_time):
    height, width = frame.shape[:2]
    elapsed = time.time() - start_time
    fps = frame_count / elapsed if elapsed > 0 else 0.0

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


def save_frame(frame, output_dir):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = output_dir / f"frame_{timestamp}.png"
    success = cv2.imwrite(str(filename), frame)
    if not success:
        raise RuntimeError(f"保存图像失败: {filename}")
    print(f"[INFO] Saved frame: {filename}")


def main():
    args = parse_args()
    output_dir = ensure_output_dir()
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

    frame_count = 0
    start_time = time.time()

    try:
        while True:
            ret, frame = read_bgr_frame(cap)
            if not ret or frame is None:
                print("[ERROR] 读取摄像头帧失败")
                break

            frame_count += 1
            if frame_count == 1:
                print(f"first frame shape = {frame.shape}")

            display_frame = draw_info(frame.copy(), frame_count, start_time)
            cv2.imshow(WINDOW_NAME, display_frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                print("[INFO] Quit.")
                break
            if key == ord("s"):
                save_frame(frame, output_dir)


    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("[INFO] Camera released, windows closed.")


if __name__ == "__main__":
    main()
