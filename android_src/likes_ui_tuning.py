"""Small visual tuning layer for the player like stats row.

The actual like icon is created by ``likes_ui_patch`` as a single KivyMD
``MDIcon``. Keep this module layout-only and apply small visual adjustments
after the stats widget has been created.
"""
from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.widget import Widget


def _tune_like_widget(owner) -> None:
    """Make the thumb icon larger/lower without replacing its renderer."""
    try:
        holder = getattr(owner, "_likes_holder", None)
        if holder is None:
            return

        holder.size_hint = (None, None)
        holder.size = (dp(106), dp(46))
        holder.pos_hint = {"center_y": 0.5}

        like_icon = None
        for child in list(getattr(holder, "children", []) or []):
            if str(getattr(child, "icon", "") or "") == "thumb-up-outline":
                like_icon = child
                break

        if like_icon is not None:
            like_icon.size_hint = (None, None)
            like_icon.size = (dp(34), dp(34))
            like_icon.font_size = "30sp"
            like_icon.text_size = (dp(34), dp(34))
            # Align the actual thumb glyph with the neighboring PNG actions.
            like_icon.pos_hint = {"x": 0.0, "center_y": 0.5}
            like_icon.halign = "center"
            like_icon.valign = "middle"

        # Place the two labels symmetrically around the thumb's midpoint.
        # Their combined visual midpoint is holder.center_y, not the count
        # line itself; this keeps the percentage from dangling underneath.
        count_label = getattr(owner, "_likes_count_label", None)
        if count_label is not None:
            count_label.size_hint = (None, None)
            count_label.size = (dp(72), dp(18))
            count_label.pos_hint = {"x": 34.0 / 106.0, "center_y": 0.68}
            count_label.text_size = (dp(72), dp(18))

        ratio_label = getattr(owner, "_likes_ratio_label", None)
        if ratio_label is not None:
            ratio_label.size_hint = (None, None)
            ratio_label.size = (dp(72), dp(14))
            ratio_label.pos_hint = {"x": 34.0 / 106.0, "center_y": 0.32}
            ratio_label.text_size = (dp(72), dp(14))
    except Exception:
        pass


def _tune_channel_row(owner) -> None:
    try:
        channel = owner.ids.get("audio_channel")
        favorite = owner.ids.get("favorite_btn")
        repeat = owner.ids.get("repeat_inline_btn")
        avatar = owner.ids.get("channel_avatar")
        parent = getattr(favorite, "parent", None) if favorite is not None else None

        # All channel-row elements share the same vertical center. Never
        # offset only the title/avatar while leaving action icons in place.
        if avatar is not None:
            try:
                avatar.pos_hint = {"center_y": 0.5}
            except Exception:
                pass

        if channel is not None:
            channel.bold = True
            channel.size_hint_x = 1
            channel.size_hint_y = None
            channel.height = dp(44)
            channel.pos_hint = {"center_y": 0.5}
            channel.shorten = True
            channel.shorten_from = "right"
            channel.halign = "left"
            channel.valign = "middle"
            channel.text_size = (channel.width, channel.height)
            if not bool(getattr(channel, "_pymusic_width_bound", False)):
                def _sync_text_width(instance, width):
                    instance.text_size = (width, instance.height)

                channel.bind(width=_sync_text_width)
                channel._pymusic_width_bound = True

        for button in (favorite, repeat):
            if button is not None:
                button.size_hint_y = None
                button.pos_hint = {"center_y": 0.5}
        _tune_like_widget(owner)

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
    """Apply layout/icon tuning without replacing the like renderer."""
    if likes_module is None:
        return False
    if bool(getattr(likes_module, "_pymusic_visual_tuning_v7", False)):
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
    likes_module._pymusic_visual_tuning_v7 = True
    return True
