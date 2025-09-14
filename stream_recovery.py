# stream_recovery.py
import threading
import time
import socket

class StreamRecovery:
    """
    Легкий вотчдог мережі/столу, який:
      • періодично читає стан плеєра через get_player_state()
      • якщо мережі нема — чекає
      • якщо потік застиг >= 10 c — викликає on_refresh()
      • НІКОЛИ не робить resume, якщо user_paused=True
    """

    def __init__(self, on_resume, on_pause, on_refresh, get_player_state):
        self.on_resume = on_resume
        self.on_pause = on_pause
        self.on_refresh = on_refresh
        self.get_player_state = get_player_state

        self._th = None
        self._stop = False
        self._last_pos = -1
        self._stall_ts = None
        self._user_paused = False

    # ---- публічне API ----
    def start(self):
        self._stop = False
        if self._th and self._th.is_alive():
            return
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()
        print("[RECOVERY] started")

    def stop(self):
        self._stop = True
        self._th = None
        print("[RECOVERY] stopped")

    def set_user_paused(self, value: bool):
        """Коли юзер натискає паузу — встановлюємо True; при Play/Resume — False."""
        self._user_paused = bool(value)
        print(f"[RECOVERY] user_paused={self._user_paused}")

    # ---- нутрощі ----
    def _net_ok(self) -> bool:
        try:
            # дуже швидкий DNS-пінг
            s = socket.create_connection(("1.1.1.1", 53), timeout=2.0)
            s.close()
            return True
        except Exception:
            return False

    def _loop(self):
        while not self._stop:
            try:
                st = self.get_player_state()  # -> (pos_ms, dur_ms, playing) або None
                if st is None:
                    time.sleep(0.5); continue

                pos, dur, playing = st
                net_ok = self._net_ok()
                print(f"[RECOVERY][WD] net={net_ok} playing={playing} pos={pos}")

                if not net_ok:
                    # чекаємо мережу; просто фіксуємо старт стола
                    if self._stall_ts is None:
                        self._stall_ts = time.time()
                    time.sleep(1.0); continue

                # якщо відтворення йде — перевіряємо застій
                if playing:
                    if pos == self._last_pos:
                        if self._stall_ts is None:
                            self._stall_ts = time.time()
                        elif time.time() - self._stall_ts >= 10:
                            print("[RECOVERY] stall>=10s -> on_refresh()")
                            self._stall_ts = None
                            self.on_refresh()
                    else:
                        self._stall_ts = None
                else:
                    # не грає; якщо юзер поставив паузу — НІЧОГО НЕ РОБИМО
                    if self._user_paused:
                        self._stall_ts = None
                    else:
                        # не юзерська пауза: якщо всередині треку — м’який автозапуск
                        if 0 < pos < max(1, (dur - 1500)):
                            self.on_resume()

                self._last_pos = pos
            except Exception as e:
                print("[RECOVERY] loop err:", e)

            time.sleep(2.0)
