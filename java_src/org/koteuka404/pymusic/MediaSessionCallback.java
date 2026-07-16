package org.koteuka404.pymusic;

import android.content.Intent;
import android.media.session.MediaSession;
import org.kivy.android.PythonActivity;

public class MediaSessionCallback extends MediaSession.Callback {
    private final PythonActivity activity;

    public MediaSessionCallback(PythonActivity activity) {
        this.activity = activity;
    }

    private void dispatchAction(String action) {
        if (activity == null) return;
        try {
            if (activity instanceof MediaKeyActivity) {
                ((MediaKeyActivity) activity).dispatchMediaAction(action);
                return;
            }
        } catch (Exception ignored) {
        }
        Intent i = new Intent(activity, activity.getClass());
        i.setAction(action);
        i.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_NEW_TASK);
        activity.startActivity(i);
    }

    private void dispatchSeekTo(long posMs) {
        if (activity == null) return;
        Intent i = new Intent(activity, activity.getClass());
        i.setAction("org.koteuka404.pymusic.SEEK");
        i.putExtra("seek_to_ms", posMs);
        i.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_NEW_TASK);
        activity.startActivity(i);
    }

    @Override
    public void onPlay() {
        dispatchAction("org.koteuka404.pymusic.PLAY");
    }

    @Override
    public void onPause() {
        dispatchAction("org.koteuka404.pymusic.PAUSE");
    }

    @Override
    public void onSkipToNext() {
        dispatchAction("org.koteuka404.pymusic.NEXT");
    }

    @Override
    public void onSkipToPrevious() {
        dispatchAction("org.koteuka404.pymusic.PREV");
    }

    @Override
    public void onSeekTo(long pos) {
        dispatchSeekTo(pos);
    }
}
