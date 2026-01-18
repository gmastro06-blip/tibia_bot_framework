from typing import Optional, Sequence, Union
import re
import os
import cv2
import win32api
import win32gui
import win32ui
import win32con
import win32process
import numpy as np
import time
from mss import mss

try:
    from input_focus_guard import update_capture_target_state
except Exception:  # pragma: no cover
    update_capture_target_state = None  # type: ignore[assignment]


def _build_monitor_priority(
    *,
    n_monitors: int,
    preferred_monitor: Optional[int],
    last_good_monitor: Optional[int],
    window_monitor: Optional[int],
    obs_mode: bool,
    obs_fallback_monitor: Optional[int],
    scan_active_monitor: bool,
    active_monitor: Optional[int],
) -> list[int]:
    """Compute MSS monitor indices (1..n, plus 0 virtual) in priority order.

    This is a pure helper to keep the selection logic testable.

    MSS convention:
      - 1..n => individual monitors
      - 0    => virtual combined screen
    """

    try:
        n = int(n_monitors)
    except Exception:
        n = 0
    if n <= 0:
        return []

    monitor_indices: list[int] = []

    def _add(idx: Optional[int], *, allow_zero: bool = False) -> None:
        try:
            if idx is None:
                return
            i = int(idx)
        except Exception:
            return

        if not (0 <= i < n):
            return
        if i == 0 and not allow_zero:
            return
        if i not in monitor_indices:
            monitor_indices.append(i)

    # --- Hints (highest priority) ---
    _add(preferred_monitor, allow_zero=False)
    _add(last_good_monitor, allow_zero=False)
    _add(window_monitor, allow_zero=False)

    # OBS dual-monitor workflow: if we explicitly want the OBS projector but
    # cannot infer its monitor, prefer a stable fallback monitor (typically 1).
    if obs_mode and not monitor_indices:
        _add(obs_fallback_monitor, allow_zero=False)

    # Only use the "active monitor" heuristic when we have *no* other hints.
    if scan_active_monitor and not monitor_indices:
        _add(active_monitor, allow_zero=False)

    # --- Fill remaining monitors ---
    for i in range(1, n):
        _add(i, allow_zero=True)

    _add(0, allow_zero=True)
    return monitor_indices

