import cv2
import time
from pathlib import Path


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
    print(f"[INFO] Camera opened: index={camera_index}, size={actual_width}x{actual_height}")

    return cap


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
    camera_index = 0
    output_dir = ensure_output_dir()

    cap = open_camera(camera_index=camera_index, width=1280, height=720)

    frame_count = 0
    start_time = time.time()

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                print("[ERROR] 读取摄像头帧失败")
                break

            frame_count += 1

            display_frame = frame.copy()
            display_frame = draw_info(display_frame, frame_count, start_time)

            cv2.imshow("Camera Input Preview", display_frame)

            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                print("[INFO] Quit.")
                break
            elif key == ord("s"):
                save_frame(frame, output_dir)

    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("[INFO] Camera released, windows closed.")


if __name__ == "__main__":
    main()
