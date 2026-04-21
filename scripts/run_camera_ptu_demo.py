from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

import cv2
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.ptu_adapter import PTUAdapter, PTUAdapterError
from aruco_detect import annotate_detection_result, collect_marker_results, detect_markers, get_aruco_dictionary
from camera_source import get_camera_debug_info, open_camera, read_bgr_frame
from control.image_centering_controller import ImageCenteringController


CONFIG_PATH = PROJECT_ROOT / "config" / "ptu_bridge.yaml"
WINDOW_NAME = "Camera PTU Demo"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal image-centering demo with ArUco + PTU bridge.")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Path to bridge YAML config.")
    parser.add_argument("--execute", action="store_true", help="Actually send PTU commands.")
    return parser.parse_args()


def load_demo_config(path: str | Path) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        raise RuntimeError(f"Bridge config not found: {config_path}")
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError("Bridge config must be a mapping.")
    return payload


def main() -> int:
    args = parse_args()
    config = load_demo_config(args.config)
    camera_cfg = config["camera"]
    tracking_cfg = config["tracking"]
    direction_cfg = config["direction"]
    ptu_cfg = config["ptu"]

    adapter = PTUAdapter(args.config)
    controller = ImageCenteringController(
        deadband_px_x=int(tracking_cfg["deadband_px_x"]),
        deadband_px_y=int(tracking_cfg["deadband_px_y"]),
        medium_error_px=int(tracking_cfg["medium_error_px"]),
        large_error_px=int(tracking_cfg["large_error_px"]),
        step_small=int(tracking_cfg["step_small"]),
        step_medium=int(tracking_cfg["step_medium"]),
        step_large=int(tracking_cfg["step_large"]),
        command_interval_sec=float(tracking_cfg["command_interval_sec"]),
        lost_target_halt=bool(tracking_cfg["lost_target_halt"]),
        stable_detect_frames=int(tracking_cfg.get("stable_detect_frames", 3)),
        lost_target_frames=int(tracking_cfg.get("lost_target_frames", 1)),
        hold_last_target_frames=int(tracking_cfg.get("hold_last_target_frames", 1)),
        center_smoothing=str(tracking_cfg.get("center_smoothing", "ema")),
        center_ema_alpha=float(tracking_cfg.get("center_ema_alpha", 0.4)),
        center_jump_reset_px=float(tracking_cfg.get("center_jump_reset_px", 80.0)),
        dedup_same_command_window_sec=float(tracking_cfg.get("dedup_same_command_window_sec", 0.4)),
        image_right_to_pan_sign=int(direction_cfg["image_right_to_pan_sign"]),
        image_up_to_tilt_sign=int(direction_cfg["image_up_to_tilt_sign"]),
    )

    try:
        bridge_status = adapter.connect()
        print(f"[INFO] PTU bridge status: {bridge_status}")
    except PTUAdapterError as exc:
        print(f"[WARN] PTU bridge not ready: {exc}")

    cap = open_camera(
        camera_index=int(camera_cfg["index"]),
        width=int(camera_cfg["width"]),
        height=int(camera_cfg["height"]),
    )
    debug_info = get_camera_debug_info(cap, int(camera_cfg["index"]))
    print(f"[INFO] Camera debug: {debug_info}")
    dictionary = get_aruco_dictionary(str(camera_cfg["aruco_dictionary"]))

    requested_execute = bool(args.execute)
    bridge_execute_enabled = bool(ptu_cfg.get("execute", False))
    execute = bool(requested_execute and bridge_execute_enabled)
    target_marker_id = int(tracking_cfg["target_marker_id"])
    frame_count = 0
    start_time = time.time()
    last_action_text = "idle"
    last_debug_line = None
    last_pan_applied = 0
    last_tilt_applied = 0
    last_negative_tilt_skipped_reason = ""

    try:
        while True:
            ret, frame = read_bgr_frame(cap)
            if not ret or frame is None:
                print("[ERROR] Failed to read frame from camera.")
                break

            frame_count += 1
            corners, ids, _ = detect_markers(frame, dictionary)
            raw_results = collect_marker_results(corners, ids)
            target_results = [result for result in raw_results if result["id"] == target_marker_id]
            target_result = target_results[0] if target_results else None
            target_center = tuple(target_result["center"]) if target_result is not None else None

            decision = controller.update(
                marker_center=target_center,
                frame_size=(frame.shape[1], frame.shape[0]),
            )
            cooldown_info = adapter.negative_tilt_cooldown_info()

            debug_line = (
                f"raw={decision.raw_center} smooth={decision.smoothed_center} "
                f"stable_count={decision.stable_detect_count} lost_count={decision.lost_frame_count} "
                f"held={decision.using_held_target} state={decision.tracking_state} "
                f"jump_reset={decision.jump_reset} "
                f"decision={decision.action}:{decision.reason} "
                f"req_pan={decision.pan_step} req_tilt={decision.tilt_step} "
                f"applied_pan={last_pan_applied} applied_tilt={last_tilt_applied} "
                f"dedup={decision.dedup_skipped} "
                f"neg_tilt_cooldown={cooldown_info['active']} "
                f"neg_tilt_skip={last_negative_tilt_skipped_reason or 'none'} "
                f"execute={execute}"
            )
            if debug_line != last_debug_line:
                print(f"[INFO] target {target_marker_id} {debug_line}")
                last_debug_line = debug_line

            action_text = decision.action
            if decision.action == "move":
                action_text = f"move pan={decision.pan_step} tilt={decision.tilt_step}"
                last_pan_applied = 0
                last_tilt_applied = 0
                last_negative_tilt_skipped_reason = ""
                try:
                    if decision.pan_step != 0:
                        print(
                            f"[INFO] PTU pan command requested={decision.pan_step} execute={execute}"
                        )
                        pan_result = adapter.pan_step(decision.pan_step, execute=execute)
                        last_pan_applied = int(pan_result.get("applied_step", 0))
                        print(pan_result)
                    if decision.tilt_step != 0:
                        print(
                            f"[INFO] PTU tilt command requested={decision.tilt_step} execute={execute}"
                        )
                        tilt_result = adapter.tilt_step(decision.tilt_step, execute=execute)
                        last_tilt_applied = int(tilt_result.get("applied_step", 0))
                        last_negative_tilt_skipped_reason = str(
                            tilt_result.get("negative_tilt_skipped_reason", "")
                        )
                        print(tilt_result)
                except PTUAdapterError as exc:
                    print(f"[WARN] PTU move command failed: {exc}")
                    action_text = "ptu_error"
            elif decision.action == "hold":
                action_text = "hold:last_target"
                last_pan_applied = 0
                last_tilt_applied = 0
                last_negative_tilt_skipped_reason = ""
            elif decision.action == "halt":
                action_text = f"halt ({decision.reason})"
                last_pan_applied = 0
                last_tilt_applied = 0
                last_negative_tilt_skipped_reason = ""
                try:
                    print(f"[INFO] PTU halt requested execute={execute} reason={decision.reason}")
                    print(adapter.halt(execute=execute))
                except PTUAdapterError as exc:
                    print(f"[WARN] PTU halt failed: {exc}")
                    action_text = "ptu_error"
            elif decision.dedup_skipped:
                action_text = f"dedup skip pan={decision.pan_step} tilt={decision.tilt_step}"
                print(
                    "[INFO] PTU command dedup skipped "
                    f"raw_center={decision.raw_center} "
                    f"smoothed_center={decision.smoothed_center}"
                )
                last_negative_tilt_skipped_reason = ""

            last_action_text = action_text

            annotated = annotate_detection_result(frame, raw_results)
            display = draw_overlay(
                annotated,
                frame_count=frame_count,
                start_time=start_time,
                target_marker_id=target_marker_id,
                target_center=target_center,
                decision=decision,
                applied_pan_step=last_pan_applied,
                applied_tilt_step=last_tilt_applied,
                requested_execute=requested_execute,
                bridge_execute_enabled=bridge_execute_enabled,
                execute=execute,
                negative_tilt_cooldown_active=bool(cooldown_info["active"]),
                negative_tilt_cooldown_remaining_sec=float(cooldown_info["remaining_sec"]),
                negative_tilt_skipped_reason=last_negative_tilt_skipped_reason,
                last_action_text=last_action_text,
                cooldown_remaining_sec=controller.cooldown_remaining_sec(),
            )
            cv2.imshow(WINDOW_NAME, display)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                print("[INFO] Quit.")
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

    return 0


