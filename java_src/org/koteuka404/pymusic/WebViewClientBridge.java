package org.koteuka404.pymusic;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
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
                || u.contains("youtube.com/playlist")
                || u.contains("m.youtube.com/playlist")
                || u.contains("music.youtube.com/playlist")
                || u.contains("youtube.com/browse/")
                || u.contains("music.youtube.com/browse/")
                || u.contains("youtu.be/")
                || u.contains("youtube.com/shorts/")
                || u.contains("m.youtube.com/shorts/")
                || u.contains("youtube.com/live/")
                || u.contains("m.youtube.com/live/");
    }

    private static String forceMobileYoutube(String url) {
        if (url == null) return null;
        try {
            Uri u = Uri.parse(url);
            String host = u.getHost();
            if (host == null) return url;
            String h = host.toLowerCase();
            if ("music.youtube.com".equals(h)) {
                return url;
            }
            if ("youtu.be".equals(h)) {
                String path = u.getPath() == null ? "" : u.getPath();
                String vid = path.replaceFirst("^/+", "");
                if (!vid.isEmpty()) {
                    Uri.Builder b = new Uri.Builder()
                            .scheme("https")
                            .authority("m.youtube.com")
                            .path("/watch")
                            .appendQueryParameter("v", vid);
                    String qList = u.getQueryParameter("list");
                    if (qList != null && !qList.isEmpty()) {
                        b.appendQueryParameter("list", qList);
                    }
                    return b.build().toString();
                }
                return url;
            }
            if ("youtube.com".equals(h) || "www.youtube.com".equals(h)) {
                return u.buildUpon().authority("m.youtube.com").build().toString();
            }
        } catch (Exception ignored) {
        }
        return url;
    }

    private boolean dispatch(String url) {
        if (ctx == null || url == null) return false;
        try {
            if (webView != null) {
                String js = "(function(){try{var m=document.querySelectorAll('video,audio');"
                        + "for(var i=0;i<m.length;i++){try{m[i].pause();}catch(e){}"
                        + "try{m[i].muted=true;}catch(e){}try{m[i].volume=0;}catch(e){}}}catch(e){}})();";
                if (Build.VERSION.SDK_INT >= 19) {
                    webView.evaluateJavascript(js, null);
                } else {
                    webView.loadUrl("javascript:" + js);
                }
            }
        } catch (Exception ignored) {
        }
        long now = SystemClock.uptimeMillis();
        if (url.equals(lastUrl) && (now - lastTs) < 1000) {
            return true;
        }
        lastUrl = url;
        lastTs = now;
        try {
            Intent i = new Intent(ctx, MediaKeyActivity.class);
            i.setAction("org.koteuka404.pymusic.WEB_URL");
            i.putExtra("url", url);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                    | Intent.FLAG_ACTIVITY_SINGLE_TOP
                    | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            ctx.startActivity(i);
            return true;
        } catch (Exception ignored) {
            return false;
        }
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
                + "function notify(){try{PyMusicBridge.onUrl(location.href);}catch(e){}}"
                + "function isWatch(u){return /youtube\\.com\\/(watch|shorts|live|playlist|browse\\/)\\b/.test(u)||/m\\.youtube\\.com\\/(watch|shorts|live|playlist)\\b/.test(u)||/music\\.youtube\\.com\\/(watch|playlist|browse\\/)\\b/.test(u)||/youtu\\.be\\//.test(u);}"
                + "function ensureUi(){"
                + "try{"
                + "if(!document.getElementById('pymusic-style')){"
                + "var s=document.createElement('style');s.id='pymusic-style';"
                + "s.textContent='#pymusic-bottom{position:fixed;left:0;right:0;bottom:0;z-index:2147483647;"
                + "background:rgba(0,0,0,.92);height:56px;display:flex;align-items:center;justify-content:center;"
                + "gap:16px;padding:0 10px 0 10px;}"
                + "#pymusic-bottom button{min-width:84px;height:34px;padding:0 14px;border-radius:18px;"
                + "border:1px solid rgba(255,255,255,.35);background:transparent;color:#fff;font:14px sans-serif;}"
                + "ytm-pivot-bar-renderer, ytm-pivot-bar-item-renderer, .pivot-bar, #pivot-bar, "
                + "tp-yt-app-bottom-nav, ytd-mini-guide-renderer, #footer{display:none!important;}"
                + "body{padding-bottom:0!important;}';"
                + "document.documentElement.appendChild(s);"
                + "}"
                + "if(!document.getElementById('pymusic-bottom')){"
                + "var bar=document.createElement('div');bar.id='pymusic-bottom';"
                + "var bWeb=document.createElement('button');bWeb.textContent='Web';"
                + "var bYt=document.createElement('button');bYt.textContent='YT';"
                + "bWeb.onclick=function(e){try{e.preventDefault();e.stopPropagation();}catch(_e){};try{PyMusicBridge.onMode('search');}catch(_e){}};"
                + "bYt.onclick=function(e){try{e.preventDefault();e.stopPropagation();}catch(_e){};try{PyMusicBridge.onMode('web');}catch(_e){}};"
                + "bar.appendChild(bWeb);bar.appendChild(bYt);document.documentElement.appendChild(bar);"
                + "}"
                + "}catch(e){}"
                + "}"
                + "function isMusicSelected(){"
                + "try{"
                + "var labels=['Музика','Music'];"
                + "var nodes=document.querySelectorAll('button,yt-chip-cloud-chip-renderer,ytm-chip-cloud-chip-renderer,a,span');"
                + "for(var i=0;i<nodes.length;i++){"
                + "var n=nodes[i];var t=(n.innerText||n.textContent||'').trim();"
                + "for(var j=0;j<labels.length;j++){"
                + "if(t===labels[j]){"
                + "var c=n.closest('button,yt-chip-cloud-chip-renderer,ytm-chip-cloud-chip-renderer,a')||n;"
                + "var sel=(c.getAttribute&&((c.getAttribute('aria-selected')||'').toLowerCase()==='true'))"
                + "||c.classList&&c.classList.contains('selected')"
                + "||c.classList&&c.classList.contains('chip-selected')"
                + "||c.hasAttribute&&c.hasAttribute('selected');"
                + "if(sel){return true;}"
                + "}"
                + "}"
                + "}"
                + "}catch(e){}"
                + "return false;"
                + "}"
                + "function forceMusicChipOnce(){"
                + "try{"
                + "if(isMusicSelected()){return true;}"
                + "var labels=['Музика','Music'];"
                + "var nodes=document.querySelectorAll('button,yt-chip-cloud-chip-renderer,ytm-chip-cloud-chip-renderer,a,span');"
                + "for(var i=0;i<nodes.length;i++){"
                + "var n=nodes[i];var t=(n.innerText||n.textContent||'').trim();"
                + "for(var j=0;j<labels.length;j++){"
                + "if(t===labels[j]){"
                + "try{"
                + "var c=n.closest('button,yt-chip-cloud-chip-renderer,ytm-chip-cloud-chip-renderer,a')||n;"
                + "c.click();"
                + "return true;"
                + "}catch(_e){}"
                + "}"
                + "}"
                + "}"
                + "}catch(e){}"
                + "return false;"
                + "}"
                + "function scheduleMusicFilter(){"
                + "try{"
                + "var key=(location.pathname||'')+'?'+(location.search||'');"
                + "if(window.__pymusic_music_key===key){return;}"
                + "window.__pymusic_music_key=key;"
                + "var tries=0,maxTries=8;"
                + "var iv=setInterval(function(){"
                + "tries++;"
                + "var done=forceMusicChipOnce();"
                + "if(done||tries>=maxTries){clearInterval(iv);}"
                + "},450);"
                + "}catch(e){}"
                + "}"
                + "var p=history.pushState;history.pushState=function(){p.apply(this,arguments);notify();};"
                + "var r=history.replaceState;history.replaceState=function(){r.apply(this,arguments);notify();};"
                + "window.addEventListener('popstate',notify);"
                + "document.addEventListener('click',function(e){var t=e.target;"
                + "while(t&&t.href===undefined&&t.parentElement){t=t.parentElement;}"
                + "if(t&&t.href){if(isWatch(t.href)){try{PyMusicBridge.onUrl(t.href);}catch(e){};e.preventDefault();e.stopPropagation();return false;}else{try{PyMusicBridge.onUrl(t.href);}catch(e){}}}"
                + "},true);"
                + "ensureUi();"
                + "scheduleMusicFilter();"
                + "notify();"
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
        String mobileUrl = forceMobileYoutube(url);
        if (mobileUrl != null && !mobileUrl.equals(url)) {
            try {
                view.loadUrl(mobileUrl);
                return true;
            } catch (Exception ignored) {
            }
        }
        if (isWatchUrl(url)) {
            if (dispatch(url)) {
                try {
                    view.stopLoading();
                } catch (Exception ignored) {}
                return true;
            }
            return false;
        }
        return false;
    }

    @Override
    public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
        if (request == null) return false;
        String url = request.getUrl() != null ? request.getUrl().toString() : null;
        String mobileUrl = forceMobileYoutube(url);
        if (mobileUrl != null && !mobileUrl.equals(url)) {
            try {
                view.loadUrl(mobileUrl);
                return true;
            } catch (Exception ignored) {
            }
        }
        if (isWatchUrl(url)) {
            if (dispatch(url)) {
                try {
                    view.stopLoading();
                } catch (Exception ignored) {}
                return true;
            }
            return false;
        }
        return false;
    }

    @Override
    public void onPageFinished(WebView view, String url) {
        injectHook(view);
        if (isWatchUrl(url)) {
            if (dispatch(url)) {
                try {
                    view.stopLoading();
                } catch (Exception ignored) {}
            }
        }
    }
}
