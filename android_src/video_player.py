from __future__ import annotations

from jnius import autoclass, PythonJavaClass, java_method
from android.runnable import run_on_ui_thread

from kivy.clock import Clock

import ytdlp_helpers as ydlh

PythonActivity = autoclass("org.kivy.android.PythonActivity")
MediaPlayer = autoclass("android.media.MediaPlayer")
SurfaceViewClass = autoclass("android.view.SurfaceView")
Color = autoclass("android.graphics.Color")
FrameLayoutLayoutParams = autoclass("android.widget.FrameLayout$LayoutParams")
Gravity = autoclass("android.view.Gravity")
Uri = autoclass("android.net.Uri")
HashMap = autoclass("java.util.HashMap")
WebView = autoclass("android.webkit.WebView")
WebViewClient = autoclass("android.webkit.WebViewClient")
WebChromeClient = autoclass("android.webkit.WebChromeClient")
View = autoclass("android.view.View")


class AndroidVideoPlayer:

    def __init__(self):
        self.player = None
        self.surface_view = None
        self.screen_w_px = None
        self.screen_h_px = None
        self.pending_bounds: tuple[int, int, int, int] | None = None
        # лічильник викликів play - щоб не було старих "хвостів"
        self._play_gen = 0
        self._prepared = False
        self._pending_start_pos_ms = None
        self._start_pos_provider = None
        self._start_paused = False
        self._on_prepared_cb = None
        self._prepared_listener = None
        self._video_cache_enabled = False

    class _OnPreparedListener(PythonJavaClass):
        __javainterfaces__ = ['android/media/MediaPlayer$OnPreparedListener']
        __javacontext__ = 'app'

        def __init__(self, owner, gen):
            super().__init__()
            self._owner = owner
            self._gen = gen

        @java_method('(Landroid/media/MediaPlayer;)V')
        def onPrepared(self, mp):
            try:
                self._owner._on_prepared(mp, self._gen)
            except Exception:
                pass

    @run_on_ui_thread
    def create_surface(self):
        activity = PythonActivity.mActivity

        # REUSE EXISTING SURFACE
        if self.surface_view is not None:
            try:
                sv = self.surface_view
                sv.setVisibility(0)
                parent = sv.getParent()
                if parent is not None:
                    try:
                        parent.bringChildToFront(sv)
                    except Exception:
                        pass
                    try:
                        parent.requestLayout()
                    except Exception:
                        try:
                            sv.requestLayout()
                        except Exception:
                            pass
                    try:
                        parent.invalidate()
                    except Exception:
                        try:
                            sv.invalidate()
                        except Exception:
                            pass
            except Exception as e:
                print("[VIDEO] reuse surface_view err:", e)
            return

        # CREATE NEW SURFACE
        sv = SurfaceViewClass(activity)

        try:
            metrics = activity.getResources().getDisplayMetrics()
            screen_w = int(metrics.widthPixels)
            screen_h = int(metrics.heightPixels)
        except Exception:
            screen_w = 1080
            screen_h = 1920

        self.screen_w_px = screen_w
        self.screen_h_px = screen_h

        params = FrameLayoutLayoutParams(
            FrameLayoutLayoutParams.MATCH_PARENT,
            FrameLayoutLayoutParams.MATCH_PARENT
        )
        params.gravity = Gravity.CENTER
        sv.setLayoutParams(params)

        try:
            sv.setBackgroundColor(Color.TRANSPARENT)
        except Exception:
            pass

        try:
            activity.addContentView(sv, params)
            try:
                parent = sv.getParent()
                if parent is not None:
                    try:
                        parent.bringChildToFront(sv)
                    except Exception:
                        pass
                    try:
                        parent.requestLayout()
                    except Exception:
                        pass
                    try:
                        parent.invalidate()
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception as e:
            print("[VIDEO] addContentView error:", e)
            return

        sv.setVisibility(0)

        self.surface_view = sv
        try:
            if getattr(self, "pending_bounds", None):
                pending = self.pending_bounds
                self.pending_bounds = None
                self.set_bounds(*pending)
        except Exception:
            pass

        print("[VIDEO] SurfaceView created, screen_px =", self.screen_w_px, self.screen_h_px)

    @run_on_ui_thread
    def play(
        self,
        video_url: str,
        headers: dict | None = None,
        loop: bool = False,
        start_pos_ms: int | None = None,
        start_pos_provider=None,
        start_paused: bool = False,
        on_prepared=None,
    ):
        if not video_url:
            print("[VIDEO] empty url")
            return

        self.create_surface()

        # нове покоління play
        self._play_gen += 1
        gen = self._play_gen

        if self.player is not None:
            try:
                self.player.stop()
            except Exception:
                pass
            try:
                self.player.release()
            except Exception:
                pass
            self.player = None

        self.player = MediaPlayer()
        self._prepared = False
        self._pending_start_pos_ms = start_pos_ms
        self._start_pos_provider = start_pos_provider
        self._start_paused = start_paused
        self._on_prepared_cb = on_prepared

        try:
            print("[VIDEO] MediaPlayer setDataSource", video_url)

            is_local = video_url.startswith("/") or video_url.startswith("file://")
            used_headers = headers or {}

            if is_local:
                if video_url.startswith("/"):
                    uri = Uri.parse("file://" + video_url)
                else:
                    uri = Uri.parse(video_url)
                self.player.setDataSource(PythonActivity.mActivity, uri)
            else:
                jmap = None
                if used_headers:
                    try:
                        jmap = ydlh.py_headers_to_javamap(used_headers, HashMap)
                    except Exception as e:
                        print("[VIDEO] py_headers_to_javamap err:", e)
                        jmap = None

                try:
                    if jmap is not None:
                        self.player.setDataSource(video_url, jmap)
                    else:
                        self.player.setDataSource(video_url)
                except Exception as e:
                    print("[VIDEO] setDataSource(url, headers) failed, fallback:", e)
                    self.player.setDataSource(video_url)

            self.player.setLooping(loop)
            self.player.setVolume(0.0, 0.0)
            try:
                self._prepared_listener = self._OnPreparedListener(self, gen)
                self.player.setOnPreparedListener(self._prepared_listener)
            except Exception:
                self._prepared_listener = None


            Clock.schedule_once(lambda dt: self._attach_and_prepare_when_surface_ready(gen), 0.05)
        except Exception as e:
            print("[VIDEO] play() error (setDataSource):", e)
            try:
                if self.surface_view is not None:
                    self.surface_view.setVisibility(4)
            except Exception:
                pass

    def _on_prepared(self, mp, gen: int):
        if gen != self._play_gen:
            return
        if self.player is None or mp is None:
            return
        self._prepared = True
        pos_ms = None
        try:
            if callable(self._start_pos_provider):
                pos_ms = self._start_pos_provider()
        except Exception:
            pos_ms = None
        if pos_ms is None:
            pos_ms = self._pending_start_pos_ms
        try:
            if pos_ms is not None and int(pos_ms) > 0:
                mp.seekTo(int(pos_ms))
        except Exception:
            pass
        try:
            if not self._start_paused:
                mp.start()
        except Exception:
            pass
        try:

            if callable(self._on_prepared_cb):
                self._on_prepared_cb()
        except Exception:
            pass

    def _attach_and_prepare_when_surface_ready(self, gen: int):

        @run_on_ui_thread
        def _check(*_):
            if gen != self._play_gen:
                return
            if self.player is None:
                return
            if self.surface_view is None:
                Clock.schedule_once(lambda dt: _check(), 0.05)
                return

            if gen != self._play_gen:
                return

            try:
                holder = self.surface_view.getHolder()
                self.player.setDisplay(holder)

                try:
                    self.surface_view.setVisibility(0)
                    parent = self.surface_view.getParent()
                    if parent is not None:
                        try:
                            parent.bringChildToFront(self.surface_view)
                        except Exception:
                            pass
                        try:
                            parent.requestLayout()
                        except Exception:
                            try:
                                self.surface_view.requestLayout()
                            except Exception:
                                pass
                        try:
                            parent.invalidate()
                        except Exception:
                            try:
                                self.surface_view.invalidate()
                            except Exception:
                                pass
                except Exception:
                    pass
                print("[VIDEO] calling prepareAsync()")
                self.player.prepareAsync()
            except Exception as e:
                print("[VIDEO] play() error (attach/prepare):", e)
                try:
                    self.surface_view.setVisibility(4)
                except Exception:
                    pass

        Clock.schedule_once(lambda dt: _check(), 0)

    @run_on_ui_thread
    def stop(self):
        self._play_gen += 1
        self._prepared = False
        try:
            if self.player is not None:
                try:
                    self.player.stop()
                except Exception:
                    pass
                try:
                    self.player.release()
                except Exception:
                    pass
                self.player = None
        except Exception:
            pass

        try:
            if self.surface_view is not None:
                self.surface_view.setVisibility(4)
        except Exception:
            pass

    @run_on_ui_thread
    def seek_to(self, ms: int):
        try:
            if self.player is None:
                return
            if self._prepared:
                self.player.seekTo(int(ms))
            else:
                self._pending_start_pos_ms = int(ms)
        except Exception:
            pass

    @run_on_ui_thread
    def set_bounds(self, left: int, top: int, width: int, height: int) -> None:
        try:
            if self.surface_view is None:
                self.pending_bounds = (left, top, width, height)
                print("[VIDEO] set_bounds stored pending:", self.pending_bounds)
                return

            sv = self.surface_view

            if width <= 0 or height <= 0:
                print("[VIDEO] set_bounds skip, non positive size:", width, height)
                return

            params = FrameLayoutLayoutParams(int(width), int(height))
            params.leftMargin = int(left)
            params.topMargin = int(top)
            sv.setLayoutParams(params)

            parent = sv.getParent()
            if parent is not None:
                try:
                    parent.bringChildToFront(sv)
                except Exception:
                    pass
                try:
                    parent.requestLayout()
                except Exception:
                    try:
                        sv.requestLayout()
                    except Exception:
                        pass
                try:
                    parent.invalidate()
                except Exception:
                    try:
                        sv.invalidate()
                    except Exception:
                        pass

            print(f"[VIDEO] set_bounds applied left={left}, top={top}, w={width}, h={height}")
        except Exception as e:
            print(f"[VIDEO] set_bounds error: {e}")

    @run_on_ui_thread
    def pause(self):
        try:
            if self.player and self.player.isPlaying():
                self.player.pause()
                print("[VIDEO] paused")
        except Exception:
            pass

    @run_on_ui_thread
    def resume(self):
        try:
            if self.player and not self.player.isPlaying():
                self.player.start()
                print("[VIDEO] resumed")
        except Exception:
            pass


