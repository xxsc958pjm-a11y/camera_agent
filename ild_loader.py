import argparse
import json
import struct
import sys
import time
from pathlib import Path

import cv2
import numpy as np


DEFAULT_ILD_PATH = Path("/Users/ruirenmei/Desktop/bluelaser.ild")
OUTPUT_DIR = Path("outputs/ilda")
WINDOW_NAME = "ILDA Preview"
CANVAS_SIZE = 900
MARGIN = 60


def parse_args():
    parser = argparse.ArgumentParser(
        description="Load and preview an ILDA laser file."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_ILD_PATH),
        help=f"Path to the ILD file. Default: {DEFAULT_ILD_PATH}",
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="Parse the file without opening a preview window",
    )
    return parser.parse_args()


def ensure_output_dir(output_dir=OUTPUT_DIR):
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def validate_input_path(input_path):
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"ILD 文件不存在: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"输入路径不是文件: {path}")
    return path


def decode_text(raw_bytes):
    return raw_bytes.decode("ascii", errors="ignore").rstrip(" \x00")


def parse_header(data, offset):
    if offset + 32 > len(data):
        return None, offset

    chunk = data[offset : offset + 32]
    if chunk[:4] != b"ILDA":
        raise RuntimeError(f"无效 ILDA header，offset={offset}")

    format_code = chunk[7]
    frame_name = decode_text(chunk[8:16])
    company_name = decode_text(chunk[16:24])
    record_count = struct.unpack(">H", chunk[24:26])[0]
    frame_number = struct.unpack(">H", chunk[26:28])[0]
    total_frames = struct.unpack(">H", chunk[28:30])[0]
    projector_number = chunk[30]

    header = {
        "format_code": format_code,
        "frame_name": frame_name,
        "company_name": company_name,
        "record_count": record_count,
        "frame_number": frame_number,
        "total_frames": total_frames,
        "projector_number": projector_number,
    }
    return header, offset + 32


def parse_format_0_record(record_bytes):
    x, y, z, status, color = struct.unpack(">hhhBB", record_bytes)
    return {
        "x": x,
        "y": y,
        "z": z,
        "status": status,
        "color_index": color,
        "blanked": bool(status & 0x40),
        "last_point": bool(status & 0x80),
    }


def parse_format_1_record(record_bytes):
    x, y, status, color = struct.unpack(">hhBB", record_bytes)
    return {
        "x": x,
        "y": y,
        "z": 0,
        "status": status,
        "color_index": color,
        "blanked": bool(status & 0x40),
        "last_point": bool(status & 0x80),
    }


def get_record_size(format_code):
    if format_code == 0:
        return 8
    if format_code == 1:
        return 6
    raise RuntimeError(
        f"当前仅支持 ILDA format 0 和 1，检测到 format {format_code}"
    )


def parse_record(record_bytes, format_code):
    if format_code == 0:
        return parse_format_0_record(record_bytes)
    if format_code == 1:
        return parse_format_1_record(record_bytes)
    raise RuntimeError(f"不支持的 ILDA format: {format_code}")


def parse_frames(data):
    frames = []
    offset = 0

    while offset < len(data):
        header, offset = parse_header(data, offset)
        if header is None:
            break

        record_count = header["record_count"]
        if record_count == 0:
            break

        record_size = get_record_size(header["format_code"])
        records = []

        for _ in range(record_count):
            if offset + record_size > len(data):
                raise RuntimeError("ILD 文件数据不完整，record 超出文件长度")

            record_bytes = data[offset : offset + record_size]
            records.append(parse_record(record_bytes, header["format_code"]))
            offset += record_size

        frames.append(
            {
                "header": header,
                "records": records,
            }
        )

    if not frames:
        raise RuntimeError("未解析到任何有效 ILDA frame")

    return frames


def load_ild_file(input_path):
    data = input_path.read_bytes()
    if not data:
        raise RuntimeError("ILD 文件为空")
    return parse_frames(data)


def build_summary_payload(input_path, frames):
    total_points = sum(len(frame["records"]) for frame in frames)
    blanked_points = sum(
        1 for frame in frames for record in frame["records"] if record["blanked"]
    )

    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "input_file": str(input_path),
        "frame_count": len(frames),
        "total_points": total_points,
        "blanked_points": blanked_points,
        "frames": [
            {
                "frame_index": index,
                "header": frame["header"],
                "point_count": len(frame["records"]),
            }
            for index, frame in enumerate(frames, start=1)
        ],
    }


