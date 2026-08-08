"""Small visual tuning layer for the player like stats row.

Uses the original Material Design Icons ``thumb-up-outline`` SVG asset for the
thumb geometry. The SVG path is parsed locally so the Android build does not
rely on KivyMD icon-font sizing, which was ignoring the requested visual size.
"""
from __future__ import annotations

import math
import os
import re
import xml.etree.ElementTree as ET

from kivy.graphics import Color, Line
from kivy.metrics import dp
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget


_SVG_ICON_PATH = os.path.join(os.path.dirname(__file__), "ico", "thumb-up-outline.svg")
_PATH_TOKEN_RE = re.compile(
    r"[AaCcHhLlMmVvZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
)


def _angle_between(ux: float, uy: float, vx: float, vy: float) -> float:
    dot = ux * vx + uy * vy
    det = ux * vy - uy * vx
    return math.atan2(det, dot)


def _sample_arc(x1, y1, rx, ry, rotation, large_arc, sweep, x2, y2, steps=8):
    rx = abs(float(rx))
    ry = abs(float(ry))
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [(x2, y2)]

    phi = math.radians(float(rotation) % 360.0)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)
    dx = (x1 - x2) / 2.0
    dy = (y1 - y2) / 2.0
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale

    numerator = max(
        0.0,
        rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p,
    )
    denominator = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    factor = 0.0 if denominator == 0 else math.sqrt(numerator / denominator)
    if bool(large_arc) == bool(sweep):
        factor = -factor

    cxp = factor * (rx * y1p / ry)
    cyp = factor * (-ry * x1p / rx)
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    ux = (x1p - cxp) / rx
    uy = (y1p - cyp) / ry
    vx = (-x1p - cxp) / rx
    vy = (-y1p - cyp) / ry
    theta1 = _angle_between(1.0, 0.0, ux, uy)
    delta = _angle_between(ux, uy, vx, vy)
    if not sweep and delta > 0:
        delta -= 2.0 * math.pi
    elif sweep and delta < 0:
        delta += 2.0 * math.pi

    point_count = max(3, int(abs(delta) / (2.0 * math.pi) * steps * 4.0))
    out = []
    for i in range(1, point_count + 1):
        theta = theta1 + delta * (i / point_count)
        ct = math.cos(theta)
        st = math.sin(theta)
        px = cx + cos_phi * rx * ct - sin_phi * ry * st
        py = cy + sin_phi * rx * ct + cos_phi * ry * st
        out.append((px, py))
    return out


def _load_svg_subpaths(path: str):
    try:
        root = ET.parse(path).getroot()
        path_node = next(
            (node for node in root.iter() if str(node.tag).endswith("path")), None
        )
        d = str(path_node.attrib.get("d", "")) if path_node is not None else ""
    except Exception:
        return []

    tokens = _PATH_TOKEN_RE.findall(d)
    if not tokens:
        return []

    subpaths = []
    current = []
    x = y = 0.0
    start_x = start_y = 0.0
    command = None
    i = 0

    def is_command(token):
        return len(token) == 1 and token.isalpha()

    def number():
        nonlocal i
        value = float(tokens[i])
        i += 1
        return value

    def append_point(px, py):
        nonlocal x, y
        x, y = float(px), float(py)
        current.append((x, y))

    def finish_current():
        nonlocal current
        if len(current) >= 2:
            subpaths.append(current)
        current = []

    while i < len(tokens):
        if is_command(tokens[i]):
            command = tokens[i]
            i += 1
        if command is None:
            break

        if command == "M":
            finish_current()
            x = number()
            y = number()
            start_x, start_y = x, y
            current = [(x, y)]
            command = "L"
        elif command == "L":
            append_point(number(), number())
        elif command == "H":
            append_point(number(), y)
        elif command == "V":
            append_point(x, number())
        elif command == "C":
            x1, y1 = number(), number()
            x2, y2 = number(), number()
            x3, y3 = number(), number()
            sx, sy = x, y
            for n in range(1, 9):
                t = n / 8.0
                mt = 1.0 - t
                px = (
                    mt ** 3 * sx
                    + 3.0 * mt * mt * t * x1
                    + 3.0 * mt * t * t * x2
                    + t ** 3 * x3
                )
                py = (
                    mt ** 3 * sy
                    + 3.0 * mt * mt * t * y1
                    + 3.0 * mt * t * t * y2
                    + t ** 3 * y3
                )
                current.append((px, py))
            x, y = x3, y3
        elif command == "A":
            rx, ry = number(), number()
            rotation = number()
            large_arc = int(number())
            sweep = int(number())
            x2, y2 = number(), number()
            current.extend(
                _sample_arc(x, y, rx, ry, rotation, large_arc, sweep, x2, y2)
            )
            x, y = x2, y2
        elif command == "Z":
            if current and current[-1] != (start_x, start_y):
                current.append((start_x, start_y))
            finish_current()
            x, y = start_x, start_y
            command = None
        else:
            return []

    finish_current()
    return subpaths


