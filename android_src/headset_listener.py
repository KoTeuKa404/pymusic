# headset_listener.py
from jnius import autoclass, cast, PythonJavaClass, java_method
from android.runnable import run_on_ui_thread

PythonActivity = autoclass('org.kivy.android.PythonActivity')
KeyEvent       = autoclass('android.view.KeyEvent')

class _HeadsetKeyListener(PythonJavaClass):
    __javainterfaces__ = ['android/view/View$OnKeyListener']
    __javacontext__ = 'app'
    def __init__(self, owner): super().__init__(); self.owner = owner
    @java_method('(Landroid/view/View;ILandroid/view/KeyEvent;)Z')
    def onKey(self, v, keyCode, event):
        if event.getAction() != KeyEvent.ACTION_DOWN:
            return False
        if keyCode in (KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE, KeyEvent.KEYCODE_HEADSETHOOK):
            self.owner.on_toggle();  return True
        if keyCode == KeyEvent.KEYCODE_MEDIA_PLAY:
            self.owner.on_play();    return True
        if keyCode == KeyEvent.KEYCODE_MEDIA_PAUSE:
            self.owner.on_pause();   return True
        if keyCode == KeyEvent.KEYCODE_MEDIA_NEXT:
            self.owner.on_next();    return True
        if keyCode == KeyEvent.KEYCODE_MEDIA_PREVIOUS:
            self.owner.on_prev();    return True
        return False

class HeadsetRouter:
    def __init__(self):
        self._listener = _HeadsetKeyListener(self)
        self._callbacks = {}
        self._bound = False

    def set_callbacks(self, *, on_play, on_pause, on_toggle, on_next, on_prev):
        self._callbacks = dict(on_play=on_play, on_pause=on_pause, on_toggle=on_toggle,
                               on_next=on_next, on_prev=on_prev)

    def on_play(self):   self._callbacks.get("on_play",  lambda: None)()
    def on_pause(self):  self._callbacks.get("on_pause", lambda: None)()
    def on_toggle(self): self._callbacks.get("on_toggle",lambda: None)()
    def on_next(self):   self._callbacks.get("on_next",  lambda: None)()
    def on_prev(self):   self._callbacks.get("on_prev",  lambda: None)()

    @run_on_ui_thread
    def set_active(self, active: bool):
        act = PythonActivity.mActivity
        decor = act.getWindow().getDecorView()
        if active and not self._bound:
            decor.setOnKeyListener(self._listener)
            decor.setFocusableInTouchMode(True)
            decor.setFocusable(True)
            decor.requestFocus()
            self._bound = True
        elif not active and self._bound:
            decor.setOnKeyListener(None)
            self._bound = False

headset_router = HeadsetRouter()
