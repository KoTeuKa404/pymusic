package org.koteuka404.pymusic;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.view.KeyEvent;
import org.kivy.android.PythonActivity;

public class MediaButtonReceiver extends BroadcastReceiver {
    private static MediaButtonReceiver instance;

    public static void register(Context context) {
        if (instance != null) return;
        instance = new MediaButtonReceiver();
        IntentFilter f = new IntentFilter(Intent.ACTION_MEDIA_BUTTON);
        f.setPriority(1000);
        context.registerReceiver(instance, f);
    }

    public static void unregister(Context context) {
        if (instance == null) return;
        try {
            context.unregisterReceiver(instance);
        } catch (Exception ignored) {
        }
        instance = null;
    }

    private static void dispatchAction(Context context, String action) {
        try {
            if (PythonActivity.mActivity instanceof MediaKeyActivity) {
                ((MediaKeyActivity) PythonActivity.mActivity).dispatchMediaAction(action);
                return;
            }
        } catch (Exception ignored) {
        }
        Intent i = new Intent(context, MediaKeyActivity.class);
        i.setAction(action);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        context.startActivity(i);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !Intent.ACTION_MEDIA_BUTTON.equals(intent.getAction())) return;
        KeyEvent event = intent.getParcelableExtra(Intent.EXTRA_KEY_EVENT);
        if (event == null) return;
        if (event.getAction() != KeyEvent.ACTION_DOWN || event.getRepeatCount() != 0) return;

        switch (event.getKeyCode()) {
            case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE:
            case KeyEvent.KEYCODE_HEADSETHOOK:
                dispatchAction(context, "org.koteuka404.pymusic.TOGGLE");
                abortBroadcast();
                break;
            case KeyEvent.KEYCODE_MEDIA_PLAY:
                dispatchAction(context, "org.koteuka404.pymusic.PLAY");
                abortBroadcast();
                break;
            case KeyEvent.KEYCODE_MEDIA_PAUSE:
                dispatchAction(context, "org.koteuka404.pymusic.PAUSE");
                abortBroadcast();
                break;
            case KeyEvent.KEYCODE_MEDIA_NEXT:
                dispatchAction(context, "org.koteuka404.pymusic.NEXT");
                abortBroadcast();
                break;
            case KeyEvent.KEYCODE_MEDIA_PREVIOUS:
                dispatchAction(context, "org.koteuka404.pymusic.PREV");
                abortBroadcast();
                break;
            default:
                break;
        }
    }
}