_SVG_SUBPATHS = _load_svg_subpaths(_SVG_ICON_PATH)


class _ThumbUpIcon(Widget):
    """Render the bundled original MDI thumb-up outline at any widget size."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._lines = []
        with self.canvas:
            self._color = Color(0.15, 0.15, 0.15, 1)
            for _subpath in _SVG_SUBPATHS:
                self._lines.append(Line(width=dp(1.45), joint="round", cap="round"))
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    def _redraw(self, *_args):
        if not _SVG_SUBPATHS:
            return
        x, y = self.pos
        w, h = self.size
        inset = min(w, h) * 0.04
        draw_w = max(1.0, w - inset * 2.0)
        draw_h = max(1.0, h - inset * 2.0)
        sx = draw_w / 24.0
        sy = draw_h / 24.0

        for line, subpath in zip(self._lines, _SVG_SUBPATHS):
            points = []
            for px, py in subpath:
                points.extend((x + inset + px * sx, y + inset + (24.0 - py) * sy))
            line.points = points


def _make_stats_widget(owner) -> FloatLayout:
    holder = FloatLayout(
        size_hint=(None, None),
        size=(dp(108), dp(46)),
    )

    icon = _ThumbUpIcon(
        size_hint=(None, None),
        size=(dp(34), dp(34)),
        pos_hint={"x": 0.0, "y": -0.05},
    )

    count_label = Label(
        text="",
        size_hint=(None, None),
        size=(dp(74), dp(28)),
        pos_hint={"x": 34.0 / 108.0, "center_y": 0.50},
        font_size="13sp",
        color=(0.15, 0.15, 0.15, 1),
        halign="left",
        valign="middle",
        shorten=True,
        shorten_from="right",
        text_size=(dp(74), dp(28)),
    )

    ratio_label = Label(
        text="",
        size_hint=(None, None),
        size=(dp(74), dp(12)),
        pos_hint={"x": 34.0 / 108.0, "y": 0.01},
        font_size="8sp",
        color=(0.48, 0.48, 0.48, 1),
        halign="left",
        valign="middle",
        text_size=(dp(74), dp(12)),
    )

    holder.add_widget(icon)
    holder.add_widget(count_label)
    holder.add_widget(ratio_label)

    holder._pymusic_like_stats = True
    holder._pymusic_count_label = count_label
    holder._pymusic_ratio_label = ratio_label
    owner._likes_holder = holder
    owner._likes_count_label = count_label
    owner._likes_ratio_label = ratio_label
    return holder


def _tune_channel_row(owner) -> None:
    try:
        channel = owner.ids.get("audio_channel")
        favorite = owner.ids.get("favorite_btn")
        repeat = owner.ids.get("repeat_inline_btn")
        avatar = owner.ids.get("channel_avatar")
        parent = getattr(favorite, "parent", None) if favorite is not None else None

        if channel is not None:
            channel.bold = True
            channel.size_hint_x = 1
            channel.shorten = True
            channel.shorten_from = "right"
            channel.text_size = (channel.width, dp(22))
            if not bool(getattr(channel, "_pymusic_width_bound", False)):
                def _sync_text_width(instance, width):
                    instance.text_size = (width, dp(22))

                channel.bind(width=_sync_text_width)
                channel._pymusic_width_bound = True

        if parent is None:
            return

        known = {channel, favorite, repeat, avatar, getattr(owner, "_likes_holder", None)}
        for child in list(getattr(parent, "children", []) or []):
            if child in known or bool(getattr(child, "_pymusic_like_stats", False)):
                continue
            if type(child) is Widget:
                child.size_hint_x = None
                child.width = 0
    except Exception:
        pass


def apply_likes_ui_tuning(likes_module) -> bool:
    if likes_module is None:
        return False
    if bool(getattr(likes_module, "_pymusic_visual_tuning_v3", False)):
        return True

    old_ensure = getattr(likes_module, "_ensure_stats_widget", None)
    if not callable(old_ensure):
        return False

    def ensure_stats_widget_tuned(owner):
        _tune_channel_row(owner)
        return old_ensure(owner)

    likes_module._make_stats_widget = _make_stats_widget
    likes_module._ensure_stats_widget = ensure_stats_widget_tuned
    likes_module._pymusic_visual_tuning_v3 = True
    return True
