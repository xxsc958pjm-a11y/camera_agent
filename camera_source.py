import cv2


DEFAULT_CAMERA_INDEX = 8
DEFAULT_FRAME_WIDTH = 640
DEFAULT_FRAME_HEIGHT = 480
DEFAULT_FOURCC = "YUYV"


def open_camera(
    camera_index=DEFAULT_CAMERA_INDEX,
    width=DEFAULT_FRAME_WIDTH,
    height=DEFAULT_FRAME_HEIGHT,
):
    cap = cv2.VideoCapture(camera_index, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*DEFAULT_FOURCC))
    cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
    return cap


def read_bgr_frame(cap):
    ret, frame = cap.read()
    if not ret or frame is None:
        return ret, None
    return ret, cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_YUY2)


def get_camera_debug_info(cap, requested_camera_index):
    fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
    return {
        "requested_camera_index": requested_camera_index,
        "opened": cap.isOpened(),
        "actual_width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "actual_height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "actual_fourcc": "".join(
            [chr((fourcc >> (8 * i)) & 0xFF) for i in range(4)]
        ).strip("\x00"),
    }
