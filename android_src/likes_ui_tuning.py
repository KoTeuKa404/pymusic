"""Small visual tuning layer for the player like stats row.

The actual like icon is created by ``likes_ui_patch`` as a single KivyMD
``MDIcon``.  Keep this module layout-only: the previous custom SVG path renderer
converted a filled compound MDI path into several stroked ``Line`` objects,
which could make the thumb look like multiple icons were drawn on top of each
other on Android.
"""
from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.widget import Widget


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
            # The KV row contains a flexible spacer. Collapse only anonymous
            # plain Widgets so the action buttons and like-stat holder cannot
            # overlap due to competing width hints.
            if type(child) is Widget:
                child.size_hint_x = None
                child.width = 0
    except Exception:
        pass


def apply_likes_ui_tuning(likes_module) -> bool:
    """Apply layout tuning without replacing the like icon renderer."""
    if likes_module is None:
        return False
    if bool(getattr(likes_module, "_pymusic_visual_tuning_v4", False)):
        return True

    old_ensure = getattr(likes_module, "_ensure_stats_widget", None)
    if not callable(old_ensure):
        return False

    def ensure_stats_widget_tuned(owner):
        _tune_channel_row(owner)
        result = old_ensure(owner)
        _tune_channel_row(owner)
        return result

    likes_module._ensure_stats_widget = ensure_stats_widget_tuned
    likes_module._pymusic_visual_tuning_v4 = True
    return True
