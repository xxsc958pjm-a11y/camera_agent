from dataclasses import dataclass


def filter_marker_results(results, target_marker_ids=None):
    if not target_marker_ids:
        return results

    allowed_ids = set(target_marker_ids)
    return [result for result in results if result["id"] in allowed_ids]


def estimate_marker_pose(
    marker_result,
    marker_size_mm=None,
    camera_matrix=None,
    dist_coeffs=None,
):
    if marker_size_mm is None or camera_matrix is None or dist_coeffs is None:
        return None

    # TODO: wire this into cv2.solvePnP once calibration data is available.
    return None


def attach_pose_estimates(
    results,
    marker_size_mm=None,
    camera_matrix=None,
    dist_coeffs=None,
):
    enriched_results = []
    for result in results:
        enriched_result = dict(result)
        enriched_result["pose"] = estimate_marker_pose(
            marker_result=result,
            marker_size_mm=marker_size_mm,
            camera_matrix=camera_matrix,
            dist_coeffs=dist_coeffs,
        )
        enriched_results.append(enriched_result)
    return enriched_results


@dataclass
class MarkerTrackState:
    count: int
    center: tuple[int, int]
    stable_announced: bool


class StableMarkerTracker:
    def __init__(self, min_stable_frames=3, max_center_jump_px=30):
        self.min_stable_frames = min_stable_frames
        self.max_center_jump_px = max_center_jump_px
        self._states = {}

    def update(self, results):
        next_states = {}
        stable_results = []
        events = []

        for result in results:
            marker_id = result["id"]
            center = tuple(result["center"])
            previous_state = self._states.get(marker_id)

            if previous_state and _center_distance(center, previous_state.center) <= self.max_center_jump_px:
                count = previous_state.count + 1
            else:
                count = 1

            stable_announced = False
            if count == 1:
                events.append(f"[INFO] 单帧检测到 ID {marker_id}")

            if count >= self.min_stable_frames:
                stable_results.append(result)
                if not previous_state or not previous_state.stable_announced:
                    events.append(
                        f"[INFO] 稳定检测到 ID {marker_id}，连续 {count} 帧"
                    )
                stable_announced = True

            next_states[marker_id] = MarkerTrackState(
                count=count,
                center=center,
                stable_announced=stable_announced,
            )

        self._states = next_states
        return stable_results, events


def _center_distance(center_a, center_b):
    dx = center_a[0] - center_b[0]
    dy = center_a[1] - center_b[1]
    return (dx * dx + dy * dy) ** 0.5
