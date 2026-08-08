"""Small visual tuning layer for the player like stats row.

Kept separate from the stats/network patch so layout tweaks cannot interfere
with playback or vote fetching.
"""
from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivymd.uix.label import MDIcon


def _make_stats_widget(owner) -> FloatLayout:
    # The fixed PNG action buttons in youtube_gui.kv are 28x28 and BoxLayout
    # places them at the bottom of the 46dp row. Put the Material thumb glyph on
    # that same bottom baseline, then enlarge it because the font glyph has more
    # internal whitespace than the PNG assets.
    holder = FloatLayout(
        size_hint=(None, None),
        size=(dp(104), dp(46)),
    )

    icon = MDIcon(
        icon="thumb-up-outline",
        size_hint=(None, None),
        size=(dp(30), dp(30)),
        pos_hint={"x": 0.0, "y": -0.03},
        font_size="29sp",
        theme_text_color="Custom",
        text_color=(0.15, 0.15, 0.15, 1),
        halign="center",
        valign="middle",
        text_size=(dp(30), dp(30)),
    )

    count_label = Label(
        text="",
        size_hint=(None, None),
        size=(dp(74), dp(28)),
        pos_hint={"x": 30.0 / 104.0, "center_y": 0.50},
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
        pos_hint={"x": 30.0 / 104.0, "y": 0.01},
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
    """Give the channel label the room that the old flexible spacer consumed."""
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
            # Limit the text texture to one visual line. Width is rebound below
            # so long channel names shorten instead of wrapping to a second row.
            channel.text_size = (channel.width, dp(22))
            if not bool(getattr(channel, "_pymusic_width_bound", False)):
                def _sync_text_width(instance, width):
                    instance.text_size = (width, dp(22))

                channel.bind(width=_sync_text_width)
                channel._pymusic_width_bound = True

        if parent is None:
            return

        known = {channel, favorite, repeat, avatar, getattr(owner, "_likes_holder", None)}
        # youtube_gui.kv contains a flexible plain Widget between the channel
        # title and the action buttons. It used to split the remaining width 50/50
        # with the channel label, which is why names such as "Grimwin d" wrapped.
        for child in list(getattr(parent, "children", []) or []):
            if child in known or bool(getattr(child, "_pymusic_like_stats", False)):
                continue
            if type(child) is Widget:
                child.size_hint_x = None
                child.width = 0
    except Exception:
        pass


def apply_likes_ui_tuning(likes_module) -> bool:
    """Replace only the visual hooks used by likes_ui_patch before it installs."""
    if likes_module is None:
        return False
    if bool(getattr(likes_module, "_pymusic_visual_tuning_v1", False)):
        return True

    old_ensure = getattr(likes_module, "_ensure_stats_widget", None)
    if not callable(old_ensure):
        return False

    def ensure_stats_widget_tuned(owner):
        _tune_channel_row(owner)
        return old_ensure(owner)

    likes_module._make_stats_widget = _make_stats_widget
    likes_module._ensure_stats_widget = ensure_stats_widget_tuned
    likes_module._pymusic_visual_tuning_v1 = True
    return True
