package org.koteuka404.pymusic;

import android.media.MediaPlayer;
import android.os.Build;
import android.os.SystemClock;
import android.view.MotionEvent;
import android.view.View;

/**
 * Transport control that never crosses into Python on the touch hot path.
 *
 * Python periodically publishes the current audio/video MediaPlayer references.
 * Native ImageButtons call handleAction() directly on ACTION_DOWN, so pause,
 * resume and seek do not wait for PyJNIus, the Python GIL or Kivy's Clock.
 */
public final class NativeTransportController {
    public static final int ACTION_REWIND = 0;
    public static final int ACTION_TOGGLE = 1;
    public static final int ACTION_FORWARD = 2;

    private static volatile MediaPlayer audioPlayer;
    private static volatile MediaPlayer videoPlayer;
    private static volatile long lastActionUptimeMs = 0L;
    private static volatile int lastAction = -1;

    private NativeTransportController() {}

    public static void setAudioPlayer(MediaPlayer player) {
        audioPlayer = player;
    }

    public static void setVideoPlayer(MediaPlayer player) {
        videoPlayer = player;
    }

    public static void clearAudioPlayer(MediaPlayer player) {
        if (audioPlayer == player) {
            audioPlayer = null;
        }
    }

    public static void clearVideoPlayer(MediaPlayer player) {
        if (videoPlayer == player) {
            videoPlayer = null;
        }
    }

    public static boolean isAudioPlaying() {
        MediaPlayer p = audioPlayer;
        if (p == null) return false;
        try {
            return p.isPlaying();
        } catch (Throwable ignored) {
            return false;
        }
    }

    public static int getAudioPosition() {
        MediaPlayer p = audioPlayer;
        if (p == null) return 0;
        try {
            return Math.max(0, p.getCurrentPosition());
        } catch (Throwable ignored) {
            return 0;
        }
    }

    public static boolean handleAction(int action) {
        final long now = SystemClock.uptimeMillis();
        if (action == lastAction && now - lastActionUptimeMs < 120L) {
            return true;
        }
        lastAction = action;
        lastActionUptimeMs = now;

        final MediaPlayer audio = audioPlayer;
        final MediaPlayer video = videoPlayer;

        try {
            if (action == ACTION_TOGGLE) {
                if (audio == null) return false;
                boolean playing = false;
                try {
                    playing = audio.isPlaying();
                } catch (Throwable ignored) {}

                if (playing) {
                    try { audio.pause(); } catch (Throwable ignored) {}
                    if (video != null) {
                        try {
                            if (video.isPlaying()) video.pause();
                        } catch (Throwable ignored) {}
                    }
                } else {
                    try { audio.start(); } catch (Throwable ignored) {}
                    if (video != null) {
                        try {
                            if (!video.isPlaying()) video.start();
                        } catch (Throwable ignored) {}
                    }
                }
                return true;
            }

            if (audio == null) return false;
            int current = 0;
            try { current = audio.getCurrentPosition(); } catch (Throwable ignored) {}
            int delta = action == ACTION_REWIND ? -10000 : 10000;
            int target = Math.max(0, current + delta);
            try {
                if (Build.VERSION.SDK_INT >= 26) {
                    audio.seekTo(target, MediaPlayer.SEEK_CLOSEST);
                } else {
                    audio.seekTo(target);
                }
            } catch (Throwable ignored) {
                try { audio.seekTo(target); } catch (Throwable ignored2) {}
            }

            if (video != null) {
                try {
                    if (Build.VERSION.SDK_INT >= 26) {
                        video.seekTo(target, MediaPlayer.SEEK_CLOSEST);
                    } else {
                        video.seekTo(target);
                    }
                } catch (Throwable ignored) {
                    try { video.seekTo(target); } catch (Throwable ignored2) {}
                }
            }
            return true;
        } catch (Throwable ignored) {
            return false;
        }
    }

    public static final class TouchListener implements View.OnTouchListener {
        private final int action;

        public TouchListener(int action) {
            this.action = action;
        }

        @Override
        public boolean onTouch(View view, MotionEvent event) {
            int masked = event.getActionMasked();
            if (masked == MotionEvent.ACTION_DOWN) {
                // The transport command runs here, synchronously in Java.
                handleAction(action);
                try {
                    view.setPressed(true);
                } catch (Throwable ignored) {}
                return true;
            }
            if (masked == MotionEvent.ACTION_UP || masked == MotionEvent.ACTION_CANCEL) {
                try {
                    view.setPressed(false);
                } catch (Throwable ignored) {}
                return true;
            }
            return true;
        }
    }
}
