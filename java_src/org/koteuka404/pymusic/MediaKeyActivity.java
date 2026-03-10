package org.koteuka404.pymusic;

import android.content.Intent;
import android.view.KeyEvent;
import org.kivy.android.PythonActivity;

public class MediaKeyActivity extends PythonActivity {
    private void dispatchAction(String action) {
        Intent i = new Intent(this, this.getClass());
        i.setAction(action);
        i.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        startActivity(i);
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
