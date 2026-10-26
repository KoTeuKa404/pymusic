package org.koteuka404.pymusic;

import android.app.ActivityManager;
import android.app.ApplicationExitInfo;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Bundle;
import android.os.Process;
import android.view.KeyEvent;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.util.Date;
import java.util.List;
import org.kivy.android.PythonActivity;

public class MediaKeyActivity extends PythonActivity {

    private static final Object CRASH_FILE_LOCK = new Object();
    private static boolean crashHandlerInstalled = false;

    private static File diagnosticsDir(Context context) {
        File base = null;
        try {
            base = context.getExternalFilesDir(null);
        } catch (Throwable ignored) {
        }
        if (base == null) base = context.getFilesDir();
        File out = new File(base, "diagnostics");
        if (!out.exists() && !out.mkdirs() && !out.isDirectory()) {
            out = context.getFilesDir();
        }
        return out;
    }

    private static void persistAndroidReport(Context context, String filename, String body) {
        try {
            synchronized (CRASH_FILE_LOCK) {
                File dir = diagnosticsDir(context);
                File file = new File(dir, filename);
                try (FileOutputStream output = new FileOutputStream(file, true)) {
                    output.write(body.getBytes(StandardCharsets.UTF_8));
                    output.flush();
                    // Fatal reports are rare; force data to disk before Android
                    // invokes the original uncaught-exception crash handler.
                    output.getFD().sync();
                }
                File[] entries = dir.listFiles();
                if (entries != null) {
                    java.util.Arrays.sort(entries, (a, b) -> Long.compare(a.lastModified(), b.lastModified()));
                    int reports = 0;
                    for (File entry : entries) {
                        if (entry.isFile() && (entry.getName().startsWith("java_crash_")
                                || entry.getName().startsWith("android_exit_")
                                || entry.getName().startsWith("android_trace_"))) {
                            reports++;
                        }
                    }
                    for (File entry : entries) {
                        if (reports <= 40) break;
                        if (entry.isFile() && (entry.getName().startsWith("java_crash_")
                                || entry.getName().startsWith("android_exit_")
                                || entry.getName().startsWith("android_trace_"))) {
                            if (entry.delete()) reports--;
                        }
                    }
                }
            }
        } catch (Throwable ignored) {
            // Crash reporter must never prevent Android's default crash handling.
        }
    }

    private void installCrashRecorder() {
        synchronized (CRASH_FILE_LOCK) {
            if (crashHandlerInstalled) return;
            final Context context = getApplicationContext();
            final Thread.UncaughtExceptionHandler previous =
                    Thread.getDefaultUncaughtExceptionHandler();
            Thread.setDefaultUncaughtExceptionHandler((thread, error) -> {
                try {
                    StringWriter stack = new StringWriter();
                    error.printStackTrace(new PrintWriter(stack));
                    String report = "=== PYMusic Java uncaught exception ===\n"
                            + "time=" + new Date() + "\n"
                            + "pid=" + Process.myPid() + " thread=" + thread.getName() + "\n"
                            + stack.toString() + "\n";
                    persistAndroidReport(context,
                            "java_crash_" + System.currentTimeMillis() + "_" + Process.myPid() + ".log",
                            report);
                } catch (Throwable ignored) {
                } finally {
                    // Preserve Android's standard crash dialog, logcat and
                    // process termination behavior.
                    if (previous != null) {
                        previous.uncaughtException(thread, error);
                    } else {
                        Process.killProcess(Process.myPid());
                        System.exit(10);
                    }
                }
            });
            crashHandlerInstalled = true;
        }
    }

    private void recordPreviousAndroidExits() {
        if (Build.VERSION.SDK_INT < 30) return;
        try {
            ActivityManager am = (ActivityManager) getSystemService(Context.ACTIVITY_SERVICE);
            if (am == null) return;
            SharedPreferences prefs = getSharedPreferences("pymusic_exit_history", MODE_PRIVATE);
            long lastSeen = prefs.getLong("last_seen_exit", 0L);
            long newest = lastSeen;
            List<ApplicationExitInfo> exits =
                    am.getHistoricalProcessExitReasons(getPackageName(), 0, 25);
            if (exits == null) return;
            for (ApplicationExitInfo exit : exits) {
                long timestamp = exit.getTimestamp();
                if (timestamp <= lastSeen) continue;
                newest = Math.max(newest, timestamp);
                int reason = exit.getReason();
                String header = "=== Android previous process exit ===\n"
                        + "time_ms=" + timestamp + "\n"
                        + "pid=" + exit.getPid() + "\n"
                        + "process=" + exit.getProcessName() + "\n"
                        + "reason=" + reason + " status=" + exit.getStatus() + "\n"
                        + "importance=" + exit.getImportance() + "\n"
                        + "description=" + exit.getDescription() + "\n";
                String name = "android_exit_" + timestamp + "_" + exit.getPid() + ".log";
                persistAndroidReport(this, name, header);
                // ANR trace is text, native tombstones on API 31+ are a
                // protobuf. Store original trace bytes separately from the
                // human-readable exit report; the system may return null.
                try (InputStream in = exit.getTraceInputStream()) {
                    if (in != null) {
                        String traceName = "android_trace_" + timestamp + "_" + exit.getPid() + ".bin";
                        File outputFile = new File(diagnosticsDir(this), traceName);
                        persistAndroidReport(this, name, "trace_file=" + traceName + "\n");
                        try (FileOutputStream out = new FileOutputStream(outputFile, false)) {
                            byte[] data = new byte[8192];
                            int remaining = 256 * 1024;
                            while (remaining > 0) {
                                int count = in.read(data, 0, Math.min(remaining, data.length));
                                if (count < 0) break;
                                out.write(data, 0, count);
                                remaining -= count;
                            }
                        }
                    }
                } catch (Throwable ignored) {
                }
            }
            if (newest > lastSeen) {
                prefs.edit().putLong("last_seen_exit", newest).apply();
            }
        } catch (Throwable ignored) {
            // ApplicationExitInfo may be limited by vendor / system history.
        }
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        installCrashRecorder();
        recordPreviousAndroidExits();
        super.onCreate(savedInstanceState);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        try {
            setIntent(intent);
        } catch (Exception ignored) {
        }
        super.onNewIntent(intent);
    }
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
