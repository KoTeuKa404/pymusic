package org.koteuka404.pymusic;

import android.media.MediaPlayer;
import android.os.Build;
import android.view.MotionEvent;
import android.view.View;
import android.widget.ImageButton;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Direct native transport control for the three buttons drawn over SurfaceView.
 *
 * The touch hot path must never wait for MediaPlayer. Some Android/Qualcomm
 * MediaPlayer implementations can block pause/start/seekTo for hundreds of ms
 * or even >1 s on remote streams. Therefore ACTION_DOWN updates visible/logical
 * state immediately and the potentially blocking MediaPlayer commands run on a
 * dedicated serial worker.
 */
public final class NativeTransportBridge {
    public static final int ACTION_REWIND = -1;
    public static final int ACTION_TOGGLE = 0;
    public static final int ACTION_FORWARD = 1;

    public static final int EVENT_NONE = 0;
    public static final int EVENT_PLAY = 1;
    public static final int EVENT_PAUSE = 2;
    public static final int EVENT_REWIND = 3;
    public static final int EVENT_FORWARD = 4;

    private static volatile MediaPlayer audioPlayer;
    private static volatile MediaPlayer videoPlayer;
    private static volatile boolean userPaused = false;
    private static volatile long stateVersion = 0L;
    private static volatile int lastEvent = EVENT_NONE;
    private static volatile long lastTargetMs = -1L;
    private static volatile long logicalPositionMs = -1L;

    /** One worker preserves transport command ordering without blocking touch/UI. */
    private static final ExecutorService TRANSPORT_EXECUTOR =
            Executors.newSingleThreadExecutor(r -> {
                Thread t = new Thread(r, "PyMusic-NativeTransport");
                t.setDaemon(true);
                return t;
            });

    private NativeTransportBridge() {
    }

    public static void setAudioPlayer(MediaPlayer player) {
        audioPlayer = player;
        if (player != null) {
            int pos = safePosition(player);
            if (pos >= 0) logicalPositionMs = pos;
        }
    }

    public static void setVideoPlayer(MediaPlayer player) {
        videoPlayer = player;
    }

    public static boolean isUserPaused() {
        return userPaused;
    }

    public static long getStateVersion() {
        return stateVersion;
    }

    public static int getLastEvent() {
        return lastEvent;
    }

    public static long getLastTargetMs() {
        return lastTargetMs;
    }

    public static void markPlaying() {
        userPaused = false;
    }

    public static void markPaused() {
        userPaused = true;
    }

    public static void bindTransportButton(final ImageButton button, final int action) {
        if (button == null) {
            return;
        }
        button.setClickable(true);
        button.setFocusable(false);
        button.setOnTouchListener(new View.OnTouchListener() {
            @Override
            public boolean onTouch(View view, MotionEvent event) {
                if (event == null) {
                    return true;
                }
                final int masked = event.getActionMasked();
                if (masked == MotionEvent.ACTION_DOWN) {
                    view.setPressed(true);
                    if (action == ACTION_REWIND) {
                        seekBy(-10_000L);
                    } else if (action == ACTION_FORWARD) {
                        seekBy(10_000L);
                    } else {
                        toggle(button);
                    }
                    return true;
                }
                if (masked == MotionEvent.ACTION_UP || masked == MotionEvent.ACTION_CANCEL) {
                    view.setPressed(false);
                    return true;
                }
                return true;
            }
        });
    }

    private static boolean safeIsPlaying(MediaPlayer player) {
        if (player == null) {
            return false;
        }
        try {
            return player.isPlaying();
        } catch (Throwable ignored) {
            return false;
        }
    }

    private static int safePosition(MediaPlayer player) {
        if (player == null) {
            return -1;
        }
        try {
            return Math.max(0, player.getCurrentPosition());
        } catch (Throwable ignored) {
            return -1;
        }
    }

    private static void safePause(MediaPlayer player) {
        if (player == null) return;
        try {
            if (player.isPlaying()) player.pause();
        } catch (Throwable ignored) {
        }
    }

