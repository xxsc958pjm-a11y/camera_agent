from __future__ import annotations

from dataclasses import dataclass
import time


@dataclass(slots=True)
class CenteringDecision:
    action: str
    pan_step: int = 0
    tilt_step: int = 0
    reason: str = ""
    error_x: float = 0.0
    error_y: float = 0.0
    raw_center: tuple[int, int] | None = None
    smoothed_center: tuple[int, int] | None = None
    stable_detect_count: int = 0
    lost_frame_count: int = 0
    using_held_target: bool = False
    dedup_skipped: bool = False
    tracking_state: str = "idle"
    jump_reset: bool = False


class ImageCenteringController:
    def __init__(
        self,
        *,
        deadband_px_x: int = 20,
        deadband_px_y: int = 20,
        medium_error_px: int = 80,
        large_error_px: int = 160,
        step_small: int = 5,
        step_medium: int = 10,
        step_large: int = 20,
        command_interval_sec: float = 0.20,
        lost_target_halt: bool = True,
        stable_detect_frames: int = 3,
        lost_target_frames: int = 1,
        hold_last_target_frames: int = 1,
        center_smoothing: str = "ema",
        center_ema_alpha: float = 0.4,
        center_jump_reset_px: float = 80.0,
        dedup_same_command_window_sec: float = 0.4,
        image_right_to_pan_sign: int = 1,
        image_up_to_tilt_sign: int = -1,
    ):
        self.deadband_px_x = deadband_px_x
        self.deadband_px_y = deadband_px_y
        self.medium_error_px = medium_error_px
        self.large_error_px = large_error_px
        self.step_small = step_small
        self.step_medium = step_medium
        self.step_large = step_large
        self.command_interval_sec = command_interval_sec
        self.lost_target_halt = lost_target_halt
        self.stable_detect_frames = max(1, int(stable_detect_frames))
        self.lost_target_frames = max(1, int(lost_target_frames))
        self.hold_last_target_frames = max(0, int(hold_last_target_frames))
        self.center_smoothing = str(center_smoothing).strip().lower()
        self.center_ema_alpha = min(1.0, max(0.0, float(center_ema_alpha)))
        self.center_jump_reset_px = max(0.0, float(center_jump_reset_px))
        self.dedup_same_command_window_sec = max(0.0, float(dedup_same_command_window_sec))
        self.image_right_to_pan_sign = _normalize_sign(image_right_to_pan_sign)
        self.image_up_to_tilt_sign = _normalize_sign(image_up_to_tilt_sign)

        self._last_command_at = 0.0
        self._lost_halt_sent = False
        self._stable_detect_count = 0
        self._target_is_stable = False
        self._lost_frame_count = 0
        self._last_stable_center: tuple[int, int] | None = None
        self._smoothed_center: tuple[float, float] | None = None
        self._last_sent_signature: tuple[int, int] | None = None
        self._last_sent_at = 0.0

    def cooldown_remaining_sec(self) -> float:
        elapsed = time.monotonic() - self._last_command_at
        return max(0.0, self.command_interval_sec - elapsed)

    def update(self, marker_center: tuple[int, int] | None, frame_size: tuple[int, int]) -> CenteringDecision:
        now = time.monotonic()
        raw_center = marker_center

        if marker_center is not None:
            self._stable_detect_count += 1
            self._lost_frame_count = 0
            self._lost_halt_sent = False

            if self._stable_detect_count < self.stable_detect_frames:
                self._target_is_stable = False
                self._smoothed_center = None
                return CenteringDecision(
                    action="idle",
                    reason=f"acquiring_target_{self._stable_detect_count}_of_{self.stable_detect_frames}",
                    raw_center=raw_center,
                    smoothed_center=None,
                    stable_detect_count=self._stable_detect_count,
                    lost_frame_count=self._lost_frame_count,
                    tracking_state="acquiring",
                )

            self._target_is_stable = True
            self._last_stable_center = marker_center
            smoothed_center, jump_reset = self._smooth_center(marker_center)
            rounded_center = self._round_center(smoothed_center)

            if now - self._last_command_at < self.command_interval_sec:
                return CenteringDecision(
                    action="wait",
                    reason="rate_limited",
                    raw_center=raw_center,
                    smoothed_center=rounded_center,
                    stable_detect_count=self._stable_detect_count,
                    lost_frame_count=self._lost_frame_count,
                    tracking_state="stable",
                    jump_reset=jump_reset,
                )

            return self._build_motion_decision(
                center=smoothed_center,
                frame_size=frame_size,
                now=now,
                raw_center=raw_center,
                using_held_target=False,
                jump_reset=jump_reset,
            )

        self._stable_detect_count = 0
        self._lost_frame_count += 1

        if (
            self._target_is_stable
            and self._last_stable_center is not None
            and self._lost_frame_count <= self.hold_last_target_frames
        ):
            held_center = self._last_stable_center
            smoothed_center, jump_reset = self._smooth_center(held_center)
            rounded_center = self._round_center(smoothed_center)

            if now - self._last_command_at < self.command_interval_sec:
                return CenteringDecision(
                    action="wait",
                    reason="rate_limited",
                    raw_center=raw_center,
                    smoothed_center=rounded_center,
                    stable_detect_count=self.stable_detect_frames,
                    lost_frame_count=self._lost_frame_count,
                    using_held_target=True,
                    tracking_state="hold",
                    jump_reset=jump_reset,
                )

            return self._build_motion_decision(
                center=smoothed_center,
                frame_size=frame_size,
                now=now,
                raw_center=raw_center,
                using_held_target=True,
                jump_reset=jump_reset,
            )

        if self._lost_frame_count > self.lost_target_frames:
            self._target_is_stable = False
            self._smoothed_center = None

            if self.lost_target_halt and not self._lost_halt_sent:
                self._last_command_at = now
                self._lost_halt_sent = True
                return CenteringDecision(
                    action="halt",
                    reason=f"target_lost_{self._lost_frame_count}_frames",
                    raw_center=raw_center,
                    smoothed_center=None,
                    stable_detect_count=0,
                    lost_frame_count=self._lost_frame_count,
                    tracking_state="lost",
                    jump_reset=False,
                )

            return CenteringDecision(
                action="idle",
                reason=f"target_lost_{self._lost_frame_count}_frames",
                raw_center=raw_center,
                smoothed_center=None,
                stable_detect_count=0,
                lost_frame_count=self._lost_frame_count,
                tracking_state="lost",
                jump_reset=False,
            )

        return CenteringDecision(
            action="idle",
            reason=f"target_missing_{self._lost_frame_count}_frames",
            raw_center=raw_center,
            smoothed_center=self.current_smoothed_center(),
            stable_detect_count=0,
            lost_frame_count=self._lost_frame_count,
            tracking_state="searching",
            jump_reset=False,
        )

    def current_smoothed_center(self) -> tuple[int, int] | None:
        return self._round_center(self._smoothed_center)

    def _build_motion_decision(
        self,
        *,
        center: tuple[float, float],
        frame_size: tuple[int, int],
        now: float,
        raw_center: tuple[int, int] | None,
        using_held_target: bool,
        jump_reset: bool,
    ) -> CenteringDecision:
        width, height = frame_size
        cx, cy = center
        ex = float(cx - (width / 2.0))
        ey = float(cy - (height / 2.0))

        pan_step = self._axis_step(
            error_px=ex,
            deadband_px=self.deadband_px_x,
            medium_px=self.medium_error_px,
            large_px=self.large_error_px,
            sign_multiplier=self.image_right_to_pan_sign,
        )
        tilt_step = self._axis_step(
            error_px=-ey,
            deadband_px=self.deadband_px_y,
            medium_px=self.medium_error_px,
            large_px=self.large_error_px,
            sign_multiplier=self.image_up_to_tilt_sign,
        )

        rounded_center = self._round_center(center)
        tracking_state = "hold" if using_held_target else "stable"

        if using_held_target:
            return CenteringDecision(
                action="hold",
                pan_step=pan_step,
                tilt_step=tilt_step,
                reason="last_target",
                error_x=ex,
                error_y=ey,
                raw_center=raw_center,
                smoothed_center=rounded_center,
                stable_detect_count=max(self._stable_detect_count, self.stable_detect_frames),
                lost_frame_count=self._lost_frame_count,
                using_held_target=True,
                tracking_state=tracking_state,
                jump_reset=jump_reset,
            )

        if pan_step == 0 and tilt_step == 0:
            self._last_command_at = now
            return CenteringDecision(
                action="halt",
                reason="inside_deadband",
                error_x=ex,
                error_y=ey,
                raw_center=raw_center,
                smoothed_center=rounded_center,
                stable_detect_count=max(self._stable_detect_count, self.stable_detect_frames),
                lost_frame_count=self._lost_frame_count,
                using_held_target=using_held_target,
                tracking_state=tracking_state,
                jump_reset=jump_reset,
            )

        command_signature = (pan_step, tilt_step)
        if (
            self._last_sent_signature == command_signature
            and (now - self._last_sent_at) < self.dedup_same_command_window_sec
        ):
            return CenteringDecision(
                action="idle",
                reason="dedup_same_command",
                pan_step=pan_step,
                tilt_step=tilt_step,
                error_x=ex,
                error_y=ey,
                raw_center=raw_center,
                smoothed_center=rounded_center,
                stable_detect_count=max(self._stable_detect_count, self.stable_detect_frames),
                lost_frame_count=self._lost_frame_count,
                using_held_target=using_held_target,
                dedup_skipped=True,
                tracking_state=tracking_state,
                jump_reset=jump_reset,
            )

        self._last_command_at = now
        self._last_sent_signature = command_signature
        self._last_sent_at = now
        return CenteringDecision(
            action="move",
            pan_step=pan_step,
            tilt_step=tilt_step,
            reason="target_offset",
            error_x=ex,
            error_y=ey,
            raw_center=raw_center,
            smoothed_center=rounded_center,
            stable_detect_count=max(self._stable_detect_count, self.stable_detect_frames),
            lost_frame_count=self._lost_frame_count,
            using_held_target=using_held_target,
            tracking_state=tracking_state,
            jump_reset=jump_reset,
        )

    def _smooth_center(self, marker_center: tuple[int, int]) -> tuple[tuple[float, float], bool]:
        if self.center_smoothing != "ema":
            self._smoothed_center = (float(marker_center[0]), float(marker_center[1]))
            return self._smoothed_center, False

        if self._smoothed_center is None:
            self._smoothed_center = (float(marker_center[0]), float(marker_center[1]))
            return self._smoothed_center, False

        prev_x, prev_y = self._smoothed_center
        cur_x, cur_y = float(marker_center[0]), float(marker_center[1])
        if (
            abs(cur_x - prev_x) > self.center_jump_reset_px
            or abs(cur_y - prev_y) > self.center_jump_reset_px
        ):
            self._smoothed_center = (cur_x, cur_y)
            return self._smoothed_center, True

        alpha = self.center_ema_alpha
        self._smoothed_center = (
            alpha * cur_x + (1.0 - alpha) * prev_x,
            alpha * cur_y + (1.0 - alpha) * prev_y,
        )
        return self._smoothed_center, False

    def _round_center(self, center: tuple[float, float] | None) -> tuple[int, int] | None:
        if center is None:
            return None
        return (int(round(center[0])), int(round(center[1])))

    def _axis_step(
        self,
        *,
        error_px: float,
        deadband_px: int,
        medium_px: int,
        large_px: int,
        sign_multiplier: int,
    ) -> int:
        magnitude = abs(error_px)
        if magnitude <= deadband_px:
            return 0
        if magnitude >= large_px:
            base_step = self.step_large
        elif magnitude >= medium_px:
            base_step = self.step_medium
        else:
            base_step = self.step_small

        error_sign = 1 if error_px > 0 else -1
        return error_sign * sign_multiplier * base_step


def _normalize_sign(value: int) -> int:
    return 1 if int(value) >= 0 else -1
