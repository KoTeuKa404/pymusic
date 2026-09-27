"""Make the native video layer transparent to normal Kivy gestures.

The actual decoded picture is an Android SurfaceView placed above Kivy's SDL
view.  When that SurfaceView (or the full-size controls FrameLayout) is a touch
target, Android can keep the gesture on the native layer as soon as video
becomes visible.  The playlist/details below then stop receiving their normal
Kivy touch stream.

V16 uses normal Android event dispatch instead of translating MOVE events in
Python:

* SurfaceView itself has no OnTouchListener and is not clickable;
* the empty full-frame controls overlay and its transport bar are not clickable;
* the three visible transport ImageButtons keep NativeTransportBridge;
* the native SeekBar keeps its own OnSeekBarChangeListener/touch handling;
* taps/swipes on empty video pixels fall through to the existing Kivy
  VideoTapArea / UI beneath the native layer.

This is deliberately the final touch-owner patch and contains no manual
scroll_y manipulation.
"""
from __future__ import annotations

_PATCHED = False


def install_video_touch_passthrough_v16() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        import audio_screen
        import video_player

        screen_cls = getattr(audio_screen, "AudioPlayerScreen", None)
        video_cls = getattr(video_player, "AndroidVideoPlayer", None)
        if screen_cls is None or video_cls is None:
            return False

        if not bool(getattr(screen_cls, "_pymusic_playlist_single_scroll_v12", False)):
            return False
        if not bool(getattr(screen_cls, "_pymusic_native_java_transport_v1", False)):
            return False
        if not bool(getattr(video_cls, "_pymusic_native_java_transport_v1", False)):
            return False
        if not bool(getattr(screen_cls, "_pymusic_final_player_v2", False)):
            return False

        if bool(getattr(screen_cls, "_pymusic_video_touch_passthrough_v16", False)):
            _PATCHED = True
            return True

        # At this point native_java_transport_fix owns _bind_controls_tap.
        # Keep a reference so the first three ImageButtons are always rebound to
        # NativeTransportBridge before we make only the empty containers passive.
        java_bind_controls = video_cls._bind_controls_tap

        def make_surface_passive(owner) -> None:
            try:
                surface = getattr(owner, "surface_view", None)
                if surface is None:
                    return
                try:
                    surface.setOnTouchListener(None)
                except Exception:
                    pass
                try:
                    surface.setClickable(False)
                except Exception:
                    pass
                try:
                    surface.setLongClickable(False)
                except Exception:
                    pass
                try:
                    surface.setFocusable(False)
                    surface.setFocusableInTouchMode(False)
                except Exception:
                    pass
            except Exception as exc:
                try:
                    print("[VIDEO-TOUCH-V16] surface passive failed:", exc)
                except Exception:
                    pass

        def make_empty_overlay_passive(owner) -> None:
            try:
                # First let Java transport reclaim the actual buttons.
                try:
                    java_bind_controls(owner)
                except Exception as exc:
                    try:
                        print("[VIDEO-TOUCH-V16] Java button rebind failed:", exc)
                    except Exception:
                        pass

                overlay = getattr(owner, "controls_overlay", None)
                if overlay is not None:
                    try:
                        overlay.setOnTouchListener(None)
                    except Exception:
                        pass
                    try:
                        overlay.setClickable(False)
                    except Exception:
                        pass
                    try:
                        overlay.setLongClickable(False)
                    except Exception:
                        pass
                    try:
                        overlay.setFocusable(False)
                        overlay.setFocusableInTouchMode(False)
                    except Exception:
                        pass

                controls = list(
                    getattr(owner, "_controls_touch_views", []) or []
                )

                # controls[0:3] are rewind/play/forward ImageButtons and must
                # stay on NativeTransportBridge. controls[3:] currently contains
                # the surrounding LinearLayout transport bar. That container
                # must not become a full-width touch sink.
                for view in controls[3:]:
                    if view is None:
                        continue
                    try:
                        view.setOnTouchListener(None)
                    except Exception:
                        pass
                    try:
                        view.setClickable(False)
                    except Exception:
                        pass
                    try:
                        view.setLongClickable(False)
                    except Exception:
                        pass
                    try:
                        view.setFocusable(False)
                        view.setFocusableInTouchMode(False)
                    except Exception:
                        pass

                # Keep explicit child controls interactive. Java binder already
                # installed their listeners, but force clickable in case an OEM
                # or earlier patch reset the flag.
                for button in controls[:3]:
                    if button is None:
                        continue
                    try:
                        button.setClickable(True)
                    except Exception:
                        pass

                seek = getattr(owner, "_native_seek_bar", None)
                if seek is not None:
                    try:
                        seek.setClickable(True)
                        seek.setEnabled(True)
                    except Exception:
                        pass
            except Exception as exc:
                try:
                    print("[VIDEO-TOUCH-V16] overlay passive failed:", exc)
                except Exception:
                    pass

        def apply_passive_native_touch(owner) -> None:
            make_surface_passive(owner)
            make_empty_overlay_passive(owner)

        # create_surface(), set_tap_callback() and any future code calling this
        # hook can no longer re-arm a consuming SurfaceView listener.
        def bind_surface_passthrough(self):
            make_surface_passive(self)

        video_cls._bind_surface_tap = bind_surface_passthrough

        # Replace controls binding too: Java buttons first, then strip the
        # listeners from only the empty parent containers.
        def bind_controls_passthrough(self):
            make_empty_overlay_passive(self)

        video_cls._bind_controls_tap = bind_controls_passthrough

        old_apply_bounds = video_cls._apply_surface_bounds

        def apply_bounds_passthrough(self, *args, **kwargs):
            result = old_apply_bounds(self, *args, **kwargs)
            # onPrepared()/video-size changes rebuild native layout params.
            # Reassert passive touch after the real frame becomes visible.
            try:
                apply_passive_native_touch(self)
            except Exception:
                pass
            return result

        video_cls._apply_surface_bounds = apply_bounds_passthrough

        old_visible = video_cls.set_native_controls_visible

        def visible_passthrough(self, visible, *args, **kwargs):
            result = old_visible(self, visible, *args, **kwargs)
            try:
                apply_passive_native_touch(self)
            except Exception:
                pass
            return result

        video_cls.set_native_controls_visible = visible_passthrough

        old_ensure = screen_cls._ensure_video_player

        def ensure_passthrough(self, *args, **kwargs):
            result = old_ensure(self, *args, **kwargs)
            try:
                vp = getattr(self, "_video_player", None)
                if vp is not None:
                    apply_passive_native_touch(vp)
            except Exception as exc:
                try:
                    print("[VIDEO-TOUCH-V16] ensure failed:", exc)
                except Exception:
                    pass
            return result

        screen_cls._ensure_video_player = ensure_passthrough

        # If the screen/player already exists when this runtime patch arrives,
        # bind it immediately instead of waiting for another _ensure call.
        try:
            from kivy.app import App

            app = App.get_running_app()
            root = getattr(app, "root", None) if app is not None else None
            candidates = []
            if root is not None:
                try:
                    candidates.append(root.get_screen("audio"))
                except Exception:
                    pass
                try:
                    manager = getattr(root, "manager", None)
                    if manager is not None:
                        candidates.append(manager.get_screen("audio"))
                except Exception:
                    pass

            for screen in candidates:
                if screen is None:
                    continue
                vp = getattr(screen, "_video_player", None)
                if vp is not None:
                    apply_passive_native_touch(vp)
        except Exception:
            pass

        screen_cls._pymusic_video_touch_passthrough_v16 = True
        video_cls._pymusic_video_touch_passthrough_v16 = True
        _PATCHED = True
        print(
            "[VIDEO-TOUCH-V16] passive SurfaceView/overlay enabled; "
            "Kivy owns normal gestures, native child controls stay active"
        )
        return True
    except Exception as exc:
        try:
            print("[VIDEO-TOUCH-V16] install failed:", exc)
        except Exception:
            pass
        return False
