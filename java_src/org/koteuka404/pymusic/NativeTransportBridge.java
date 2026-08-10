package org.koteuka404.pymusic;

import android.media.MediaPlayer;
import android.os.Build;
import android.view.MotionEvent;
import android.view.View;
import android.widget.ImageButton;

/**
 * Direct native transport control for the three buttons drawn over SurfaceView.
 *
 * The important rule here is that ACTION_DOWN never crosses PyJNIus/Python
 * before pause/start/seekTo is issued.  Python only mirrors the resulting state
 * afterwards, so a busy Kivy/GIL cannot delay the user's transport command.
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

    private NativeTransportBridge() {
    }

    public static void setAudioPlayer(MediaPlayer player) {
        audioPlayer = player;
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
        if (player == null) {
            return;
        }
        try {
            if (player.isPlaying()) {
                player.pause();
            }
        } catch (Throwable ignored) {
        }
    }

    private static void safeStart(MediaPlayer player) {
        if (player == null) {
            return;
        }
        try {
            if (!player.isPlaying()) {
                player.start();
            }
        } catch (Throwable ignored) {
        }
    }

    private static void safeSeekAudio(MediaPlayer player, long targetMs) {
        if (player == null) {
            return;
        }
        try {
            player.seekTo((int) Math.max(0L, targetMs));
        } catch (Throwable ignored) {
        }
    }

    private static void safeSeekVideo(MediaPlayer player, long targetMs) {
        if (player == null) {
            return;
        }
        final int target = (int) Math.max(0L, targetMs);
        try {
            if (Build.VERSION.SDK_INT >= 26) {
                // A sync frame lands much faster for interactive +/-10s seeking
                // than SEEK_CLOSEST, which may wait for exact decoder output.
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

    private static synchronized void toggle(ImageButton sourceButton) {
        // Video is the audible master after the muxed handoff; pause it first.
        final boolean playing = safeIsPlaying(videoPlayer) || safeIsPlaying(audioPlayer);
        if (playing) {
            safePause(videoPlayer);
            safePause(audioPlayer);
            userPaused = true;
            lastEvent = EVENT_PAUSE;
            try {
                if (sourceButton != null) {
                    sourceButton.setImageResource(android.R.drawable.ic_media_play);
                    sourceButton.invalidate();
                }
            } catch (Throwable ignored) {
            }
        } else {
            // Start the shadow audio first, then video.  If one player is not in
            // a startable state its exception is isolated and the other still runs.
            safeStart(audioPlayer);
            safeStart(videoPlayer);
            userPaused = false;
            lastEvent = EVENT_PLAY;
            try {
                if (sourceButton != null) {
                    sourceButton.setImageResource(android.R.drawable.ic_media_pause);
                    sourceButton.invalidate();
                }
            } catch (Throwable ignored) {
            }
        }
        lastTargetMs = -1L;
        stateVersion++;
    }

    private static synchronized void seekBy(long deltaMs) {
        // The visible muxed video is normally the master clock.  Prefer its
        // position; fall back to audio when video is not available/prepared.
        int base = safePosition(videoPlayer);
        if (base < 0) {
            base = safePosition(audioPlayer);
        }
        if (base < 0) {
            base = 0;
        }
        final long target = Math.max(0L, ((long) base) + deltaMs);

        // Move the visible/audible player first so feedback is immediate.
        safeSeekVideo(videoPlayer, target);
        safeSeekAudio(audioPlayer, target);

        lastTargetMs = target;
        lastEvent = deltaMs < 0 ? EVENT_REWIND : EVENT_FORWARD;
        stateVersion++;
    }
}
