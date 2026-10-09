"""Small, persistent in-app UI presets for the PyMusic player header.

Only changes presentation metrics. Does not replace widgets, patch callbacks,
reparent ScrollViews or touch video playback.
"""
from __future__ import annotations

import json
import os

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup


STYLES = {
    "classic": {
        "name": "Класичний",
        "description": "Збалансовані розміри та відступи",
        "row": 60, "avatar": 44, "icon": 27, "gap": 8,
        "channel_font": 15, "title_font": 20, "views_font": 14,
        "stats_width": 105, "heading_font": 17,
        "channel_color": (0.15, 0.15, 0.15, 1),
    },
    "compact": {
        "name": "Компактний",
        "description": "Менше відступів, більше місця для тексту",
        "row": 50, "avatar": 36, "icon": 23, "gap": 5,
        "channel_font": 14, "title_font": 18, "views_font": 12,
        "stats_width": 90, "heading_font": 15,
        "channel_color": (0.15, 0.15, 0.15, 1),
    },
    "comfortable": {
        "name": "Просторий",
        "description": "Більші ава, іконки та підписи",
        "row": 70, "avatar": 50, "icon": 30, "gap": 9,
        "channel_font": 16, "title_font": 22, "views_font": 14,
        "stats_width": 112, "heading_font": 18,
        "channel_color": (0.08, 0.25, 0.52, 1),
    },
}


def _settings_path():
    try:
        directory = App.get_running_app().user_data_dir
    except Exception:
        directory = os.getcwd()
    return os.path.join(directory, "player_ui_style.json")


def get_saved_style():
    try:
        with open(_settings_path(), "r", encoding="utf-8") as f:
            name = json.load(f).get("player_ui_style", "classic")
        return name if name in STYLES else "classic"
    except Exception:
        return "classic"


def save_style(name):
    if name not in STYLES:
        return
    try:
        path = _settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"player_ui_style": name}, f)
    except Exception as exc:
        print("[UI-PRESETS] save error:", exc)


def apply_style(player, name=None):
    """Adjust only visible player widgets. Repeated calls are safe."""
    if player is None:
        return
    name = name if name in STYLES else get_saved_style()
    cfg = STYLES[name]
    ids = player.ids
    row = ids.get("channel_actions_row")
    if row is None:
        return

    row.size_hint_y = None
    row.height = dp(cfg["row"])
    row.spacing = dp(cfg["gap"])
    row.padding = (dp(2), dp(5), dp(2), dp(5))

    avatar = ids.get("channel_avatar")
    if avatar is not None:
        avatar.size_hint = (None, None)
        avatar.size = (dp(cfg["avatar"]), dp(cfg["avatar"]))
        avatar.pos_hint = {"center_y": .5}

    channel = ids.get("audio_channel")
    if channel is not None:
        channel.size_hint_x = 1
        channel.font_size = f'{cfg["channel_font"]}sp'
        channel.bold = True
        channel.shorten = True
        channel.text_size = (channel.width, channel.height)
        channel.color = cfg["channel_color"]

    for ident in ("repeat_inline_btn", "favorite_btn"):
        button = ids.get(ident)
        if button is not None:
            button.size_hint = (None, None)
            button.size = (dp(cfg["icon"]), dp(cfg["icon"]))
            button.pos_hint = {"center_y": .5}

    title = ids.get("audio_title")
    if title is not None:
        title.font_size = f'{cfg["title_font"]}sp'
    views = ids.get("audio_views")
    if views is not None:
        views.font_size = f'{cfg["views_font"]}sp'
    for heading in ("playlist_header", "similar_header"):
        widget = ids.get(heading)
        if widget is not None:
            widget.font_size = f'{cfg["heading_font"]}sp'

    likes = getattr(player, "_likes_holder", None)
    if likes is not None and getattr(likes, "parent", None) is not None:
        likes.size_hint = (None, None)
        likes.size = (dp(cfg["stats_width"]), dp(cfg["row"] - 10))
        likes.pos_hint = {"center_y": .5}
        available = max(45, cfg["stats_width"] - 28)
        for attr in ("_pymusic_count_label", "_pymusic_ratio_label"):
            label = getattr(likes, attr, None)
            if label is not None:
                label.width = dp(available)
                label.text_size = (dp(available), label.height)

    player._pymusic_ui_style = name
    print(f"[UI-PRESETS] applied {name}")


def bind_player(player):
    """Apply saved style now and after the likes widget is inserted."""
    if player is None:
        return
    apply_style(player)
    row = player.ids.get("channel_actions_row")
    if row is None or getattr(row, "_ui_style_bound", False):
        return

    def new_child(*_args):
        Clock.schedule_once(
            lambda _dt: apply_style(player, getattr(player, "_pymusic_ui_style", None)),
            0,
        )

    row._ui_style_bound = True
    row._ui_style_listener = new_child
    row.bind(children=new_child)


def show_style_picker(player):
    """Show three immediately applicable presets in a compact bottom-panel UI."""
    if player is None:
        return

    selected = get_saved_style()
    body = BoxLayout(orientation="vertical", spacing=dp(9), padding=dp(10))
    prompt = Label(
        text="Оформлення плеєра",
        size_hint_y=None,
        height=dp(40),
        font_size="19sp",
        bold=True,
        color=(0.12, 0.12, 0.12, 1),
    )
    body.add_widget(prompt)

    popup = Popup(
        title="UI",
        content=body,
        size_hint=(.92, None),
        height=dp(345),
        auto_dismiss=True,
        separator_height=0,
    )

    for name, cfg in STYLES.items():
        label = f'{"✓  " if name == selected else ""}{cfg["name"]}\n{cfg["description"]}'
        option = Button(
            text=label,
            size_hint_y=None,
            height=dp(68),
            font_size="14sp",
            halign="center",
            valign="middle",
            background_normal="",
            background_color=(.80, .87, 1, 1) if name == selected
                             else (.92, .94, .98, 1),
            color=(.1, .14, .2, 1),
        )
        option.bind(size=lambda obj, _size: setattr(obj, "text_size", obj.size))

        def pick(_button, chosen=name):
            save_style(chosen)
            apply_style(player, chosen)
            popup.dismiss()

        option.bind(on_release=pick)
        body.add_widget(option)

    popup.open()