def print_summary(summary):
    print(
        f"[INFO] ILDA 文件已加载: frames={summary['frame_count']}, "
        f"total_points={summary['total_points']}, "
        f"blanked_points={summary['blanked_points']}"
    )

    for frame in summary["frames"]:
        header = frame["header"]
        print(
            f"  - frame={frame['frame_index']}, "
            f"name={header['frame_name'] or '<unnamed>'}, "
            f"company={header['company_name'] or '<unknown>'}, "
            f"format={header['format_code']}, "
            f"points={frame['point_count']}"
        )


def collect_points(frame):
    return [(record["x"], record["y"]) for record in frame["records"]]


def compute_bounds(points):
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)

    if min_x == max_x:
        max_x += 1
    if min_y == max_y:
        max_y += 1

    return min_x, max_x, min_y, max_y


def create_canvas():
    canvas = np.full((CANVAS_SIZE, CANVAS_SIZE, 3), 248, dtype=np.uint8)
    cv2.rectangle(
        canvas,
        (0, 0),
        (CANVAS_SIZE - 1, CANVAS_SIZE - 1),
        (220, 220, 220),
        2,
    )
    return canvas


def project_point(point, bounds):
    min_x, max_x, min_y, max_y = bounds
    usable = CANVAS_SIZE - 2 * MARGIN
    scale = min(usable / (max_x - min_x), usable / (max_y - min_y))
    used_width = (max_x - min_x) * scale
    used_height = (max_y - min_y) * scale
    offset_x = (CANVAS_SIZE - used_width) / 2.0
    offset_y = (CANVAS_SIZE - used_height) / 2.0

    x = int(round(offset_x + (point[0] - min_x) * scale))
    y = int(round(offset_y + (max_y - point[1]) * scale))
    return x, y


def render_frame_preview(frame, summary):
    points = collect_points(frame)
    bounds = compute_bounds(points)
    canvas = create_canvas()

    header = frame["header"]
    info_lines = [
        "ILDA Preview",
        f"Frame: {header['frame_number']} / {header['total_frames']}",
        f"Name: {header['frame_name'] or '<unnamed>'}",
        f"Company: {header['company_name'] or '<unknown>'}",
        f"Points: {len(frame['records'])}",
    ]

    y = 30
    for index, line in enumerate(info_lines):
        cv2.putText(
            canvas,
            line,
            (20, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (30, 30, 30),
            2 if index == 0 else 1,
            cv2.LINE_AA,
        )
        y += 26

    last_visible = None
    for record in frame["records"]:
        point = project_point((record["x"], record["y"]), bounds)
        if not record["blanked"]:
            cv2.circle(canvas, point, 2, (255, 80, 80), -1)
            if last_visible is not None:
                cv2.line(canvas, last_visible, point, (255, 180, 0), 1, cv2.LINE_AA)
            last_visible = point
        else:
            last_visible = None

    return canvas


def build_output_path(prefix, suffix, output_dir):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return output_dir / f"{prefix}_{timestamp}.{suffix}"


def save_summary_json(summary, input_path, output_dir):
    output_path = build_output_path(input_path.stem, "json", output_dir)
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[INFO] 已保存 ILDA 摘要 JSON: {output_path}")


def save_preview_image(image, input_path, output_dir):
    output_path = build_output_path(f"{input_path.stem}_preview", "png", output_dir)
    success = cv2.imwrite(str(output_path), image)
    if not success:
        raise RuntimeError(f"保存 ILDA 预览图失败: {output_path}")
    print(f"[INFO] 已保存 ILDA 预览图: {output_path}")


def show_preview(image, input_path, output_dir):
    print("[INFO] 按 'q' 退出窗口，按 's' 保存预览图。")

    while True:
        cv2.imshow(WINDOW_NAME, image)
        key = cv2.waitKey(0) & 0xFF

        if key == ord("q"):
            print("[INFO] Quit.")
            break
        if key == ord("s"):
            save_preview_image(image, input_path, output_dir)

    cv2.destroyAllWindows()


def main():
    args = parse_args()

    try:
        input_path = validate_input_path(args.input)
        output_dir = ensure_output_dir()

        frames = load_ild_file(input_path)
        summary = build_summary_payload(input_path, frames)
        print_summary(summary)
        save_summary_json(summary, input_path, output_dir)

        preview = render_frame_preview(frames[0], summary)
        save_preview_image(preview, input_path, output_dir)

        if not args.no_window:
            show_preview(preview, input_path, output_dir)

    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
