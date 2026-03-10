package org.koteuka404.pymusic;

import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.SystemClock;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;

public class WebViewClientBridge extends WebViewClient {
    private final Context ctx;
    private final WebView webView;
    private static String lastUrl = null;
    private static long lastTs = 0;

    public WebViewClientBridge(Context ctx, WebView webView) {
        this.ctx = ctx;
        this.webView = webView;
        try {
            if (this.webView != null) {
                this.webView.addJavascriptInterface(this, "PyMusicBridge");
            }
        } catch (Exception ignored) {
        }
    }

    private static boolean isWatchUrl(String url) {
        if (url == null) return false;
        String u = url.toLowerCase();
        return u.contains("youtube.com/watch")
                || u.contains("m.youtube.com/watch")
                || u.contains("music.youtube.com/watch")
                || u.contains("youtu.be/")
                || u.contains("youtube.com/shorts/")
                || u.contains("m.youtube.com/shorts/")
                || u.contains("youtube.com/live/")
                || u.contains("m.youtube.com/live/");
    }

    private void dispatch(String url) {
        if (ctx == null || url == null) return;
        long now = SystemClock.uptimeMillis();
        if (url.equals(lastUrl) && (now - lastTs) < 1000) {
            return;
        }
        lastUrl = url;
        lastTs = now;
        Intent i = new Intent(ctx, MediaKeyActivity.class);
        i.setAction("org.koteuka404.pymusic.WEB_URL");
        i.putExtra("url", url);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                | Intent.FLAG_ACTIVITY_SINGLE_TOP
                | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        ctx.startActivity(i);
    }

    private void dispatchMode(String mode) {
        if (ctx == null || mode == null) return;
        Intent i = new Intent(ctx, MediaKeyActivity.class);
        i.setAction("org.koteuka404.pymusic.WEB_MODE");
        i.putExtra("mode", mode);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                | Intent.FLAG_ACTIVITY_SINGLE_TOP
                | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        ctx.startActivity(i);
    }

    @JavascriptInterface
    public void onUrl(String url) {
        if (isWatchUrl(url)) {
            dispatch(url);
        }
    }

    @JavascriptInterface
    public void onMode(String mode) {
        dispatchMode(mode);
    }

    private void injectHook(WebView view) {
        if (view == null) return;
        String js = "(function(){"
                + "if(window.__pymusic_hooked){return;}window.__pymusic_hooked=true;"
                + "function notify(){try{ensureUi();}catch(e){};try{PyMusicBridge.onUrl(location.href);}catch(e){}}"
                + "function ensureUi(){"
                + "if(document.getElementById('pymusic-bottom')){return;}"
                + "var style=document.createElement('style');style.id='pymusic-style';"
                + "style.textContent='#pymusic-bottom{position:fixed;left:0;right:0;bottom:0;"
                + "z-index:2147483647;background:rgba(0,0,0,0.9);display:flex;"
                + "justify-content:space-around;align-items:center;height:52px;"
                + "padding-bottom:env(safe-area-inset-bottom);}'"
                + "+'#pymusic-bottom button{min-width:72px;padding:8px 16px;"
                + "border-radius:999px;border:1px solid rgba(255,255,255,0.35);"
                + "background:transparent;color:#fff;font:13px sans-serif;}';"
                + "document.documentElement.appendChild(style);"
                + "var bar=document.createElement('div');bar.id='pymusic-bottom';"
                + "var bWeb=document.createElement('button');bWeb.textContent='Web';"
                + "var bYt=document.createElement('button');bYt.textContent='YT';"
                + "bWeb.addEventListener('click',function(e){e.stopPropagation();"
                + "try{PyMusicBridge.onMode(\"search\");}catch(e){};});"
                + "bYt.addEventListener('click',function(e){e.stopPropagation();"
                + "try{PyMusicBridge.onMode(\"web\");}catch(e){};});"
                + "bar.appendChild(bWeb);bar.appendChild(bYt);"
                + "document.documentElement.appendChild(bar);"
                + "}"
                + "function isWatch(u){return /youtube\\.com\\/(watch|shorts|live)\\b/.test(u)||/m\\.youtube\\.com\\/(watch|shorts|live)\\b/.test(u)||/music\\.youtube\\.com\\/watch\\b/.test(u)||/youtu\\.be\\//.test(u);}"
                + "var p=history.pushState;history.pushState=function(){p.apply(this,arguments);notify();};"
                + "var r=history.replaceState;history.replaceState=function(){r.apply(this,arguments);notify();};"
                + "window.addEventListener('popstate',notify);"
                + "document.addEventListener('click',function(e){var t=e.target;"
                + "while(t&&t.href===undefined&&t.parentElement){t=t.parentElement;}"
                + "if(t&&t.href){if(isWatch(t.href)){try{PyMusicBridge.onUrl(t.href);}catch(e){};e.preventDefault();e.stopPropagation();return false;}else{try{PyMusicBridge.onUrl(t.href);}catch(e){}}}"
                + "},true);"
                + "ensureUi();notify();"
                + "})();";
        try {
            if (Build.VERSION.SDK_INT >= 19) {
                view.evaluateJavascript(js, null);
            } else {
                view.loadUrl("javascript:" + js);
            }
        } catch (Exception ignored) {
        }
    }

    @Override
    public boolean shouldOverrideUrlLoading(WebView view, String url) {
        if (isWatchUrl(url)) {
            dispatch(url);
            try {
                view.stopLoading();
            } catch (Exception ignored) {}
            return true;
        }
        return false;
    }

    @Override
    public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
        if (request == null) return false;
        String url = request.getUrl() != null ? request.getUrl().toString() : null;
        if (isWatchUrl(url)) {
            dispatch(url);
            try {
                view.stopLoading();
            } catch (Exception ignored) {}
            return true;
        }
        return false;
    }

    @Override
    public void onPageFinished(WebView view, String url) {
        injectHook(view);
        if (isWatchUrl(url)) {
            dispatch(url);
            try {
                view.stopLoading();
            } catch (Exception ignored) {}
        }
    }
}