def draw_overlay(
    frame,
    *,
    frame_count: int,
    start_time: float,
    target_marker_id: int,
    target_center: tuple[int, int] | None,
    decision,
    applied_pan_step: int,
    applied_tilt_step: int,
    requested_execute: bool,
    bridge_execute_enabled: bool,
    execute: bool,
    negative_tilt_cooldown_active: bool,
    negative_tilt_cooldown_remaining_sec: float,
    negative_tilt_skipped_reason: str,
    last_action_text: str,
    cooldown_remaining_sec: float,
):
    display = frame.copy()
    height, width = display.shape[:2]
    cx = width // 2
    cy = height // 2
    cv2.line(display, (cx, 0), (cx, height), (255, 255, 0), 1)
    cv2.line(display, (0, cy), (width, cy), (255, 255, 0), 1)
    cv2.circle(display, (cx, cy), 8, (255, 255, 0), 2)

    elapsed = time.time() - start_time
    fps = frame_count / elapsed if elapsed > 0 else 0.0
    info_lines = [
        f"Target marker: {target_marker_id}",
        f"CLI --execute: {requested_execute}",
        f"Bridge execute enabled: {bridge_execute_enabled}",
        f"PTU execute active: {execute}",
        f"Tracking state: {decision.tracking_state}",
        f"Raw center: {decision.raw_center if decision.raw_center is not None else 'n/a'}",
        f"Smoothed center: {decision.smoothed_center if decision.smoothed_center is not None else 'n/a'}",
        f"Observed center: {target_center if target_center is not None else 'not visible'}",
        f"Jump reset: {decision.jump_reset}",
        f"Stable detect count: {decision.stable_detect_count}",
        f"Lost frames: {decision.lost_frame_count}",
        f"Held target: {decision.using_held_target}",
        f"Dedup skipped: {decision.dedup_skipped}",
        f"Suggested pan/tilt: ({decision.pan_step}, {decision.tilt_step})",
        f"Applied pan/tilt: ({applied_pan_step}, {applied_tilt_step})",
        f"Neg tilt cooldown: {negative_tilt_cooldown_active} ({negative_tilt_cooldown_remaining_sec:.2f}s)",
        f"Neg tilt skipped: {negative_tilt_skipped_reason or 'none'}",
        f"Decision: {last_action_text}",
        f"Rate limit remaining: {cooldown_remaining_sec:.2f}s",
        f"Err(x,y): ({decision.error_x:.1f}, {decision.error_y:.1f})",
        f"FPS: {fps:.2f}",
        "Press q to quit",
    ]
    y = 30
    for line in info_lines:
        cv2.putText(
            display,
            line,
            (10, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )
        y += 28
    return display


if __name__ == "__main__":
    raise SystemExit(main())
