"""
Real-time wall coordinate map renderer for camera pipeline.

This module provides utilities to render a 2D wall map showing detected marker positions
in wall coordinates.
"""

import traceback

import cv2
import numpy as np


class WallMapRenderer:
    """Renders a 2D wall map with detected markers."""

    def __init__(
        self,
        wall_width_mm=1030,
        wall_height_mm=1420,
        canvas_width=800,
        canvas_height=600,
    ):
        """
        Initialize the wall map renderer.

        Args:
            wall_width_mm: Physical wall width in millimeters
            wall_height_mm: Physical wall height in millimeters
            canvas_width: Canvas width in pixels
            canvas_height: Canvas height in pixels
        """
        self.wall_width_mm = wall_width_mm
        self.wall_height_mm = wall_height_mm
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.margin = 50
        self._debug_point_types_seen = set()
        self._debug_render_logs = 0
        self._debug_exception_logs = 0

        # Compute scale
        self.compute_scale()

    def _debug_log(self, message):
        print(f"[DEBUG_WALL] {message}")

    def _debug_repr(self, value, limit=240):
        text = repr(value)
        if len(text) > limit:
            return f"{text[:limit]}..."
        return text

    def _debug_log_new_point_type(self, point, context):
        point_type = type(point)
        if point_type in self._debug_point_types_seen:
            return
        self._debug_point_types_seen.add(point_type)
        self._debug_log(
            f"renderer {context} point type={point_type} value={self._debug_repr(point)}"
        )

    def _debug_log_render_input(self, markers, reference_info):
        if self._debug_render_logs >= 3:
            return

        self._debug_log(
            "renderer input "
            f"ref={reference_info} "
            f"markers_type={type(markers)} "
            f"marker_count={len(markers)}"
        )
        for marker in list(markers)[:2]:
            marker_id = marker.get("id")
            wall_mm = marker.get("wall_mm")
            center = wall_mm.get("center") if isinstance(wall_mm, dict) else wall_mm
            corners = wall_mm.get("corners") if isinstance(wall_mm, dict) else None
            self._debug_log(
                f"renderer marker {marker_id} wall_mm type={type(wall_mm)} "
                f"value={self._debug_repr(wall_mm)}"
            )
            self._debug_log(
                f"renderer marker {marker_id} center type={type(center)} "
                f"value={self._debug_repr(center)}"
            )
            if isinstance(corners, (list, tuple)) and corners:
                self._debug_log(
                    f"renderer marker {marker_id} corner0 type={type(corners[0])} "
                    f"value={self._debug_repr(corners[0])}"
                )
        self._debug_render_logs += 1

    def _debug_log_exception(self, stage, object_name, obj):
        if self._debug_exception_logs >= 3:
            return

        self._debug_log(f"renderer exception stage={stage}")
        self._debug_log(f"renderer exception object={object_name}")
        self._debug_log(f"renderer exception object type={type(obj)}")
        self._debug_log(
            f"renderer exception object repr={self._debug_repr(obj, limit=400)}"
        )
        traceback_tail = traceback.format_exc().splitlines()[-6:]
        for line in traceback_tail:
            self._debug_log(f"renderer traceback {line}")

        self._debug_exception_logs += 1

    def compute_scale(self):
        """Compute pixel-to-mm scale for rendering."""
        usable_width = self.canvas_width - 2 * self.margin
        usable_height = self.canvas_height - 2 * self.margin

        scale_x = usable_width / self.wall_width_mm
        scale_y = usable_height / self.wall_height_mm

        # Use the smaller scale to fit the wall in the canvas
        self.scale = min(scale_x, scale_y)

    def create_canvas(self):
        """Create a blank canvas for the wall map."""
        canvas = np.full(
            (self.canvas_height, self.canvas_width, 3),
            240,
            dtype=np.uint8,
        )
        return canvas

    def normalize_point(self, point, context="point"):
        """
        Normalize a point value into an (x, y) tuple.

        Accepts either {"x": ..., "y": ...} or a 2-item tuple/list/ndarray.
        Returns None when the input is missing or invalid.
        """
        if point is None:
            return None

        self._debug_log_new_point_type(point, context)

        if isinstance(point, dict):
            x_value = point.get("x")
            y_value = point.get("y")
        elif isinstance(point, (tuple, list, np.ndarray)) and len(point) >= 2:
            x_value, y_value = point[0], point[1]
        else:
            return None

        if x_value is None or y_value is None:
            return None

        return float(x_value), float(y_value)

    def normalize_marker_center(self, marker):
        """
        Extract and normalize a marker center from supported wall map inputs.

        Supported center layouts:
        - marker["wall_mm"]["center"] = {"x": ..., "y": ...}
        - marker["wall_mm"]["center"] = (x, y) / [x, y]
        - marker["wall_mm"] = {"x": ..., "y": ...}
        - marker["wall_mm"] = (x, y) / [x, y]
        """
        wall_mm = marker.get("wall_mm")
        if wall_mm is None:
            return None

        if isinstance(wall_mm, dict):
            if "center" in wall_mm:
                return self.normalize_point(wall_mm.get("center"), "marker.center")
            return self.normalize_point(wall_mm, "marker.wall_mm")

        return self.normalize_point(wall_mm, "marker.wall_mm")

    def mm_to_canvas(self, x_mm, y_mm):
        """
        Convert wall coordinates (mm) to canvas coordinates (pixels).

        Args:
            x_mm: X coordinate in millimeters (right is positive)
            y_mm: Y coordinate in millimeters (up is positive)

        Returns:
            (canvas_x, canvas_y) tuple in pixels
        """
        # Center the wall map on the canvas
        used_width = self.wall_width_mm * self.scale
        used_height = self.wall_height_mm * self.scale
        offset_x = (self.canvas_width - used_width) / 2.0
        offset_y = (self.canvas_height - used_height) / 2.0

        # Convert to canvas coordinates
        # Note: canvas y increases downward, but wall y increases upward
        canvas_x = int(round(offset_x + x_mm * self.scale))
        canvas_y = int(round(offset_y + (self.wall_height_mm - y_mm) * self.scale))

        return canvas_x, canvas_y

    def draw_wall_outline(self, canvas):
        """Draw the wall boundary rectangle."""
        top_left = self.mm_to_canvas(0, self.wall_height_mm)
        bottom_right = self.mm_to_canvas(self.wall_width_mm, 0)

        cv2.rectangle(canvas, top_left, bottom_right, (100, 100, 100), 2)

    def draw_axes(self, canvas):
        """Draw coordinate axes and labels."""
        # Origin point
        origin = self.mm_to_canvas(0, 0)
        x_max_point = self.mm_to_canvas(self.wall_width_mm, 0)
        y_max_point = self.mm_to_canvas(0, self.wall_height_mm)

        # Draw axes
        cv2.arrowedLine(
            canvas, origin, x_max_point, (0, 100, 200), 2, tipLength=0.05
        )
        cv2.arrowedLine(
            canvas, origin, y_max_point, (0, 200, 100), 2, tipLength=0.05
        )

        # Draw axis labels
        cv2.putText(
            canvas,
            "X+ (right)",
            (x_max_point[0] + 5, origin[1] - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 100, 200),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            "Y+ (up)",
            (origin[0] - 50, y_max_point[1] - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 200, 100),
            1,
            cv2.LINE_AA,
        )

        # Draw origin label
        cv2.putText(
            canvas,
            "O(0,0)",
            (origin[0] - 30, origin[1] + 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (50, 50, 50),
            1,
            cv2.LINE_AA,
        )

    def draw_grid(self, canvas, step_mm=200):
        """Draw optional grid lines."""
        grid_color = (220, 220, 220)
        line_style = cv2.LINE_AA

        # Vertical grid lines
        for x_mm in range(0, int(self.wall_width_mm) + 1, step_mm):
            if x_mm == 0 or x_mm >= self.wall_width_mm:
                continue
            p1 = self.mm_to_canvas(x_mm, 0)
            p2 = self.mm_to_canvas(x_mm, self.wall_height_mm)
            cv2.line(canvas, p1, p2, grid_color, 1, line_style)

        # Horizontal grid lines
        for y_mm in range(0, int(self.wall_height_mm) + 1, step_mm):
            if y_mm == 0 or y_mm >= self.wall_height_mm:
                continue
            p1 = self.mm_to_canvas(0, y_mm)
            p2 = self.mm_to_canvas(self.wall_width_mm, y_mm)
            cv2.line(canvas, p1, p2, grid_color, 1, line_style)

    def draw_marker(
        self, canvas, x_mm, y_mm, marker_id, marker_size_mm=50, color=None
    ):
        """
        Draw a marker at the specified wall coordinates.

        Args:
            canvas: OpenCV image to draw on
            x_mm: X coordinate in millimeters
            y_mm: Y coordinate in millimeters
            marker_id: Marker ID for label
            marker_size_mm: Size of marker in millimeters (for drawing as square)
            color: BGR color tuple (default: blue)
        """
        if color is None:
            color = (255, 150, 0)  # Blue-orange

        # Marker center
        center = self.mm_to_canvas(x_mm, y_mm)

        # Draw marker center point
        cv2.circle(canvas, center, 5, color, -1)

        # Draw marker as a small square
        half_side = int(marker_size_mm * self.scale / 2.0)
        top_left = (center[0] - half_side, center[1] - half_side)
        bottom_right = (center[0] + half_side, center[1] + half_side)
        cv2.rectangle(canvas, top_left, bottom_right, color, 2)

        # Draw marker ID label
        cv2.putText(
            canvas,
            f"ID:{marker_id}",
            (center[0] + 10, center[1] - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )

    def draw_reference_marker(
        self, canvas, x_mm, y_mm, marker_id, marker_size_mm=50
    ):
        """
        Draw the reference marker (typically marker 37).

        Args:
            canvas: OpenCV image to draw on
            x_mm: X coordinate in millimeters
            y_mm: Y coordinate in millimeters
            marker_id: Marker ID
            marker_size_mm: Size of marker in millimeters
        """
        # Reference marker is drawn in a distinct color (green)
        self.draw_marker(
            canvas, x_mm, y_mm, marker_id, marker_size_mm, color=(0, 150, 0)
        )

    def render_wall_map(
        self,
        markers,
        reference_marker_id=None,
        marker_size_mm=50,
        reference_marker_ids=None,
    ):
        """
        Render a complete wall map with all markers.

        Args:
            markers: List of dicts with 'id', 'wall_mm' containing {'center': {'x', 'y'}}
            reference_marker_id: ID of reference marker (if any)
            marker_size_mm: Size of markers in millimeters
            reference_marker_ids: Optional list of reference marker IDs

        Returns:
            OpenCV image (numpy array) with the wall map
        """
        current_object_name = "markers"
        current_object = markers
        current_stage = "create_canvas"
        active_reference_ids = (
            set(reference_marker_ids)
            if reference_marker_ids
            else ({reference_marker_id} if reference_marker_id is not None else set())
        )
        reference_info = (
            sorted(active_reference_ids)
            if active_reference_ids
            else reference_marker_id
        )

        try:
            self._debug_log_render_input(markers, reference_info)
            canvas = self.create_canvas()
            current_object_name = "canvas"
            current_object = canvas

            # Draw wall boundaries
            current_stage = "draw_wall_outline"
            self.draw_wall_outline(canvas)

            # Draw grid (optional, subtle)
            current_stage = "draw_grid"
            self.draw_grid(canvas, step_mm=200)

            # Draw axes
            current_stage = "draw_axes"
            self.draw_axes(canvas)

            # Draw markers
            current_stage = "draw_markers"
            for index, marker in enumerate(markers):
                current_object_name = f"marker[{index}]"
                current_object = marker
                marker_id = marker.get("id")
                center = self.normalize_marker_center(marker)
                current_object_name = f"marker[{index}].center"
                current_object = center

                if center is None:
                    continue

                x_mm, y_mm = center

                # Check if it's the reference marker
                if marker_id in active_reference_ids:
                    self.draw_reference_marker(
                        canvas, x_mm, y_mm, marker_id, marker_size_mm
                    )
                else:
                    self.draw_marker(canvas, x_mm, y_mm, marker_id, marker_size_mm)

            # Draw wall info header
            current_stage = "draw_header"
            current_object_name = "reference_marker_ids"
            current_object = reference_info
            self._draw_header(canvas, reference_info, len(markers))

            return canvas
        except Exception:
            self._debug_log_exception(current_stage, current_object_name, current_object)
            raise

    def _draw_header(self, canvas, reference_info, marker_count):
        """Draw header information on the canvas."""
        header_text = f"Wall Map: {self.wall_width_mm}x{self.wall_height_mm} mm | Markers: {marker_count}"
        if reference_info:
            if isinstance(reference_info, (list, tuple, set)):
                reference_label = ",".join(str(marker_id) for marker_id in reference_info)
            else:
                reference_label = str(reference_info)
            header_text += f" | Ref: {reference_label}"

        cv2.putText(
            canvas,
            header_text,
            (10, 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )


class NoReferenceMarkerError(Exception):
    """Raised when reference marker is required but not available."""

    pass