class DXGICapture:
    def __init__(
        self,
        # Window titles are dynamic: the canonical Tibia title is usually
        # "Tibia - <PlayerName>" where <PlayerName> changes.
        # We match by substring, so this stays stable.
        title_partial: Union[str, Sequence[str]] = ("Tibia -", "Tibia"),
        force_monitor: Optional[int] = None,
        *,
        strict_force_monitor: bool = False,
    ):
        # Acepta un string o una lista de strings para matchear títulos de ventanas.
        if isinstance(title_partial, str):
            self.title_partials = [title_partial]
        else:
            self.title_partials = [p for p in title_partial if p]
        self.hwnd = self.find_window()
        # Client discovery (hwnd can exist even when background/maximized).
        self.client_hwnd: int = int(self.hwnd or 0)
        try:
            self.client_title = str(win32gui.GetWindowText(int(self.client_hwnd)) or "") if self.client_hwnd else ""
        except Exception:
            self.client_title = ""
        self.target_found: bool = False
        self.target_reason: str = ""
        self.capture_target: str = "auto"
        self.capture_backend: str = "dxgi"
        self.capture_bounds: Optional[tuple[int, int, int, int]] = None
        self.capture_state: str = "init"
        # Best-effort: which monitor we believe we captured from.
        # (MSS indexes: 1..n for individual monitors, 0 is the combined virtual screen)
        self.capture_monitor_index: Optional[int] = None
        self.force_monitor = force_monitor
        # Strict forced monitor policy:
        # - When enabled and `force_monitor` is set, the backend will NOT fall back
        #   to other monitors / window-crops if the forced monitor fails.
        # - This is useful for OBS-pinned workflows (avoid black crops on the wrong
        #   monitor), and ROI calibration tools.
        strict_env = False
        try:
            raw = (os.getenv("CAPTURE_STRICT_FORCE_MONITOR", "") or "").strip().lower()
            if raw:
                strict_env = raw in {"1", "true", "yes", "y", "on"}
        except Exception:
            strict_env = False
        self.strict_force_monitor = bool(strict_force_monitor) or bool(strict_env)
        # Optional explicit failover modes can still be used (projector/scan).
        self.fps = 0.0
        self.latency_ms = 0.0
        self.dropped = 0
        self._verbose = os.getenv("CAPTURE_VERBOSE", "").strip().lower() in {"1", "true", "yes"}
        self._last_hwnd_state: Optional[bool] = None
        self._last_ok_log_ts = 0.0
        # Rate-limit monitor-detection logs to avoid spamming stdout.
        self._last_window_monitor: Optional[int] = None
        self._last_window_monitor_log_ts = 0.0
        # Remember a known-good monitor index (MSS indexing).
        # Helps recover from wrong monitor selection / multi-monitor setups.
        self._last_good_monitor_index: Optional[int] = None

        # Optional: pick the most "active" monitor when window monitor cannot
        # be inferred. Default ON for runtime robustness.
        self._scan_active_monitor = os.getenv("CAPTURE_SCAN_ACTIVE_MONITOR", "1").strip().lower() not in {
            "0",
            "false",
            "no",
        }

        # Robust capture target selection (defaults are safe for real client capture).
        self._cfg_target = (os.getenv("CAPTURE_TARGET", "client") or "client").strip().lower() or "client"
        # Projector capture: OBS "Proyector en ventana (Fuente) - <SourceName>".
        # This is intentionally independent from the real client title ("Tibia - ...").
        self._cfg_projector_title = (
            os.getenv("CAPTURE_PROJECTOR_TITLE", "Proyector en ventana (Fuente) - Tibia_Fuente")
            or "Proyector en ventana (Fuente) - Tibia_Fuente"
        ).strip()
        self._cfg_title_include = (os.getenv("CAPTURE_TITLE_INCLUDE", "Tibia") or "Tibia").strip()
        self._cfg_title_exclude = (
            os.getenv("CAPTURE_TITLE_EXCLUDE", "Proyector|Projector|OBS") or "Proyector|Projector|OBS"
        ).strip()
        self._cfg_process_name = (os.getenv("CAPTURE_PROCESS_NAME", "") or "").strip()

        # Last-frame diagnostics (used by smoke tools).
        self.last_frame_mean: float | None = None
        self.black_streak: int = 0

        # Projector flow state (avoid infinite retries on a black surface).
        self._projector_backend: str = "bitblt"  # bitblt|screen_crop|fullscreen
        self._projector_adjust_logged: bool = False
        self._projector_switch_logged: dict[str, bool] = {}


        # Capture health counters (main will also track these, but having them here
        # makes the backend self-describing and testable).
        self.fail_count: int = 0
        self.reacquire_count: int = 0
        self.last_error: str = ""

        # Rate-limit console warnings (capture can transiently fail when minimized).
        self._warn_last: dict[str, float] = {}
        try:
            self._warn_every_s = max(0.0, float((os.getenv("CAPTURE_WARN_EVERY_S", "1.0") or "1.0").strip() or "1.0"))
        except Exception:
            self._warn_every_s = 1.0

        # Observability: rate-limit failover logs to avoid spamming at capture FPS.
        self._failover_log_last_ts: float = 0.0
        self._failover_log_last_sig: str = ""

    def _warn(self, key: str, msg: str) -> None:
        """Print a warning message at most once per `CAPTURE_WARN_EVERY_S` per key."""

        try:
            every = float(getattr(self, "_warn_every_s", 1.0) or 0.0)
        except Exception:
            every = 1.0

        try:
            now = float(time.time())
            last = float(self._warn_last.get(str(key), 0.0) or 0.0)
            if (now - last) >= float(every):
                self._warn_last[str(key)] = now
                print(str(msg))
        except Exception:
            pass

    def _compile_regex(self, pat: str) -> Optional[re.Pattern[str]]:
        try:
            s = str(pat or "").strip()
            if not s:
                return None
            return re.compile(s, re.IGNORECASE)
        except Exception:
            return None

    def _window_title_ok(self, title: str) -> bool:
        t = str(title or "").strip()
        if not t:
            return False
        inc = self._compile_regex(self._cfg_title_include)
        exc = self._compile_regex(self._cfg_title_exclude)
        try:
            if inc is not None and inc.search(t) is None:
                return False
        except Exception:
            pass
        try:
            if exc is not None and exc.search(t) is not None:
                return False
        except Exception:
            pass
        return True

    def _is_projector_target(self) -> bool:
        try:
            raw = (os.getenv("CAPTURE_TARGET", "client") or "client").strip().lower() or "client"
        except Exception:
            raw = "client"
        # Supported values (explicit): client|projector
        return raw in {"projector", "proj"}

    def _projector_title_hint(self) -> str:
        try:
            s = (os.getenv("CAPTURE_PROJECTOR_TITLE", self._cfg_projector_title) or self._cfg_projector_title).strip()
            return s if s else str(self._cfg_projector_title or "").strip()
        except Exception:
            return str(self._cfg_projector_title or "").strip()

    def _find_projector_window(self) -> tuple[int, str, Optional[int]]:
        """Return (hwnd, title, monitor_index) for the projector window (best-effort)."""

        title_hint = self._projector_title_hint()
        if not title_hint:
            return 0, "", None

        try:
            pat = self._compile_regex(title_hint)
        except Exception:
            pat = None

        for hwnd0, t0, _pid0 in (self._list_visible_windows() or []):
            tt = str(t0 or "")
            if not tt:
                continue
            ok = False
            try:
                if pat is not None:
                    ok = pat.search(tt) is not None
                else:
                    ok = title_hint.lower() in tt.lower()
            except Exception:
                ok = False
            if not ok:
                continue

            try:
                mi = self.find_window_monitor(int(hwnd0))
            except Exception:
                mi = None
            return int(hwnd0), tt, (int(mi) if mi is not None else None)

        return 0, "", None

    def resolve_forced_monitor(self, *, found_monitor: Optional[int], target: str) -> None:
        """Resolve forced monitor policies (projector-specific auto-adjust).

        - Keeps the existing out-of-range validation (handled elsewhere).
        - New: if target==projector and strict pin is enabled but the projector
          lives on another monitor index (MSS indices), auto-adjust the forced
          monitor to the projector's monitor.
        """

        try:
            tgt = str(target or "").strip().lower()
        except Exception:
            tgt = ""

        if tgt != "projector":
            return
        if found_monitor is None:
            return
        try:
            fm = getattr(self, "force_monitor", None)
            if fm is None:
                return
            fm_i = int(fm)
            found_i = int(found_monitor)
        except Exception:
            return

        try:
            if fm_i == found_i:
                return
        except Exception:
            return

        # Only adjust for the projector flow; never for client capture.
        try:
            self.force_monitor = int(found_i)
        except Exception:
            return

        if not bool(getattr(self, "_projector_adjust_logged", False)):
            try:
                print(
                    "ℹ️ forced_monitor ajustado al monitor del proyector: "
                    f"{int(fm_i)}->{int(found_i)} (indices internos MSS)."
                )
            except Exception:
                pass
            try:
                self._projector_adjust_logged = True
            except Exception:
                pass

    def _log_projector_switch_once(self, key: str, msg: str) -> None:
        try:
            if bool(self._projector_switch_logged.get(str(key), False)):
                return
            self._projector_switch_logged[str(key)] = True
            print(str(msg))
        except Exception:
            pass

    def _capture_projector(self) -> Optional[np.ndarray]:
        """Capture the OBS projector window.

        Primary target selection is by projector title (independent from Tibia client).
        Backend policy: try BitBlt first, then screen-crop, then fullscreen.
        Avoid infinite loops on black frames by switching backend at most twice.
        """

        hwnd_p, title_p, mon_p = self._find_projector_window()
        if hwnd_p:
            self.client_hwnd = int(hwnd_p)
            self.client_title = str(title_p or "")
            self.target_found = True
            self.target_reason = "projector"
            self.capture_target = "projector"
            try:
                if mon_p is None:
                    mon_p = self.find_window_monitor(int(hwnd_p))
            except Exception:
                pass

            # Auto-adjust forced monitor if strict pin is enabled but indices differ.
            try:
                if bool(self.strict_force_monitor):
                    self.resolve_forced_monitor(found_monitor=mon_p, target="projector")
            except Exception:
                pass

            try:
                l, t, r, b = win32gui.GetWindowRect(int(hwnd_p))
                self.capture_bounds = (int(l), int(t), int(r), int(b))
            except Exception:
                pass

            try:
                self.capture_monitor_index = int(mon_p) if mon_p is not None else None
            except Exception:
                self.capture_monitor_index = None
        else:
            # Projector not found yet.
            self.target_found = False
            self.target_reason = "projector_not_found"
            self.capture_target = "projector"
            self.capture_state = "projector_not_found"
            return None

        # Backends: stick to the chosen backend until we see repeated black frames.
        backend = str(getattr(self, "_projector_backend", "bitblt") or "bitblt")
        frame: Optional[np.ndarray] = None

        if backend == "bitblt":
            try:
                frame = self.capture_window(hwnd=int(hwnd_p))
            except Exception:
                frame = None
        elif backend == "screen_crop":
            try:
                if self.capture_bounds is not None:
                    l, t, r, b = self.capture_bounds
                    frame = self.capture_screen_crop(bounds=(int(l), int(t), int(r), int(b)))
            except Exception:
                frame = None
        else:
            # fullscreen
            try:
                pref = None
                if self.capture_monitor_index is not None:
                    pref = int(self.capture_monitor_index)
                elif self.force_monitor is not None:
                    pref = int(self.force_monitor)
                frame = self.capture_fullscreen(preferred_monitor=pref)
            except Exception:
                frame = None

        ok = False
        try:
            if frame is not None:
                ok = bool(self.validate_capture(frame, min_width=80, min_height=80))
        except Exception:
            ok = False

        if ok and frame is not None:
            # Stable projector state.
            mon_s = None
            try:
                mon_s = int(self.capture_monitor_index) if self.capture_monitor_index is not None else None
            except Exception:
                mon_s = None
            self.capture_state = f"projector@mon{int(mon_s)}" if mon_s is not None else "projector"
            self.capture_backend = f"projector_{backend}"
            return frame

        # Black / invalid capture: switch backend after a short streak.
        try:
            streak = int(getattr(self, "black_streak", 0) or 0)
        except Exception:
            streak = 0

        # Switch thresholds are conservative to avoid thrashing.
        if backend == "bitblt" and streak >= 3:
            self._projector_backend = "screen_crop"
            try:
                self.black_streak = 0
            except Exception:
                pass
            self._log_projector_switch_once(
                "switch_bitblt_to_screen_crop",
                "⚠️  projector: BitBlt inválido/negro; cambiando a screen_crop",
            )
        elif backend == "screen_crop" and streak >= 3:
            self._projector_backend = "fullscreen"
            try:
                self.black_streak = 0
            except Exception:
                pass
            self._log_projector_switch_once(
                "switch_screen_crop_to_fullscreen",
                "⚠️  projector: screen_crop inválido/negro; cambiando a fullscreen",
            )

        self.capture_backend = f"projector_{backend}"
        self.capture_state = "projector_retry"
        return None

    def _is_obs_like_title(self, title: str) -> bool:
        t = str(title or "")
        exc = self._compile_regex(self._cfg_title_exclude)
        try:
            return bool(exc is not None and exc.search(t) is not None)
        except Exception:
            return False

    def _get_process_name_by_pid(self, pid: int) -> str:
        """Best-effort process name resolution (no psutil dependency)."""
        try:
            h = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        except Exception:
            try:
                h = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, int(pid))
            except Exception:
                return ""
        try:
            # GetModuleFileNameEx is available in pywin32's win32process.
            path = ""
            try:
                path = str(win32process.GetModuleFileNameEx(h, 0) or "")
            except Exception:
                path = ""
            base = os.path.basename(path) if path else ""
            return str(base or "")
        except Exception:
            return ""
        finally:
            try:
                win32api.CloseHandle(h)
            except Exception:
                pass

    def _list_visible_windows(self) -> list[tuple[int, str, int]]:
        """Return [(hwnd, title, pid)] for visible top-level windows."""

        out: list[tuple[int, str, int]] = []

        def enum_handler(hwnd, ctx):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                title = win32gui.GetWindowText(hwnd) or ""
                try:
                    _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
                except Exception:
                    pid = 0
                ctx.append((int(hwnd), str(title), int(pid)))
            except Exception:
                return

        try:
            win32gui.EnumWindows(enum_handler, out)
        except Exception:
            return []
        return out

    def _pick_target_window(self) -> tuple[int, str, str]:
        """Pick a stable client window according to env policy.

        Returns: (hwnd, title, reason)
        """

        # If user explicitly wants OBS/projector, allow excluded titles.
        want_obs = str(self._cfg_target or "client").strip().lower() in {"obs", "projector", "obs_projector"}

        proc_name = str(self._cfg_process_name or "").strip().lower()
        inc = self._compile_regex(self._cfg_title_include)
        exc = self._compile_regex(self._cfg_title_exclude)

        candidates = self._list_visible_windows()

        def _title_matches(t: str) -> bool:
            if not t:
                return False
            try:
                if inc is not None and inc.search(t) is None:
                    return False
            except Exception:
                pass
            if not want_obs:
                try:
                    if exc is not None and exc.search(t) is not None:
                        return False
                except Exception:
                    pass
            return True

        # 1) process_name priority (if set)
        if proc_name:
            for hwnd, title, pid in candidates:
                try:
                    pn = self._get_process_name_by_pid(int(pid)).lower()
                except Exception:
                    pn = ""
                if pn and pn == proc_name:
                    # Optionally also enforce include/exclude for extra safety.
                    if _title_matches(str(title)) or want_obs:
                        return int(hwnd), str(title), "client_process_name"

        # 2) include/exclude title policy (client)
        for hwnd, title, _pid in candidates:
            if _title_matches(str(title)):
                return int(hwnd), str(title), "client_title"

        # 3) If user explicitly asked for OBS, allow projector-ish titles.
        if want_obs:
            for hwnd, title, _pid in candidates:
                try:
                    if inc is not None and inc.search(str(title)) is None:
                        continue
                except Exception:
                    pass
                # In OBS mode we *allow* exclude matches.
                return int(hwnd), str(title), "obs_title"

        return 0, "", "not_found"

    def _log_ok(self, msg: str) -> None:
        if not self._verbose:
            return
        now = time.time()
        # rate-limit para evitar spam
        if now - self._last_ok_log_ts < 2.0:
            return
        self._last_ok_log_ts = now
        try:
            print(msg)
        except Exception:
            pass

    def find_window(self) -> int:
        # Legacy matching (kept for compatibility) now defers to the robust
        # policy when available.
        try:
            hwnd, _title, _reason = self._pick_target_window()
            return int(hwnd or 0)
        except Exception:
            pass

        def enum_handler(hwnd, ctx):
            try:
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    title_l = str(title or "").lower()
                    for partial in self.title_partials:
                        if partial.lower() in title_l:
                            ctx.append(int(hwnd))
                            break
            except Exception:
                return

        hwnds: list[int] = []
        try:
            win32gui.EnumWindows(enum_handler, hwnds)
        except Exception:
            return 0
        return int(hwnds[0]) if hwnds else 0

    def reacquire_target(self) -> bool:
        """Try to (re)select the capture target window.

        - Never clears capture_backend.
        - Sets capture_state='reacquiring' when not found.
        - Updates client_hwnd/title/bounds on success.
        """

        try:
            # Refresh env live (so UI/scripts can change policy without restart).
            self._cfg_target = (os.getenv("CAPTURE_TARGET", "client") or "client").strip().lower() or "client"
            self._cfg_title_include = (os.getenv("CAPTURE_TITLE_INCLUDE", "Tibia") or "Tibia").strip()
            self._cfg_title_exclude = (
                os.getenv("CAPTURE_TITLE_EXCLUDE", "Proyector|Projector|OBS") or "Proyector|Projector|OBS"
            ).strip()
            self._cfg_process_name = (os.getenv("CAPTURE_PROCESS_NAME", "") or "").strip()
        except Exception:
            pass

        try:
            hwnd, title, reason = self._pick_target_window()
        except Exception as e:
            self.last_error = f"pick_target_error:{e}"
            self.fail_count += 1
            self.capture_state = "reacquiring"
            self.target_found = False
            self.target_reason = "reacquire_exception"
            return False

        if not hwnd:
            self.fail_count += 1
            self.target_found = False
            self.target_reason = str(reason or "not_found")
            self.capture_state = "reacquiring"
            return False

        try:
            # Validate hwnd still exists.
            if not bool(win32gui.IsWindow(int(hwnd))):
                self.fail_count += 1
                self.target_found = False
                self.target_reason = "hwnd_invalid"
                self.capture_state = "reacquiring"
                return False
        except Exception:
            pass

        self.client_hwnd = int(hwnd)
        self.client_title = str(title or "")
        self.target_found = True
        self.target_reason = str(reason or "reacquired")
        self.reacquire_count += 1

        # Best-effort bounds for window crop.
        try:
            l, t, r, b = win32gui.GetWindowRect(int(hwnd))
            self.capture_bounds = (int(l), int(t), int(r), int(b))
        except Exception:
            # Keep existing bounds if any.
            pass

        self.capture_state = "reacquired"
        self.last_error = ""
        return True

    def capture_fullscreen(self, preferred_monitor: Optional[int] = None) -> Optional[np.ndarray]:
        """Captura toda la pantalla usando MSS como fallback.

        Prioridad:
        1) monitor preferido (si es válido)
        2) monitor donde está la ventana del cliente (si se puede inferir)
        3) monitores 1..n
        4) monitor 0 (virtual combinado)
        """
        try:
            with mss() as sct:
                n_monitors = len(sct.monitors)

                # Preferred monitor hint (caller): this comes from window monitor inference.
                try:
                    pm = int(preferred_monitor) if preferred_monitor is not None else None
                except Exception:
                    pm = None

                # Last known-good monitor hint.
                try:
                    lg = int(self._last_good_monitor_index) if self._last_good_monitor_index is not None else None
                except Exception:
                    lg = None

                # Infer monitor strictly from the current capture target HWND.
                # IMPORTANT: do NOT fall back to the Tibia client window here,
                # otherwise OBS projector workflows can get incorrectly pinned
                # to the Tibia monitor.
                try:
                    window_monitor = self.find_window_monitor(self.client_hwnd)
                except Exception:
                    window_monitor = None
                if self._verbose and window_monitor is not None:
                    try:
                        print(f"Priorizando monitor {int(window_monitor)} donde está el capture target")
                    except Exception:
                        pass

                # OBS projector mode (common dual-monitor workflow): if we
                # cannot infer the monitor from HWND, prefer monitor 1 by default.
                obs_mode = False
                try:
                    obs_mode = str(self.capture_target or "").strip().lower() in {"obs_projector"} or str(
                        self.target_reason or ""
                    ).strip().lower() in {"obs_projector"}
                except Exception:
                    obs_mode = False
                try:
                    if not obs_mode:
                        obs_mode = (os.getenv("CAPTURE_TARGET", "") or "").strip().lower() in {"obs", "projector", "obs_projector"}
                except Exception:
                    pass

                try:
                    obs_fallback_monitor = int(float(os.getenv("CAPTURE_OBS_FALLBACK_MONITOR", "1").strip() or "1"))
                except Exception:
                    obs_fallback_monitor = 1

                # Only compute the expensive "active monitor" heuristic when:
                # - enabled
                # - we have no other stable hints
                # - and we're NOT in OBS projector mode (dual-monitor workflow)
                has_hint = False
                try:
                    has_hint = bool(pm is not None and pm != 0)
                except Exception:
                    pass
                try:
                    has_hint = bool(has_hint or (lg is not None and lg != 0))
                except Exception:
                    pass
                try:
                    has_hint = bool(has_hint or (window_monitor is not None and int(window_monitor) != 0))
                except Exception:
                    pass
                try:
                    if bool(obs_mode):
                        has_hint = bool(has_hint or (obs_fallback_monitor is not None and int(obs_fallback_monitor) != 0))
                except Exception:
                    pass

                active_best = None
                if bool(self._scan_active_monitor) and (not bool(has_hint)) and (not bool(obs_mode)):
                    try:
                        active_best = self._find_most_active_monitor_index(sct)
                    except Exception:
                        active_best = None

                monitor_indices = _build_monitor_priority(
                    n_monitors=n_monitors,
                    preferred_monitor=pm,
                    last_good_monitor=lg,
                    window_monitor=window_monitor,
                    obs_mode=bool(obs_mode),
                    obs_fallback_monitor=int(obs_fallback_monitor) if obs_fallback_monitor is not None else None,
                    scan_active_monitor=bool(self._scan_active_monitor),
                    active_monitor=active_best,
                )

                # Intentar capturar en cada monitor en orden de prioridad
                for i in monitor_indices:
                    try:
                        monitor = sct.monitors[i]
                        screenshot = sct.grab(monitor)
                        frame = np.frombuffer(screenshot.bgra, dtype=np.uint8)
                        frame = frame.reshape((screenshot.height, screenshot.width, 4))
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR).astype(np.uint8, copy=False)

                        # Verificar que la captura es válida antes de retornarla
                        if self.validate_capture(frame, min_width=800, min_height=600):
                            monitor_info = f"{monitor.get('width', 'unknown')}x{monitor.get('height', 'unknown')}"
                            if self._verbose:
                                print(f"Captura de pantalla completa exitosa en monitor {i} ({monitor_info}): {frame.shape}")
                            self.capture_monitor_index = int(i)
                            self._last_good_monitor_index = int(i)
                            return frame
                        else:
                            if self._verbose:
                                print(f"Monitor {i} no válido, intentando siguiente...")
                            continue

                    except Exception as e:
                        if self._verbose:
                            print(f"Error en monitor {i}: {e}")
                        continue

                if self._verbose:
                    print("No se pudo capturar en ningún monitor")
                self.capture_monitor_index = None
                return None

        except Exception as e:
            print(f"Fallback MSS falló completamente: {e}")
            return None

    def find_window_monitor(self, hwnd: Optional[int] = None) -> Optional[int]:
        """Encuentra el monitor donde está ubicada la ventana del cliente."""

        try:
            h = int(hwnd or 0)
        except Exception:
            h = 0
        if not h:
            try:
                h = int(self.client_hwnd or 0)
            except Exception:
                h = 0
        if not h:
            try:
                h = int(self.hwnd or 0)
            except Exception:
                h = 0
        if not h:
            return None

        try:
            # Obtener las coordenadas de la ventana
            rect = win32gui.GetWindowRect(int(h))
            window_x, window_y = rect[0], rect[1]
            window_width, window_height = rect[2] - rect[0], rect[3] - rect[1]
            if self._verbose:
                print(f"Ventana en posición: ({window_x}, {window_y}) tamaño: {window_width}x{window_height}")

            # Obtener información de todos los monitores
            with mss() as sct:
                if self._verbose:
                    print(f"Monitores disponibles: {len(sct.monitors)}")
                best_monitor = None
                best_overlap_ratio = 0

                for i, monitor in enumerate(sct.monitors):
                    mon_x, mon_y = monitor['left'], monitor['top']
                    mon_width, mon_height = monitor['width'], monitor['height']
                    if self._verbose:
                        print(f"  Monitor {i}: pos ({mon_x}, {mon_y}) tamaño {mon_width}x{mon_height}")

                    # Calcular overlap entre ventana y monitor
                    overlap_x = max(0, min(window_x + window_width, mon_x + mon_width) - max(window_x, mon_x))
                    overlap_y = max(0, min(window_y + window_height, mon_y + mon_height) - max(window_y, mon_y))
                    overlap_area = overlap_x * overlap_y

                    if overlap_area > 0:
                        monitor_area = mon_width * mon_height
                        overlap_ratio = overlap_area / monitor_area
                        if self._verbose:
                            print(f"    Overlap con monitor {i}: {overlap_ratio:.2f}")

                        if overlap_ratio > best_overlap_ratio:
                            best_overlap_ratio = overlap_ratio
                            best_monitor = i

                if best_monitor is not None:
                    # Avoid spamming: log only when verbose, or when the chosen
                    # monitor changes (and rate-limited).
                    try:
                        now = time.time()
                        changed = (self._last_window_monitor is None) or (int(best_monitor) != int(self._last_window_monitor))
                        if self._verbose or (changed and (now - float(self._last_window_monitor_log_ts or 0.0)) >= 2.0):
                            print(
                                f"Ventana asignada a monitor {best_monitor} (mejor overlap: {best_overlap_ratio:.2f})"
                            )
                            self._last_window_monitor_log_ts = float(now)
                        self._last_window_monitor = int(best_monitor)
                    except Exception:
                        pass
                    return best_monitor

                if self._verbose:
                    try:
                        now = time.time()
                        if (now - float(self._last_window_monitor_log_ts or 0.0)) >= 2.0:
                            print("Ventana no encontrada en ningún monitor")
                            self._last_window_monitor_log_ts = float(now)
                    except Exception:
                        pass

        except Exception as e:
            if self._verbose:
                try:
                    now = time.time()
                    if (now - float(self._last_window_monitor_log_ts or 0.0)) >= 2.0:
                        print(f"Error detectando monitor de ventana: {e}")
                        self._last_window_monitor_log_ts = float(now)
                except Exception:
                    pass

        return None

    def capture_specific_monitor(self, monitor_index: int) -> Optional[np.ndarray]:
        """Captura un monitor específico por índice"""
        try:
            with mss() as sct:
                if monitor_index >= len(sct.monitors):
                    print(f"Monitor {monitor_index} no existe")
                    return None

                monitor = sct.monitors[monitor_index]
                screenshot = sct.grab(monitor)
                frame = np.frombuffer(screenshot.bgra, dtype=np.uint8)
                frame = frame.reshape((screenshot.height, screenshot.width, 4))
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR).astype(np.uint8, copy=False)

                # Verificar que la captura es válida
                if self.validate_capture(frame, min_width=800, min_height=600):
                    monitor_info = f"{monitor.get('width', 'unknown')}x{monitor.get('height', 'unknown')}"
                    self._log_ok(
                        f"Captura de monitor específico exitosa en monitor {monitor_index} ({monitor_info}): {frame.shape}"
                    )
                    self._last_good_monitor_index = int(monitor_index)
                    return frame
                else:
                    if self._verbose:
                        print(f"Monitor {monitor_index} no válido")
                    return None

        except Exception as e:
            if self._verbose:
                print(f"Error capturando monitor {monitor_index}: {e}")
            return None

    def _find_most_active_monitor_index(self, sct) -> Optional[int]:
        """Devuelve el índice MSS (1..n) del monitor con más "actividad".

        Heurística: suma de std-dev por canal en una captura rápida.
        Es un fallback para setups multi-monitor cuando no podemos inferir
        el monitor por HWND/bounds.
        """

        best_i: Optional[int] = None
        best_score = 0.0

        for i in range(1, len(sct.monitors)):
            try:
                monitor = sct.monitors[i]
                screenshot = sct.grab(monitor)
                frame = np.frombuffer(screenshot.bgra, dtype=np.uint8)
                frame = frame.reshape((screenshot.height, screenshot.width, 4))
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR).astype(np.uint8, copy=False)

                # Si es inválida según nuestra validación de fullscreen, no cuenta.
                if not self.validate_capture(frame, min_width=800, min_height=600):
                    continue

                variation = 0.0
                try:
                    variation = float(sum(float(np.std(frame[:, :, c])) for c in range(3)))
                except Exception:
                    variation = 0.0

                if variation > best_score:
                    best_score = variation
                    best_i = int(i)
            except Exception:
                continue

        return best_i

    def capture(self) -> Optional[np.ndarray]:
        frame: Optional[np.ndarray] = None

        # --- Startup diagnostics (ONE-SHOT) ---
        # 1) List detected monitors (MSS indices) once.
        # 2) Validate forced monitor pin once; if out of range, auto-disable strict pin.
        try:
            if not bool(getattr(self, "_startup_monitors_logged", False)):
                with mss() as sct:
                    mons = list(getattr(sct, "monitors", []) or [])
                    n = int(len(mons))

                    def _device_name_for_rect(rect: tuple[int, int, int, int]) -> str:
                        try:
                            for hmon, _hdc, _rc in win32api.EnumDisplayMonitors():
                                info = win32api.GetMonitorInfo(hmon)
                                mon_rc = info.get("Monitor") if isinstance(info, dict) else None
                                dev = info.get("Device") if isinstance(info, dict) else None
                                if isinstance(mon_rc, (list, tuple)) and len(mon_rc) == 4:
                                    if tuple(int(x) for x in mon_rc) == tuple(int(x) for x in rect):
                                        return str(dev or "").strip()
                        except Exception:
                            return ""
                        return ""

                    try:
                        print(f"🖥️  MSS monitors detectados: {n} (válidos: 0..{max(0, n - 1)})")
                    except Exception:
                        pass

                    for i, m in enumerate(mons):
                        try:
                            left = int(m.get("left", 0))
                            top = int(m.get("top", 0))
                            width = int(m.get("width", 0))
                            height = int(m.get("height", 0))
                            rect = (left, top, left + width, top + height)
                            dev = _device_name_for_rect(rect)
                            name_s = f" name={dev}" if dev else ""
                            print(
                                f"  - idx={i} {width}x{height} bounds=({left},{top},{left + width},{top + height}){name_s}"
                            )
                        except Exception:
                            continue

                setattr(self, "_startup_monitors_logged", True)
        except Exception:
            # Never block capture for diagnostics.
            try:
                setattr(self, "_startup_monitors_logged", True)
            except Exception:
                pass

        try:
            if not bool(getattr(self, "_startup_forced_monitor_validated", False)):
                forced = getattr(self, "force_monitor", None)
                if forced is not None:
                    try:
                        forced_i = int(forced)
                    except Exception:
                        forced_i = None
                    if forced_i is not None:
                        with mss() as sct:
                            n = int(len(list(getattr(sct, "monitors", []) or [])))
                        if forced_i < 0 or forced_i >= n:
                            try:
                                print(
                                    "⛔ forced_monitor fuera de rango: "
                                    f"{forced_i} (monitores válidos: 0..{max(0, n - 1)}). "
                                    "Se desactiva strict pin automáticamente."
                                )
                            except Exception:
                                pass
                            try:
                                self.strict_force_monitor = False
                            except Exception:
                                pass
                            try:
                                self.force_monitor = None
                            except Exception:
                                pass
                setattr(self, "_startup_forced_monitor_validated", True)
        except Exception:
            try:
                setattr(self, "_startup_forced_monitor_validated", True)
            except Exception:
                pass

        # Always refresh capture target snapshot (best-effort) so we can monitor
        # even when the client is not foreground and/or gets recreated.
        try:
            if callable(update_capture_target_state):
                st = update_capture_target_state()
                self.capture_backend = str(getattr(st, "capture_backend", "dxgi") or "dxgi")
                self.capture_target = str(getattr(st, "capture_target", "auto") or "auto")
                self.client_hwnd = int(getattr(st, "target_hwnd", 0) or 0)
                self.client_title = str(getattr(st, "target_title", "") or "")
                self.capture_bounds = getattr(st, "target_bounds", None)
                self.target_found = bool(getattr(st, "target_found", False))
                self.target_reason = str(getattr(st, "reason", "") or "")
                if bool(getattr(st, "target_is_minimized", False)):
                    # IMPORTANT: never return None just because the chosen
                    # capture target window is minimized (OBS projector/client).
                    # That would starve the pipeline and cause STALE_GS.
                    # Instead, fall back to fullscreen capture (MSS).
                    self.capture_state = "minimized"
                    self.capture_monitor_index = None
                    self.capture_bounds = None
        except Exception:
            pass

        # Enforce robust target selection: prefer real client window by default.
        try:
            want_obs = (os.getenv("CAPTURE_TARGET", "client") or "client").strip().lower() in {
                "obs",
                "projector",
                "obs_projector",
            }
        except Exception:
            want_obs = False

        try:
            # If current selection looks like OBS/projector but user didn't ask
            # for it, override with client window selection.
            if (not bool(want_obs)) and self._is_obs_like_title(self.client_title):
                self.reacquire_target()
        except Exception:
            pass

        try:
            # If hwnd is missing/invalid, reacquire.
            h = int(self.client_hwnd or 0)
            if (not h) or (not bool(win32gui.IsWindow(h))):
                self.reacquire_target()
        except Exception:
            pass

        # Best-effort: infer monitor index from current client hwnd.
        try:
            self.capture_monitor_index = self.find_window_monitor(self.client_hwnd)
        except Exception:
            self.capture_monitor_index = None

        # --- Projector-first capture (CAPTURE_TARGET=projector) ---
        # In projector mode we must NOT depend on the Tibia client title, and we
        # must never reject the projector due to forced monitor index mismatch.
        try:
            if self._is_projector_target():
                # Refresh title hint dynamically (env can change).
                try:
                    self._cfg_projector_title = (
                        os.getenv(
                            "CAPTURE_PROJECTOR_TITLE",
                            "Proyector en ventana (Fuente) - Tibia_Fuente",
                        )
                        or "Proyector en ventana (Fuente) - Tibia_Fuente"
                    ).strip()
                except Exception:
                    pass

                fr = self._capture_projector()
                if fr is not None:
                    return fr

                # If the projector window isn't found, avoid starving the
                # pipeline by falling back to fullscreen capture.
                # If it IS found but was black/invalid, do not capture a
                # different source here; let the projector backend switching
                # happen in subsequent ticks.
                if not bool(getattr(self, "target_found", False)) or str(getattr(self, "target_reason", "") or "") in {
                    "projector_not_found",
                }:
                    pref = None
                    try:
                        if self.force_monitor is not None:
                            pref = int(self.force_monitor)
                    except Exception:
                        pref = None
                    if pref is None:
                        try:
                            if self.capture_monitor_index is not None:
                                pref = int(self.capture_monitor_index)
                        except Exception:
                            pref = None
                    self.capture_state = (
                        f"projector_fallback_fullscreen@mon{int(pref)}" if pref is not None else "projector_fallback_fullscreen"
                    )
                    return self.capture_fullscreen(preferred_monitor=pref)
                return None
        except Exception:
            # Continue with normal client capture pipeline.
            pass

        # When a monitor is forced (OBS pinning / FORCE_MONITOR), always try
        # capturing that monitor first.
        # This prevents accidental captures from a different monitor via
        # window-crops or fullscreen heuristics.
        if self.force_monitor is not None:
            try:
                fm = int(self.force_monitor)
            except Exception:
                fm = None
            if fm is not None:
                try:
                    mon = self.capture_specific_monitor(fm)
                    if mon is not None and self.validate_capture(mon):
                        self.capture_monitor_index = int(fm)
                        self.capture_state = f"monitor_forced_first@mon{int(fm)}"
                        return mon
                except Exception:
                    pass

                if bool(self.strict_force_monitor):
                    # Optional failover when strict forced monitor fails.
                    # This keeps strict-by-default safety but allows explicit
                    # workflows:
                    #   - CAPTURE_FAILOVER_MODE=scan_monitors
                    #   - CAPTURE_FAILOVER_MODE=projector (CAPTURE_PROJECTOR_TITLE)
                    try:
                        failover = (os.getenv("CAPTURE_FAILOVER_MODE", "") or "").strip().lower()
                    except Exception:
                        failover = ""

                    # One-shot / rate-limited log: makes it obvious when we
                    # entered strict forced-monitor failover.
                    try:
                        title_hint_dbg = ""
                        if failover in {"projector", "obs_projector", "window", "source"}:
                            title_hint_dbg = str(
                                (os.getenv("CAPTURE_PROJECTOR_TITLE", "Tibia_Fuente") or "Tibia_Fuente").strip()
                            )
                        sig = f"fm={fm}|failover={failover}|title={title_hint_dbg}"
                        now = float(time.time())
                        if (sig != str(getattr(self, "_failover_log_last_sig", "") or "")) or (
                            now - float(getattr(self, "_failover_log_last_ts", 0.0) or 0.0)
                        ) >= 2.0:
                            self._failover_log_last_sig = str(sig)
                            self._failover_log_last_ts = float(now)
                            msg = f"⚠️  CAPTURE failover: forced_monitor={fm} strict=1 mode={failover or 'none'}"
                            if title_hint_dbg:
                                msg = f"{msg} title='{title_hint_dbg}'"
                            try:
                                print(msg)
                            except Exception:
                                pass
                    except Exception:
                        pass

                    if failover in {"scan", "scan_monitors", "monitors"}:
                        try:
                            fr = self.capture_fullscreen(preferred_monitor=None)
                            if fr is not None and self.validate_capture(fr, min_width=800, min_height=600):
                                # capture_fullscreen sets capture_monitor_index.
                                mi = getattr(self, "capture_monitor_index", None)
                                self.capture_state = (
                                    f"monitor_forced_failed_scan@mon{int(mi)}" if mi is not None else "monitor_forced_failed_scan"
                                )
                                return fr
                        except Exception:
                            pass

                    if failover in {"projector", "obs_projector", "window", "source"}:
                        try:
                            title_hint = (os.getenv("CAPTURE_PROJECTOR_TITLE", "Tibia_Fuente") or "Tibia_Fuente").strip()
                        except Exception:
                            title_hint = "Tibia_Fuente"

                        # Safety policy: when we're in strict forced-monitor mode,
                        # only accept a projector window that lives on the forced
                        # monitor. This prevents accidentally capturing a window
                        # on another monitor (which the user explicitly wants to
                        # avoid in dual-monitor OBS setups).
                        allow_other_monitors = False
                        try:
                            raw = (os.getenv("CAPTURE_PROJECTOR_ALLOW_OTHER_MONITORS", "0") or "0").strip().lower()
                            allow_other_monitors = raw in {"1", "true", "yes", "y", "on"}
                        except Exception:
                            allow_other_monitors = False
                        enforce_projector_on_forced = bool(self.strict_force_monitor) and (fm is not None) and (not allow_other_monitors)

                        try:
                            # Find a visible window matching the hint (substring/regex).
                            hwnd_p = 0
                            title_p = ""
                            mon_p = None
                            wrong_mon_seen = None
                            try:
                                pat = self._compile_regex(title_hint)
                            except Exception:
                                pat = None

                            for hwnd0, t0, _pid0 in (self._list_visible_windows() or []):
                                tt = str(t0 or "")
                                if not tt:
                                    continue
                                ok = False
                                try:
                                    if pat is not None:
                                        ok = pat.search(tt) is not None
                                    else:
                                        ok = title_hint.lower() in tt.lower()
                                except Exception:
                                    ok = False
                                if ok:
                                    # If we're pinned to a specific monitor, only
                                    # accept projector windows on that monitor.
                                    try:
                                        mi0 = self.find_window_monitor(int(hwnd0))
                                    except Exception:
                                        mi0 = None
                                    if enforce_projector_on_forced and (mi0 is not None) and (int(mi0) != int(fm)):
                                        wrong_mon_seen = int(mi0)
                                        continue
                                    hwnd_p = int(hwnd0)
                                    title_p = tt
                                    mon_p = mi0
                                    break

                            if hwnd_p:
                                # Update target snapshot for diagnostics.
                                self.client_hwnd = int(hwnd_p)
                                self.client_title = str(title_p or "")
                                self.target_found = True
                                self.target_reason = "projector_failover"
                                self.capture_target = "obs_projector"
                                try:
                                    self.capture_monitor_index = (
                                        int(mon_p) if mon_p is not None else self.find_window_monitor(int(hwnd_p))
                                    )
                                except Exception:
                                    pass
                                try:
                                    l, t, r, b = win32gui.GetWindowRect(int(hwnd_p))
                                    self.capture_bounds = (int(l), int(t), int(r), int(b))
                                except Exception:
                                    self.capture_bounds = None

                                # Try BitBlt first (often works even when MSS crop is black).
                                try:
                                    bb = self.capture_window(hwnd=int(hwnd_p))
                                    if bb is not None and self.validate_capture(bb, min_width=80, min_height=80):
                                        self.capture_state = "projector_bitblt"
                                        return bb
                                except Exception:
                                    pass

                                # Then try desktop crop using window bounds.
                                try:
                                    if self.capture_bounds is not None:
                                        l, t, r, b = self.capture_bounds
                                        sc = self.capture_screen_crop(bounds=(int(l), int(t), int(r), int(b)))
                                        if sc is not None and self.validate_capture(sc, min_width=80, min_height=80):
                                            self.capture_state = "projector_screen_crop"
                                            return sc
                                except Exception:
                                    pass
                            else:
                                # Helpful state: projector title exists but is
                                # on a different monitor than the forced one.
                                if enforce_projector_on_forced and wrong_mon_seen is not None:
                                    try:
                                        self.capture_state = f"projector_wrong_monitor@mon{int(wrong_mon_seen)}"
                                    except Exception:
                                        self.capture_state = "projector_wrong_monitor"
                                    try:
                                        self.target_reason = "projector_wrong_monitor"
                                    except Exception:
                                        pass
                                    try:
                                        self._warn(
                                            "projector_wrong_monitor",
                                            (
                                                "⚠️  CAPTURE projector encontrado en monitor incorrecto: "
                                                f"forced_monitor={int(fm)} strict=1 found_monitor={int(wrong_mon_seen)} "
                                                f"title='{str(title_hint or '').strip()}' "
                                                "| mueve el proyector al monitor forzado o setea CAPTURE_PROJECTOR_ALLOW_OTHER_MONITORS=1"
                                            ),
                                        )
                                    except Exception:
                                        pass
                        except Exception:
                            pass

                    self.capture_monitor_index = int(fm)
                    self.capture_state = "monitor_forced_failed"
                    return None

        # Prefer window-crop capture when we have bounds.
        try:
            if self.capture_bounds is not None:
                l, t, r, b = self.capture_bounds
                w = max(1, int(r) - int(l))
                h = max(1, int(b) - int(t))
                # Sanity bounds: avoid absurd sizes.
                if w >= 80 and h >= 80:
                    with mss() as sct:
                        shot = sct.grab({"left": int(l), "top": int(t), "width": int(w), "height": int(h)})
                        frame = np.frombuffer(shot.bgra, dtype=np.uint8)
                        frame = frame.reshape((shot.height, shot.width, 4))
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR).astype(np.uint8, copy=False)
                        # Window crops can be smaller than full monitors.
                        if frame is not None and frame.size > 0 and frame.shape[1] >= 80 and frame.shape[0] >= 80:
                            # IMPORTANT: window-crop capture can return black/empty
                            # content even when the window exists (occlusion/minimized/
                            # GPU surface quirks). Validate and fall back to fullscreen.
                            if self.validate_capture(frame, min_width=80, min_height=80):
                                if self.capture_monitor_index is not None:
                                    self.capture_state = f"window_crop@mon{int(self.capture_monitor_index)}"
                                else:
                                    self.capture_state = "window_crop"
                                return frame
                            if self._verbose:
                                print("Window-crop inválido; fallback a fullscreen")
                            self.capture_state = "window_crop_invalid"

                            # Fallback #1: try BitBlt against the target HWND.
                            # This can work when MSS returns a black surface.
                            try:
                                bb = self.capture_window(hwnd=self.client_hwnd)
                                if bb is not None and self.validate_capture(bb, min_width=80, min_height=80):
                                    self.capture_state = "bitblt"
                                    return bb
                            except Exception:
                                pass

                            # Fallback #2 (most general): capture from the desktop
                            # DC (screen) and crop to the window bounds.
                            # This works even when window DC capture fails.
                            try:
                                sc = self.capture_screen_crop(bounds=(int(l), int(t), int(r), int(b)))
                                if sc is not None and self.validate_capture(sc, min_width=80, min_height=80):
                                    self.capture_state = "screen_crop"
                                    return sc
                            except Exception:
                                pass
        except Exception:
            # Fall back to monitor/fullscreen capture.
            pass

        # Prefer forced monitor (if set) for fullscreen capture, otherwise
        # use the inferred window monitor.
        pref = None
        try:
            if self.force_monitor is not None:
                pref = int(self.force_monitor)
            else:
                pref = self.capture_monitor_index
        except Exception:
            pref = self.capture_monitor_index
        self.capture_state = f"fullscreen@mon{int(pref)}" if pref is not None else "fullscreen"
        return self.capture_fullscreen(preferred_monitor=pref)

    def capture_window(self, hwnd: Optional[int] = None) -> Optional[np.ndarray]:
        """Captura una ventana específica usando BitBlt.

        Nota: BitBlt puede funcionar cuando MSS entrega frames negros.
        """
        start = time.time()
        frame = None
        wDC = dcObj = cDC = bitmap = None
        try:
            h = int(hwnd or 0)
            if not h:
                try:
                    h = int(self.client_hwnd or 0)
                except Exception:
                    h = 0
            if not h:
                try:
                    h = int(self.hwnd or 0)
                except Exception:
                    h = 0
            if not h:
                return None

            wDC = win32gui.GetWindowDC(h)
            dcObj = win32ui.CreateDCFromHandle(wDC)
            cDC = dcObj.CreateCompatibleDC()
            rect = win32gui.GetClientRect(h)
            width, height = rect[2], rect[3]
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(dcObj, width, height)
            cDC.SelectObject(bitmap)
            cDC.BitBlt((0, 0), (width, height), dcObj, (0, 0), win32con.SRCCOPY)
            signedIntsArray = bitmap.GetBitmapBits(True)
            img = np.frombuffer(signedIntsArray, dtype='uint8')
            img.shape = (height, width, 4)
            frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        except Exception as e:
            self._warn("bitblt_failed", f"BitBlt failed: {e}")
        finally:
            try:
                if wDC is not None and (hwnd or self.client_hwnd or self.hwnd):
                    try:
                        h = int(hwnd or self.client_hwnd or self.hwnd or 0)
                    except Exception:
                        h = 0
                    if h:
                        win32gui.ReleaseDC(h, wDC)
            except:
                pass
            try:
                if dcObj is not None:
                    dcObj.DeleteDC()
            except:
                pass
            try:
                if cDC is not None:
                    cDC.DeleteDC()
            except:
                pass
            try:
                if bitmap is not None:
                    win32gui.DeleteObject(bitmap.GetHandle())
            except:
                pass

        if frame is not None:
            self.latency_ms = (time.time() - start) * 1000
            self.fps = 1 / (self.latency_ms / 1000) if self.latency_ms > 0 else 0
        return frame

    def capture_screen_crop(self, *, bounds: tuple[int, int, int, int]) -> Optional[np.ndarray]:
        """Captura el escritorio (screen DC) y recorta a `bounds`.

        `bounds` debe estar en coordenadas de pantalla (virtual screen), igual
        que las que entrega `update_capture_target_state`.
        """

        start = time.time()
        frame: Optional[np.ndarray] = None
        wDC = dcObj = cDC = bitmap = None

        try:
            l, t, r, b = bounds
            w = max(1, int(r) - int(l))
            h = max(1, int(b) - int(t))
            if w < 80 or h < 80:
                return None

            # Virtual screen origin (can be negative on multi-monitor setups).
            vx = int(win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN))
            vy = int(win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN))

            src_x = int(l) - int(vx)
            src_y = int(t) - int(vy)

            # Desktop DC
            wDC = win32gui.GetDC(0)
            dcObj = win32ui.CreateDCFromHandle(wDC)
            cDC = dcObj.CreateCompatibleDC()

            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(dcObj, int(w), int(h))
            cDC.SelectObject(bitmap)

            # Copy only the region we need (avoid huge virtual-screen bitmaps).
            cDC.BitBlt((0, 0), (int(w), int(h)), dcObj, (int(src_x), int(src_y)), win32con.SRCCOPY)

            signedIntsArray = bitmap.GetBitmapBits(True)
            img = np.frombuffer(signedIntsArray, dtype="uint8")
            img.shape = (int(h), int(w), 4)
            frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        except Exception as e:
            if self._verbose:
                try:
                    print(f"Screen-crop failed: {e}")
                except Exception:
                    pass
            frame = None
        finally:
            try:
                if wDC is not None:
                    win32gui.ReleaseDC(0, wDC)
            except Exception:
                pass
            try:
                if dcObj is not None:
                    dcObj.DeleteDC()
            except Exception:
                pass
            try:
                if cDC is not None:
                    cDC.DeleteDC()
            except Exception:
                pass
            try:
                if bitmap is not None:
                    win32gui.DeleteObject(bitmap.GetHandle())
            except Exception:
                pass

        if frame is not None:
            self.latency_ms = (time.time() - start) * 1000
            self.fps = 1 / (self.latency_ms / 1000) if self.latency_ms > 0 else 0
        return frame

    def validate_capture(self, frame: np.ndarray, *, min_width: int = 800, min_height: int = 600) -> bool:
        """Valida que la captura contiene contenido real y no es negra/vacía.

        `min_width/min_height` permite validar crops de ventana más pequeños
        sin forzar resolución de monitor.
        """
        if frame is None or frame.size == 0:
            return False

        # Verificar que no es una imagen completamente negra
        if np.all(frame == 0):
            try:
                self.last_frame_mean = 0.0
            except Exception:
                pass
            try:
                self.black_streak = int(getattr(self, "black_streak", 0) or 0) + 1
            except Exception:
                pass
            self._warn("black_frame", "Warning: Captura completamente negra detectada")
            return False

        # Verificar si el frame es prácticamente negro en promedio.
        # Esto es un heuristic muy efectivo para el caso típico de "capturé el
        # monitor equivocado" (pantalla negra con mínimos píxeles no-cero).
        # Se usa un umbral conservador por defecto y es configurable.
        try:
            try:
                mean_thr = float((os.getenv("CAPTURE_BLACK_MEAN_THR", "5.0") or "5.0").strip() or "5.0")
            except Exception:
                mean_thr = 5.0
            mean_thr = float(max(0.0, min(255.0, mean_thr)))
            if mean_thr > 0.0:
                try:
                    mean_val = float(frame.mean())
                except Exception:
                    mean_val = 255.0

                try:
                    self.last_frame_mean = float(mean_val)
                except Exception:
                    pass

                if mean_val < mean_thr:
                    try:
                        self.black_streak = int(getattr(self, "black_streak", 0) or 0) + 1
                    except Exception:
                        pass
                    self._warn(
                        "black_mean",
                        f"Warning: Captura mayoritariamente negra (mean={mean_val:.2f} thr={mean_thr:.2f})",
                    )
                    return False
        except Exception:
            pass

        # Verificar si la imagen está mayoritariamente negra (típico de ventana
        # minimizada/oculta o captura del monitor equivocado en setups multi-monitor).
        # Esto ayuda a disparar failover (projector/scan) en modo strict.
        try:
            try:
                ratio_thr = float((os.getenv("CAPTURE_MOSTLY_BLACK_RATIO", "0.985") or "0.985").strip() or "0.985")
            except Exception:
                ratio_thr = 0.985
            ratio_thr = min(1.0, max(0.0, float(ratio_thr)))

            try:
                luma_thr = int(float((os.getenv("CAPTURE_MOSTLY_BLACK_LUMA", "8") or "8").strip() or "8"))
            except Exception:
                luma_thr = 8
            luma_thr = int(min(255, max(0, luma_thr)))

            try:
                stride = int(float((os.getenv("CAPTURE_MOSTLY_BLACK_STRIDE", "4") or "4").strip() or "4"))
            except Exception:
                stride = 4
            stride = int(max(1, min(16, stride)))

            # Sample the frame to keep it cheap.
            sample = frame[::stride, ::stride]
            if sample is not None and sample.size:
                try:
                    gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY) if len(sample.shape) == 3 else sample
                    black_ratio = float(np.mean(gray <= luma_thr))
                except Exception:
                    black_ratio = 0.0

                if ratio_thr < 1.0 and black_ratio >= ratio_thr:
                    try:
                        self.black_streak = int(getattr(self, "black_streak", 0) or 0) + 1
                    except Exception:
                        pass
                    self._warn(
                        "mostly_black_frame",
                        f"Warning: Captura mayoritariamente negra detectada (black_ratio={black_ratio:.3f} thr={ratio_thr:.3f})",
                    )
                    return False
        except Exception:
            pass

        # Verificar que no es una imagen uniforme (todos los píxeles iguales)
        if np.all(frame == frame[0, 0]):
            self._warn("uniform_frame", "Warning: Captura uniforme detectada (todos los píxeles iguales)")
            return False

        # Verificar que tiene variación de color (no es monocromática)
        if len(frame.shape) == 3:
            # Para imágenes RGB/BGR
            std_per_channel = [np.std(frame[:, :, i]) for i in range(3)]
            if all(std < 1.0 for std in std_per_channel):  # Muy poca variación
                self._warn("low_variation", "Warning: Captura con muy poca variación de color")
                return False

        # Verificar resolución mínima
        if frame.shape[1] < min_width or frame.shape[0] < min_height:
            self._warn("low_resolution", f"Warning: Resolución demasiado baja: {frame.shape}")
            return False

        # If we reached here, the frame is considered valid.
        try:
            self.black_streak = 0
        except Exception:
            pass
        return True