class AndroidWebVideoPlayer:
    is_embed = True

    def __init__(self):
        self.webview = None
        self.screen_w_px = None
        self.screen_h_px = None
        self.pending_bounds: tuple[int, int, int, int] | None = None
        self._play_gen = 0
        self._current_video_id = None
        self._web_client = None
        self._use_full_page = False
        self._current_mode = "full"

    class _WebUiClient(PythonJavaClass):
        __javabase__ = 'android/webkit/WebViewClient'
        __javacontext__ = 'app'

        @java_method('(Landroid/webkit/WebView;Ljava/lang/String;)V')
        def onPageFinished(self, view, url):
            try:
                js = (
                    "try{"
                    "var css='html,body{margin:0!important;padding:0!important;width:100%!important;height:100%!important;"
                    "overflow:hidden!important;background:#000!important;}'"
                    "+'#player, ytm-player, .player-container{position:absolute!important;left:0;top:0;"
                    "width:100%!important;height:100%!important;background:#000!important;}'"
                    "+'video{width:100%!important;height:100%!important;object-fit:contain!important;background:#000!important;}';"
                    "var s=document.getElementById('pymusic-hide');"
                    "if(!s){s=document.createElement('style');s.id='pymusic-hide';document.documentElement.appendChild(s);}"
                    "s.textContent=css;"
                    "var v=document.querySelector('video');if(v){v.muted=true;v.volume=0;}"
                    "}catch(e){}"
                )
                if view is not None:
                    view.evaluateJavascript(js, None)
            except Exception:
                pass

    def _extract_video_id(self, url: str | None) -> str | None:
        if not url:
            return None
        try:
            u = str(url)
            if "youtu.be/" in u:
                part = u.split("youtu.be/", 1)[1]
                return part.split("?", 1)[0].split("&", 1)[0]
            if "watch?v=" in u:
                part = u.split("watch?v=", 1)[1]
                return part.split("&", 1)[0]
            if "/shorts/" in u:
                part = u.split("/shorts/", 1)[1]
                return part.split("?", 1)[0].split("&", 1)[0]
            if "/embed/" in u:
                part = u.split("/embed/", 1)[1]
                return part.split("?", 1)[0].split("&", 1)[0]
        except Exception:
            return None
        return None

    @run_on_ui_thread
    def create_surface(self):
        activity = PythonActivity.mActivity
        if self.webview is not None:
            try:
                self.webview.setVisibility(View.VISIBLE)
                parent = self.webview.getParent()
                if parent is not None:
                    try:
                        parent.bringChildToFront(self.webview)
                    except Exception:
                        pass
            except Exception:
                pass
            return

        wv = WebView(activity)
        try:
            metrics = activity.getResources().getDisplayMetrics()
            self.screen_w_px = int(metrics.widthPixels)
            self.screen_h_px = int(metrics.heightPixels)
        except Exception:
            self.screen_w_px = 1080
            self.screen_h_px = 1920

        params = FrameLayoutLayoutParams(
            FrameLayoutLayoutParams.MATCH_PARENT,
            FrameLayoutLayoutParams.MATCH_PARENT
        )
        params.gravity = Gravity.CENTER
        wv.setLayoutParams(params)
        try:
            wv.setBackgroundColor(Color.BLACK)
        except Exception:
            pass

        try:
            settings = wv.getSettings()
            settings.setJavaScriptEnabled(True)
            settings.setDomStorageEnabled(True)
            try:
                settings.setMediaPlaybackRequiresUserGesture(False)
            except Exception:
                pass
        except Exception:
            pass

        try:
            self._web_client = self._WebUiClient()
            wv.setWebViewClient(self._web_client)
        except Exception:
            pass
        try:
            wv.setWebChromeClient(WebChromeClient())
        except Exception:
            pass

        try:
            activity.addContentView(wv, params)
            parent = wv.getParent()
            if parent is not None:
                try:
                    parent.bringChildToFront(wv)
                except Exception:
                    pass
        except Exception:
            pass

        wv.setVisibility(View.VISIBLE)
        self.webview = wv
        try:
            if getattr(self, "pending_bounds", None):
                pending = self.pending_bounds
                self.pending_bounds = None
                self.set_bounds(*pending)
        except Exception:
            pass

    def _html(self, video_id: str, start_sec: int, allow_fallback: bool) -> str:
        return f"""<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<style>
html,body,#player{{margin:0;width:100%;height:100%;background:#000;overflow:hidden;}}
</style>
</head>
<body>
<div id="player"></div>
<script src="https://www.youtube.com/iframe_api"></script>
<script>
var player=null;
var currentVid='{video_id}';
var currentStart={start_sec};
var allowFallback={'true' if allow_fallback else 'false'};
var pymusicReady=false;
var pymusicStartTs=Date.now();
function fallbackUrl(vid,start){{
  var s = start||0;
  return 'https://m.youtube.com/watch?v=' + vid + '&playsinline=1&autoplay=1&start=' + s;
}}
function forceFallback(){{
  if(!allowFallback) return;
  try{{ window.location.replace(fallbackUrl(currentVid, currentStart)); }}catch(e){{ window.location.href=fallbackUrl(currentVid, currentStart); }}
}}
function handleError(e){{
  var code = e && e.data;
  if(code===101 || code===150 || code===152){{
    forceFallback();
  }}
}}
var _unmuted=false;
function tryUnmute(){{
  if(_unmuted || !player) return;
  try{{ player.unMute(); _unmuted=true; }}catch(e){{}}
}}
function onYouTubeIframeAPIReady() {{
  player = new YT.Player('player', {{
    videoId: '{video_id}',
    playerVars: {{
      'autoplay': 1,
      'controls': 1,
      'playsinline': 1,
      'mute': 1,
      'rel': 0,
      'modestbranding': 1,
      'iv_load_policy': 3,
      'fs': 0
    }},
    events: {{
      'onReady': function(e) {{
        pymusicReady=true;
        try{{ player.mute(); }}catch(e){{}}
        try{{ player.seekTo({start_sec}, true); }}catch(e){{}}
        try{{ player.playVideo(); }}catch(e){{}}
        try{{ setTimeout(tryUnmute, 1500); }}catch(e){{}}
        try{{ document.addEventListener('touchstart', tryUnmute, {{once:true, passive:true}}); }}catch(e){{}}
        try{{ document.addEventListener('click', tryUnmute, {{once:true}}); }}catch(e){{}}
        try{{
          var checks=0;
          var iv=setInterval(function(){{
            checks++;
            var st=-1;
            try{{ st=player.getPlayerState(); }}catch(e){{}}
            if(st===1||st===2||st===3){{ clearInterval(iv); return; }}
            if(checks>6){{ clearInterval(iv); forceFallback(); }}
          }}, 500);
        }}catch(e){{}}
      }},
      'onError': handleError
    }}
  }});
}}
setTimeout(function(){{
  if(!pymusicReady){{ forceFallback(); }}
}}, 3000);
window.pymusicLoad=function(vid,start){{
  currentVid = vid;
  currentStart = start||0;
  if(!player) {{
    window.location.href = fallbackUrl(currentVid, currentStart);
    return;
  }}
  try{{ player.loadVideoById(vid, currentStart); player.mute(); setTimeout(tryUnmute, 1500); }}catch(e){{}}
}};
window.pymusicSeek=function(sec){{ try{{ if(player) player.seekTo(sec,true); }}catch(e){{}} }};
window.pymusicPlay=function(){{ try{{ if(player) player.playVideo(); }}catch(e){{}} }};
window.pymusicPause=function(){{ try{{ if(player) player.pauseVideo(); }}catch(e){{}} }};
</script>
</body>
</html>
"""

    @run_on_ui_thread
    def play(self, video_url: str, start_pos_ms: int | None = None, start_pos_provider=None):
        vid = self._extract_video_id(video_url)
        if not vid:
            print("[VIDEO] embed: no video id")
            return

        self.create_surface()
        self._play_gen += 1
        gen = self._play_gen
        self._current_video_id = vid

        start_ms = None
        try:
            if callable(start_pos_provider):
                start_ms = int(start_pos_provider() or 0)
        except Exception:
            start_ms = None
        if start_ms is None:
            start_ms = int(start_pos_ms or 0)
        start_sec = max(0, int(start_ms / 1000))

        if self.webview is None:
            return
        try:
            self.webview.setVisibility(View.VISIBLE)
        except Exception:
            pass

        if self._use_full_page:
            self._current_mode = "full"
            url = f"https://m.youtube.com/watch?v={vid}&playsinline=1&autoplay=1&start={start_sec}"
            try:
                self.webview.loadUrl(url)
            except Exception:
                pass
        else:
            self._current_mode = "iframe"
            html = self._html(vid, start_sec, False)
            try:
                self.webview.loadDataWithBaseURL("https://www.youtube.com", html, "text/html", "utf-8", None)
            except Exception:
                try:
                    self.webview.loadData(html, "text/html", "utf-8")
                except Exception:
                    pass

    @run_on_ui_thread
    def seek_to(self, ms: int):
        if self.webview is None:
            return
        sec = max(0, int(ms / 1000))
        try:
            self.webview.evaluateJavascript(f"pymusicSeek({sec});", None)
        except Exception:
            pass
        try:
            self.webview.evaluateJavascript(
                f"var v=document.querySelector('video');if(v){{v.currentTime={sec};}}",
                None
            )
        except Exception:
            pass

    @run_on_ui_thread
    def pause(self):
        if self.webview is None:
            return
        try:
            self.webview.evaluateJavascript("pymusicPause();", None)
        except Exception:
            pass
        try:
            self.webview.evaluateJavascript(
                "var v=document.querySelector('video');if(v){v.pause();}",
                None
            )
        except Exception:
            pass

    @run_on_ui_thread
    def resume(self):
        if self.webview is None:
            return
        try:
            self.webview.evaluateJavascript("pymusicPlay();", None)
        except Exception:
            pass
        try:
            self.webview.evaluateJavascript(
                "var v=document.querySelector('video');if(v){v.play();}",
                None
            )
        except Exception:
            pass

    @run_on_ui_thread
    def stop(self):
        self._play_gen += 1
        if self.webview is None:
            return
        try:
            self.webview.loadUrl("about:blank")
        except Exception:
            pass
        try:
            self.webview.setVisibility(View.GONE)
        except Exception:
            pass

    @run_on_ui_thread
    def set_bounds(self, left: int, top: int, width: int, height: int) -> None:
        try:
            if self.webview is None:
                self.pending_bounds = (left, top, width, height)
                print("[VIDEO] set_bounds stored pending:", self.pending_bounds)
                return

            if width <= 0 or height <= 0:
                print("[VIDEO] set_bounds skip, non positive size:", width, height)
                return

            params = FrameLayoutLayoutParams(int(width), int(height))
            params.leftMargin = int(left)
            params.topMargin = int(top)
            self.webview.setLayoutParams(params)

            parent = self.webview.getParent()
            if parent is not None:
                try:
                    parent.bringChildToFront(self.webview)
                except Exception:
                    pass
                try:
                    parent.requestLayout()
                except Exception:
                    pass
                try:
                    parent.invalidate()
                except Exception:
                    pass
        except Exception as e:
            print(f"[VIDEO] set_bounds error: {e}")
