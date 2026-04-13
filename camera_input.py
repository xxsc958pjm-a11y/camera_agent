import argparse

import cv2
import time
from pathlib import Path

DEFAULT_CAMERA_INDEX = 8


def parse_args():
    parser = argparse.ArgumentParser(description="Open a camera preview window.")
    parser.add_argument(
        "--camera",
        type=int,
        default=DEFAULT_CAMERA_INDEX,
        help="Camera index to open. Default: 8",
    )
    return parser.parse_args()


def open_camera(camera_index, width=640, height=480):
    cap = cv2.VideoCapture(camera_index, cv2.CAP_V4L2)

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
    cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)

    print(f"requested source = {camera_index}")
    print(f"opened = {cap.isOpened()}")
    print(f"actual width = {cap.get(cv2.CAP_PROP_FRAME_WIDTH)}")
    print(f"actual height = {cap.get(cv2.CAP_PROP_FRAME_HEIGHT)}")
    fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
    print(f"actual fourcc = {''.join([chr((fourcc >> 8 * i) & 0xFF) for i in range(4)])}")
    if not cap.isOpened():
        raise RuntimeError(f"无法打开摄像头: {camera_index}")

    ret, frame = cap.read()
    print(f"ret = {ret}")
    print(f"frame is None = {frame is None}")
    if frame is not None:
        frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_YUY2)
        print(f"frame.shape = {frame.shape}")
        print(f"frame.dtype = {frame.dtype}")
        print(f"min/max/mean = {frame.min()} {frame.max()} {frame.mean()}")
        cv2.imwrite("debug_frame.jpg", frame)

    actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"[INFO] Camera opened: source={camera_index}, size={actual_width}x{actual_height}")

    return cap, ret, frame


def prepare_display_frame(frame):
    if frame is None:
        return None
    return frame


def ensure_output_dir(output_dir="outputs/captured_frames"):
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def draw_info(frame, frame_count, start_time):
    h, w = frame.shape[:2]
    elapsed = time.time() - start_time
    fps = frame_count / elapsed if elapsed > 0 else 0.0

    info_lines = [
        f"Resolution: {w}x{h}",
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
    cap, first_ret, first_frame = open_camera(
        camera_index=args.camera, width=640, height=480
    )

    frame_count = 0
    start_time = time.time()

    try:
        while True:
            if frame_count == 0:
                ret, frame = first_ret, first_frame
            else:
                ret, frame = cap.read()
                if ret and frame is not None:
                    frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_YUY2)
            if not ret or frame is None:
                print("[ERROR] 读取摄像头帧失败")
                break

            frame_count += 1
            if frame_count == 1:
                cv2.imwrite("debug_frame.jpg", frame)
                print("[INFO] Saved debug frame: debug_frame.jpg")

            raw_mean = frame.mean()
            display_frame = prepare_display_frame(frame)
            if display_frame is None:
                print("[ERROR] 帧格式转换失败")
                break
            display_mean = display_frame.mean()
            if frame_count == 1:
                print(f"[DEBUG] raw frame mean: {raw_mean:.2f}")
                print(f"[DEBUG] converted frame mean: {display_mean:.2f}")
            display_frame = draw_info(display_frame, frame_count, start_time)

            cv2.imshow("Camera Input Preview", display_frame)

            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                print("[INFO] Quit.")
                break
            elif key == ord("s"):
                save_frame(display_frame, output_dir)

    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("[INFO] Camera released, windows closed.")


if __name__ == "__main__":
    main()
