"""Render recorded simulator traces without inventing intermediate states.

Pillow is optional. Install ``worldfuzz[render]`` to create a GIF and a PNG
poster from a local evidence capsule. The renderer never executes a policy.
"""

from __future__ import annotations

from bisect import bisect_right
import json
import math
from pathlib import Path
from typing import Any


WIDTH, HEIGHT = 1100, 660
BACKGROUND = "#080F1D"
PANEL = "#101C2D"
FLOOR = "#0C1727"
BORDER = "#22334B"
GRID = "#192B40"
TEXT = "#F2F6FC"
MUTED = "#94A7BF"
ROBOT = "#9DDDF8"
LIME = "#C5F76F"
AMBER = "#F5C675"
CORAL = "#FF8E83"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _distance(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:.3f} m"


def _font(ImageFont: Any, size: int, bold: bool = False) -> Any:
    suffix = "-Bold" if bold else ""
    candidates = (
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{suffix}.ttf",
        f"/usr/share/fonts/truetype/liberation2/LiberationSans{suffix or '-Regular'}.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _load_rollout(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        rollout = json.load(handle)
    if not isinstance(rollout, dict):
        raise ValueError(f"{path.name}: expected a rollout object")
    trace = rollout.get("trace")
    if not isinstance(trace, list) or not trace:
        raise ValueError(f"{path.name}: expected a nonempty recorded trace")
    times: list[float] = []
    closest: list[float | None] = []
    running_minimum = None
    for sample in trace:
        if not isinstance(sample, dict) or _number(sample.get("t")) is None:
            raise ValueError(f"{path.name}: every trace sample must have a finite time")
        time = float(sample["t"])
        if times and time < times[-1]:
            raise ValueError(f"{path.name}: trace timestamps must be ordered")
        times.append(time)
        distance = _number(sample.get("distance_m"))
        if distance is not None and sample.get("human_present", True):
            running_minimum = distance if running_minimum is None else min(running_minimum, distance)
        closest.append(running_minimum)
    rollout["_times"] = times
    rollout["_closest"] = closest
    return rollout


def render_comparison(capsule_directory: Path, output: Path) -> dict[str, Any]:
    """Create a looping GIF and closest-approach PNG using local JSON evidence.

    Frames select recorded samples at or before playback time. Distances and
    positions are never interpolated. Running minima include every recorded
    sample up to the selected time, including samples between display frames.
    The PNG selects the baseline's exact closest-approach timestamp.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("Replay rendering requires Pillow: pip install 'worldfuzz[render]'") from exc

    capsule_directory, output = Path(capsule_directory), Path(output)
    if output.suffix.lower() != ".gif":
        raise ValueError("Replay output must use a .gif filename")
    baseline = _load_rollout(capsule_directory / "baseline.json")
    hardened = _load_rollout(capsule_directory / "hardened.json")
    control = _load_rollout(capsule_directory / "control.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    poster_path = output.with_suffix(".png")
    fonts = {name: _font(ImageFont, size, bold) for name, size, bold in (
        ("title", 32, True), ("metric", 34, True), ("value", 20, True),
        ("body", 14, False), ("small", 12, False), ("small_bold", 12, True),
        ("tiny", 10, False), ("tag", 10, True),
    )}
    start_time = min(baseline["_times"][0], hardened["_times"][0])
    end_time = max(baseline["_times"][-1], hardened["_times"][-1])
    duration = max(0.125, end_time - start_time)
    frame_count = min(160, max(100, round(duration * 8)))
    frame_duration_ms = 125

    def text(draw: Any, xy: tuple[float, float], value: str, font: str = "body", color: str = TEXT) -> None:
        draw.text(xy, value, font=fonts[font], fill=color)

    def right_text(draw: Any, right: float, y: float, value: str, font: str = "body", color: str = TEXT) -> None:
        length = draw.textlength(value, font=fonts[font])
        text(draw, (right - length, y), value, font, color)

    def dashed_line(draw: Any, start: tuple[float, float], end: tuple[float, float], fill: str, width: int = 1, dash: int = 5) -> None:
        dx, dy = end[0] - start[0], end[1] - start[1]
        distance = math.hypot(dx, dy)
        if distance == 0:
            return
        for offset in range(0, int(distance), dash * 2):
            stop = min(distance, offset + dash)
            draw.line((start[0] + dx * offset / distance, start[1] + dy * offset / distance,
                       start[0] + dx * stop / distance, start[1] + dy * stop / distance), fill=fill, width=width)

    def draw_panel(canvas: Any, rollout: dict[str, Any], x: int, label: str, playback_time: float) -> None:
        draw = ImageDraw.Draw(canvas)
        panel_right = x + 514
        draw.rounded_rectangle((x, 111, panel_right, 571), radius=18, fill=PANEL, outline=BORDER)
        summary = rollout.get("summary", {})
        scenario = rollout.get("scenario", {})
        threshold = _number(scenario.get("safety_distance_m")) or 0.75
        final_minimum = _number(summary.get("minimum_distance_m"))
        final_breach = final_minimum is not None and final_minimum < threshold
        violations = summary.get("violations", [])
        violations = violations if isinstance(violations, list) else []
        other_violations = [entry for entry in violations if not isinstance(entry, dict)
                            or entry.get("monitor") != "human_distance"]
        final_color = CORAL if final_breach else LIME
        final_status = "VIOLATION RECORDED" if other_violations else (
            "THRESHOLD BREACHED" if final_breach or violations else "NO BREACH RECORDED")
        final_status_color = CORAL if violations or final_breach else LIME
        if final_minimum is None:
            final_color = MUTED
        text(draw, (x + 22, 129), label, "small_bold", MUTED)
        right_text(draw, panel_right - 22, 130, final_status, "tag", final_status_color)
        text(draw, (x + 20, 152), _distance(final_minimum), "metric", final_color)
        text(draw, (x + 23, 195), "FULL-RUN MINIMUM SEPARATION", "tag", MUTED)
        mission_complete = summary.get("mission_completed") is True
        right_text(draw, panel_right - 22, 169, "Mission completed" if mission_complete else "Mission incomplete", "small", TEXT)
        right_text(draw, panel_right - 22, 193, f"Safety threshold {threshold:.2f} m", "tiny", MUTED)

        index = max(0, bisect_right(rollout["_times"], playback_time) - 1)
        sample = rollout["trace"][index]
        closest = rollout["_closest"][index]
        distance = _number(sample.get("distance_m")) if sample.get("human_present", True) else None
        breached_so_far = closest is not None and closest < threshold
        current_breach = distance is not None and distance < threshold
        recorded_violation = any(isinstance(entry, dict)
                                 and _number(entry.get("time_s")) is not None
                                 and entry["time_s"] <= sample["t"]
                                 for entry in violations)
        other_violation_so_far = any(isinstance(entry, dict)
                                    and _number(entry.get("time_s")) is not None
                                    and entry["time_s"] <= sample["t"]
                                    for entry in other_violations)
        status_color = CORAL if breached_so_far else LIME

        world = rollout.get("world", {})
        bounds = world.get("bounds", {})
        x_min = _number(bounds.get("x_min"))
        x_max = _number(bounds.get("x_max"))
        y_min = _number(bounds.get("y_min"))
        y_max = _number(bounds.get("y_max"))
        x_min, x_max = (-1.0 if x_min is None else x_min), (13.0 if x_max is None else x_max)
        y_min, y_max = (-4.0 if y_min is None else y_min), (4.0 if y_max is None else y_max)
        if x_max <= x_min or y_max <= y_min:
            raise ValueError("World bounds must have positive width and height")
        scale = min(466 / (x_max - x_min), 266 / (y_max - y_min))
        plot_width, plot_height = (x_max - x_min) * scale, (y_max - y_min) * scale
        left, top = x + (514 - plot_width) / 2, 218 + (266 - plot_height) / 2
        right, bottom = left + plot_width, top + plot_height
        draw.rounded_rectangle((left, top, right, bottom), radius=10, fill=FLOOR, outline=GRID)

        def point(wx: float, wy: float) -> tuple[float, float]:
            return left + (wx - x_min) * scale, bottom - (wy - y_min) * scale

        def inside(wx: float | None, wy: float | None) -> bool:
            return wx is not None and wy is not None and x_min <= wx <= x_max and y_min <= wy <= y_max

        for wx in range(math.ceil(x_min), math.floor(x_max) + 1):
            sx, _ = point(wx, 0)
            draw.line((sx, top + 1, sx, bottom - 1), fill=GRID)
        for wy in range(math.ceil(y_min), math.floor(y_max) + 1):
            _, sy = point(0, wy)
            draw.line((left + 1, sy, right - 1, sy), fill=GRID)
        text(draw, (left + 11, top + 10), "RECORDED 2D WORLD", "tag", MUTED)
        right_text(draw, right - 10, top + 10, f"SAMPLE {sample['t']:.2f} s", "tag", MUTED)

        goal_x = _number(world.get("goal_x_m"))
        goal_x = 12.0 if goal_x is None else goal_x
        start_x = _number(world.get("start_x_m")) or 0.0
        if inside(start_x, 0.0) and inside(goal_x, 0.0):
            start, goal = point(start_x, 0.0), point(goal_x, 0.0)
            dashed_line(draw, start, goal, "#516077", width=2, dash=6)
            draw.ellipse((start[0] - 5, start[1] - 5, start[0] + 5, start[1] + 5), outline=MUTED, width=2)
            draw.ellipse((goal[0] - 7, goal[1] - 7, goal[0] + 7, goal[1] + 7), outline=LIME, width=2)
            draw.ellipse((goal[0] - 2, goal[1] - 2, goal[0] + 2, goal[1] + 2), fill=LIME)
            text(draw, (start[0] - 14, start[1] + 19), "START", "tiny", MUTED)
            text(draw, (goal[0] - 13, goal[1] + 19), "GOAL", "tiny", LIME)

        # Draw only actual past positions. No obstacle geometry or predicted
        # behavior is added to the evidence visualization.
        past_robot = []
        for previous in rollout["trace"][: index + 1]:
            rx, ry = _number(previous.get("robot_x")), _number(previous.get("robot_y"))
            if inside(rx, ry):
                past_robot.append(point(rx, ry))
        if len(past_robot) > 1:
            draw.line(past_robot, fill="#496F88", width=3)

        robot_x, robot_y = _number(sample.get("robot_x")), _number(sample.get("robot_y"))
        human_x, human_y = _number(sample.get("human_x")), _number(sample.get("human_y"))
        human_present = bool(sample.get("human_present", human_x is not None and human_y is not None))
        robot_point = point(robot_x, robot_y) if inside(robot_x, robot_y) else None
        human_point = point(human_x, human_y) if human_present and inside(human_x, human_y) else None
        if robot_point:
            rx, ry = robot_point
            radius = threshold * scale
            # Clip the safety disc to the recorded world rectangle.
            overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            circle_color = (255, 142, 131) if current_breach else (245, 198, 117)
            overlay_draw.ellipse((rx - radius, ry - radius, rx + radius, ry + radius),
                                 fill=(*circle_color, 22), outline=(*circle_color, 200), width=2)
            crop_box = (int(left + 1), int(top + 1), int(right), int(bottom))
            crop = overlay.crop(crop_box)
            canvas.paste(crop, crop_box[:2], crop)
            draw = ImageDraw.Draw(canvas)
            if human_point:
                dashed_line(draw, robot_point, human_point, CORAL if current_breach else "#61738B", width=1, dash=4)
            draw.rounded_rectangle((rx - 10, ry - 8, rx + 10, ry + 8), radius=4, fill=ROBOT, outline=TEXT, width=1)
            draw.rectangle((rx - 6, ry - 11, rx + 5, ry - 9), fill="#45677F")
            draw.rectangle((rx - 6, ry + 9, rx + 5, ry + 11), fill="#45677F")
            draw.polygon(((rx + 11, ry), (rx + 5, ry - 4), (rx + 5, ry + 4)), fill=TEXT)
        if human_point:
            hx, hy = human_point
            draw.ellipse((hx - 7, hy - 7, hx + 7, hy + 7), fill=CORAL, outline=TEXT, width=2)
            draw.ellipse((hx - 2, hy - 2, hx + 2, hy + 2), fill=FLOOR)
        if not human_present:
            text(draw, (left + 11, bottom - 21), "No pedestrian in this scenario", "tiny", MUTED)
        elif human_point is None:
            text(draw, (left + 11, bottom - 21), "Pedestrian outside world view", "tiny", MUTED)
        else:
            text(draw, (left + 11, bottom - 21), "1 grid square = 1 metre", "tiny", MUTED)
        status = "VIOLATION RECORDED" if other_violation_so_far else (
            "BREACH RECORDED" if breached_so_far or recorded_violation else "NO VIOLATION YET")
        right_text(draw, right - 10, bottom - 21, status, "tag",
                   CORAL if breached_so_far or recorded_violation else LIME)

        text(draw, (x + 23, 504), "SEPARATION NOW", "tag", MUTED)
        text(draw, (x + 23, 523), _distance(distance), "value", CORAL if current_breach else TEXT)
        text(draw, (x + 203, 504), "CLOSEST SAMPLE SO FAR", "tag", MUTED)
        text(draw, (x + 203, 523), _distance(closest), "value", status_color)
        speed = _number(sample.get("speed"))
        text(draw, (x + 378, 504), "ROBOT SPEED", "tag", MUTED)
        text(draw, (x + 378, 526), "—" if speed is None else f"{speed:.2f} m/s", "body", TEXT)

    def draw_frame(playback_time: float) -> Any:
        canvas = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((24, 27, 32, 63), radius=3, fill=LIME)
        text(draw, (44, 23), "WorldFuzz", "title")
        text(draw, (25, 74), "The same scenario. Two policies. Recorded execution evidence.", "body", MUTED)
        right_text(draw, 1076, 32, "SYNTHETIC SIMULATION · LOCAL EXECUTION", "small_bold", LIME)
        right_text(draw, 1076, 57, "Recorded positions · deterministic trace replay", "small", MUTED)
        draw_panel(canvas, baseline, 24, "BASELINE POLICY", playback_time)
        draw_panel(canvas, hardened, 562, "HARDENED POLICY", playback_time)
        draw = ImageDraw.Draw(canvas)
        # Legend describes rendering marks, not additional physical inputs.
        draw.rounded_rectangle((25, 590, 39, 600), radius=3, fill=ROBOT)
        text(draw, (47, 585), "Robot", "small", MUTED)
        draw.ellipse((114, 590, 125, 601), fill=CORAL)
        text(draw, (134, 585), "Pedestrian", "small", MUTED)
        draw.ellipse((226, 588, 240, 602), outline=AMBER, width=2)
        text(draw, (249, 585), "Safety boundary", "small", MUTED)
        right_text(draw, 1076, 585, f"PLAYBACK  {playback_time:05.2f} / {end_time:.2f} s", "small_bold", TEXT)
        summary = control.get("summary", {})
        completed = summary.get("mission_completed") is True
        violations = summary.get("violations")
        violation_count = len(violations) if isinstance(violations, (list, dict)) else None
        mission_text = "mission completed" if completed else "mission incomplete"
        violation_text = "no recorded violations" if violation_count == 0 else (
            f"{violation_count} recorded violation{'s' if violation_count != 1 else ''}"
            if violation_count is not None else "violation count unavailable")
        control_good = completed and violation_count == 0
        text(draw, (25, 612), "CONTROL CHECK", "tag", LIME if control_good else AMBER)
        text(draw, (143, 608), f"{mission_text} · {violation_text}", "small", MUTED)
        right_text(draw, 1076, 610, "Point-centre model · instantaneous stops", "tiny", MUTED)
        draw.rounded_rectangle((25, 640, 1075, 644), radius=2, fill=BORDER)
        progress = max(0.0, min(1.0, (playback_time - start_time) / duration))
        if progress > 0:
            draw.rounded_rectangle((25, 640, 25 + progress * 1050, 644), radius=2, fill=LIME)
        return canvas

    samples_with_distance = [sample for sample in baseline["trace"]
                             if _number(sample.get("distance_m")) is not None
                             and sample.get("human_present", True)]
    poster_time = (float(min(samples_with_distance, key=lambda sample: sample["distance_m"])["t"])
                   if samples_with_distance else end_time)
    draw_frame(poster_time).save(poster_path, format="PNG", optimize=True)

    # One global palette prevents small frame-to-frame color changes in GIFs.
    palette_source = draw_frame(poster_time).quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    frames = []
    for frame_index in range(frame_count):
        playback_time = start_time + (end_time - start_time) * frame_index / (frame_count - 1)
        frame = draw_frame(playback_time).quantize(palette=palette_source, dither=Image.Dither.NONE)
        frames.append(frame)
    # GIF time units are 10 ms. Alternating durations preserve a true 8 fps
    # average instead of silently truncating every 125 ms frame to 120 ms.
    frame_durations = [120 if index % 2 == 0 else 130 for index in range(frame_count)]
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:],
                   duration=frame_durations, loop=0, disposal=2, optimize=False)
    return {
        "gif_path": str(output.resolve()),
        "poster_path": str(poster_path.resolve()),
        "width": WIDTH,
        "height": HEIGHT,
        "frames": frame_count,
        "fps": 8,
        "frame_duration_ms": frame_duration_ms,
        "playback_duration_s": sum(frame_durations) / 1000,
        "trace_start_s": start_time,
        "trace_end_s": end_time,
        "poster_time_s": poster_time,
        "provenance": "synthetic simulation; local execution; recorded samples only",
    }