    private static void safeStart(MediaPlayer player) {
        if (player == null) return;
        try {
            if (!player.isPlaying()) player.start();
        } catch (Throwable ignored) {
        }
    }

    private static void safeMute(MediaPlayer player) {
        if (player == null) return;
        try {
            player.setVolume(0.0f, 0.0f);
        } catch (Throwable ignored) {
        }
    }

    private static void safeUnmute(MediaPlayer player) {
        if (player == null) return;
        try {
            player.setVolume(1.0f, 1.0f);
        } catch (Throwable ignored) {
        }
    }

    private static void safeSeekFast(MediaPlayer player, long targetMs) {
        if (player == null) return;
        final int target = (int) Math.max(0L, targetMs);
        try {
            if (Build.VERSION.SDK_INT >= 26) {
                // For interactive +/-10 s controls a nearby sync frame responds
                // much faster than an exact decoder seek on remote YouTube media.
                player.seekTo(target, MediaPlayer.SEEK_CLOSEST_SYNC);
            } else {
                player.seekTo(target);
            }
        } catch (Throwable ignored) {
            try {
                player.seekTo(target);
            } catch (Throwable ignoredAgain) {
            }
        }
    }

    private static synchronized void toggle(final ImageButton sourceButton) {
        final MediaPlayer audio = audioPlayer;
        final MediaPlayer video = videoPlayer;
        final boolean playing = safeIsPlaying(audio) || safeIsPlaying(video);

        if (playing) {
            // Capture the logical pause point before the worker can be delayed.
            int pos = safePosition(audio);
            if (pos < 0) pos = safePosition(video);
            final long pauseAt = Math.max(0L, pos);
            logicalPositionMs = pauseAt;

            // Immediate perceived response: icon + mute happen before any possibly
            // blocking pause() call. Video is already muted by the Python player.
            userPaused = true;
            lastEvent = EVENT_PAUSE;
            lastTargetMs = pauseAt;
            stateVersion++;
            try {
                if (sourceButton != null) {
                    sourceButton.setImageResource(android.R.drawable.ic_media_play);
                    sourceButton.invalidate();
                }
            } catch (Throwable ignored) {
            }
            safeMute(audio);

            TRANSPORT_EXECUTOR.execute(() -> {
                safePause(video);
                safePause(audio);
                // If pause() itself took time, return both players to the point
                // where the user actually touched the button.
                safeSeekFast(audio, pauseAt);
                safeSeekFast(video, pauseAt);
            });
        } else {
            userPaused = false;
            lastEvent = EVENT_PLAY;
            lastTargetMs = logicalPositionMs;
            stateVersion++;
            try {
                if (sourceButton != null) {
                    sourceButton.setImageResource(android.R.drawable.ic_media_pause);
                    sourceButton.invalidate();
                }
            } catch (Throwable ignored) {
            }

            TRANSPORT_EXECUTOR.execute(() -> {
                long resumeAt = logicalPositionMs;
                if (resumeAt >= 0) {
                    safeSeekFast(audio, resumeAt);
                    safeSeekFast(video, resumeAt);
                }
                safeStart(audio);
                safeStart(video);
                safeUnmute(audio);
            });
        }
    }

    private static synchronized void seekBy(long deltaMs) {
        final MediaPlayer audio = audioPlayer;
        final MediaPlayer video = videoPlayer;

        int base = safePosition(audio);
        if (base < 0) base = safePosition(video);
        if (base < 0 && logicalPositionMs >= 0) base = (int) logicalPositionMs;
        if (base < 0) base = 0;

        final long target = Math.max(0L, ((long) base) + deltaMs);
        logicalPositionMs = target;

        // Publish target immediately so Python/native progress UI can jump now;
        // the decoder seek itself is allowed to finish asynchronously.
        lastTargetMs = target;
        lastEvent = deltaMs < 0 ? EVENT_REWIND : EVENT_FORWARD;
        stateVersion++;

        TRANSPORT_EXECUTOR.execute(() -> {
            safeSeekFast(audio, target);
            safeSeekFast(video, target);
        });
    }
}
