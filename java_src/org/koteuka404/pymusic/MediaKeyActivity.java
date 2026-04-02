package org.koteuka404.pymusic;

import android.content.Intent;
import android.view.KeyEvent;
import org.kivy.android.PythonActivity;

public class MediaKeyActivity extends PythonActivity {
    public void dispatchMediaAction(String action) {
        if (action == null) return;
        Intent i = new Intent();
        i.setAction(action);
        try {
            setIntent(i);
        } catch (Exception ignored) {
        }
        try {
            onNewIntent(i);
        } catch (Exception ignored) {
        }
    }

    private void dispatchAction(String action) {
        dispatchMediaAction(action);
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        int keyCode = event.getKeyCode();
        if (event.getAction() == KeyEvent.ACTION_DOWN && event.getRepeatCount() == 0) {
            switch (keyCode) {
                case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE:
                case KeyEvent.KEYCODE_HEADSETHOOK:
                    dispatchAction("org.koteuka404.pymusic.TOGGLE");
                    return true;
                case KeyEvent.KEYCODE_MEDIA_PLAY:
                    dispatchAction("org.koteuka404.pymusic.PLAY");
                    return true;
                case KeyEvent.KEYCODE_MEDIA_PAUSE:
                    dispatchAction("org.koteuka404.pymusic.PAUSE");
                    return true;
                case KeyEvent.KEYCODE_MEDIA_NEXT:
                    dispatchAction("org.koteuka404.pymusic.NEXT");
                    return true;
                case KeyEvent.KEYCODE_MEDIA_PREVIOUS:
                    dispatchAction("org.koteuka404.pymusic.PREV");
                    return true;
                default:
                    break;
            }
        }
        return super.dispatchKeyEvent(event);
    }
}
