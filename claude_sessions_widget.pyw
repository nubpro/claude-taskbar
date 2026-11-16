"""Claude sessions widget: shows every Claude session on this PC and whether it
is working, just finished, or idle, either docked on the Windows taskbar or as a
small floating always-on-top window.

Reads the status files Claude writes to %USERPROFILE%\\.claude\\sessions\\*.json
(only the .json files; the .key files next to them are never opened).

Click a session to open it in the Claude desktop app.
Taskbar mode: drag sideways to move it along the taskbar.
Floating mode: drag the header to move, "–" to collapse, "×" to close.
Right-click anywhere for: dock/float, show idle, opacity, transparency, exit.
"""

import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from pathlib import Path

POLL_MS = 3000
PR_POLL_S = 90              # how often each session's pull request is re-checked with gh
TOPMOST_MS = 300            # how often the docked widget re-asserts itself above the taskbar
DONE_SECONDS = 120          # how long a "done" badge stays after busy -> idle
MAX_ROWS = 6                # most sessions shown; working first, then just-finished, then most recent
FLOAT_WIDTH = 240
CELL_W = 260                # width of one session in taskbar mode
TRAY_GAP = 40               # space kept between the widget and the tray's ^ arrow
TB_ROWS = 3                 # sessions stacked per column on the taskbar
HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
PROJECTS = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
SESSIONS = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "sessions"

SOLID_BG = "#1f1e1d"
SOLID_HEADER_BG = "#2a2927"
KEY = "#0b0b0a"             # this exact colour is drawn see-through
ROW_HOVER = "#33312e"
CURRENT_BG = "#3b3833"         # the session you are most likely looking at
CURRENT_HOVER = "#46423c"
FG = "#ebe9e4"
DIM = "#a7a49c"
ORANGE = "#d97757"
GREEN = "#6fbf73"
GREY = "#7a776f"
RED = "#e5534b"
PURPLE = "#a371f7"
AMBER = "#e0b44c"
FONT = ("Segoe UI", 8)
FONT_BOLD = ("Segoe UI Semibold", 8)
FONT_SMALL = ("Segoe UI", 7)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


# ---------- Windows helpers ----------

def single_instance() -> bool:
    try:
        kernel32.CreateMutexW(None, False, "ClaudeSessionsWidgetMutex")
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        kernel32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    except Exception:
        return True


def dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def window_rect(hwnd) -> tuple[int, int, int, int] | None:
    if not hwnd:
        return None
    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return r.left, r.top, r.right, r.bottom


def taskbar_geometry() -> tuple[tuple[int, int, int, int], int] | None:
    """(taskbar rect, x where the tray icons start) for the main taskbar."""
    tray = user32.FindWindowW("Shell_TrayWnd", None)
    bar = window_rect(tray)
    if not bar:
        return None
    notify = window_rect(user32.FindWindowExW(tray, 0, "TrayNotifyWnd", None))
    tray_left = notify[0] if notify and notify[2] > notify[0] else bar[2] - 260
    return bar, tray_left


SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                 "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow", "TopLevelWindowForOverflowXamlIsland",
                 "CEF-OSC-WIDGET", "TkTopLevel"}
SHELL_PROCESSES = {"explorer.exe", "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
                   "textinputhost.exe", "nvidia overlay.exe", "lockapp.exe", "pythonw.exe", "python.exe"}


def process_name(hwnd) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def foreground_is_fullscreen() -> bool:
    """True when a full-screen app (video, game, presentation) is in front.

    Two signals, both needed because each alone misfires on this PC:
    - an exclusive full-screen game, as Windows itself reports it (QUNS_RUNNING_D3D_FULL_SCREEN);
    - or the active window really fills the whole screen and is not part of Windows itself.
      Start, search, Alt+Tab, the desktop, the emoji panel and the NVIDIA overlay are all
      screen-sized, and Windows even reports "busy" while Start or Win+D is up, so those are ignored.
    """
    state = ctypes.c_int(0)
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0 and state.value == 3:
            return True
    except Exception:
        pass
    fg = user32.GetForegroundWindow()
    if not fg:
        return False
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(fg, buf, 128)
    if buf.value in SHELL_CLASSES or process_name(fg) in SHELL_PROCESSES:
        return False
    r = window_rect(fg)
    if not r:
        return False
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return r[0] <= 0 and r[1] <= 0 and r[2] >= sw and r[3] >= sh


def open_session(host_session_id: str) -> None:
    """Ask the Claude desktop app to switch to this session (its own deep link)."""
    if re.fullmatch(r"local_[A-Za-z0-9-]{1,64}", host_session_id):
        try:
            os.startfile(f"claude://code/continue?session={host_session_id}")
        except OSError:
            pass


# ---------- pull requests ----------

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: no console flashes from git / gh


def run_quiet(args: list[str], cwd: str, timeout: int = 20) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              timeout=timeout, creationflags=NO_WINDOW)
    except Exception:
        return None


def pr_badge(cwd: str) -> dict | None:
    """The PR for the branch checked out in cwd, as {text, color}; None when there is none."""
    if not cwd or not Path(cwd).is_dir():
        return None
    head = run_quiet(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd, 5)
    branch = head.stdout.strip() if head and head.returncode == 0 else ""
    if not branch or branch == "HEAD":
        return None
    r = run_quiet(["gh", "pr", "view", "--json", "number,state,isDraft,statusCheckRollup"], cwd)
    if not r or r.returncode != 0:
        return None
    try:
        pr = json.loads(r.stdout)
    except Exception:
        return None
    state = pr.get("state")
    if state == "MERGED":
        return {"kind": "merged"}
    if state == "CLOSED":
        return {"kind": "closed"}
    if pr.get("isDraft"):
        return {"kind": "draft"}
    failing = pending = False
    for c in pr.get("statusCheckRollup") or []:
        concl = (c.get("conclusion") or c.get("state") or "").upper()
        if concl in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"):
            failing = True
        elif concl in ("", "PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS"):
            pending = True
    if failing:
        return {"kind": "fail"}
    if pending:
        return {"kind": "pending"}
    return {"kind": "pass"}


class PrWatcher:
    """Looks up each session's PR on a background thread, so the widget never waits on gh."""

    def __init__(self) -> None:
        self.badges: dict[str, dict | None] = {}
        self.checked: dict[str, float] = {}
        self.wanted: set[str] = set()
        self.lock = threading.Lock()
        threading.Thread(target=self.loop, daemon=True).start()

    def want(self, cwds: list[str]) -> None:
        with self.lock:
            self.wanted = set(cwds)

    def get(self, cwd: str) -> dict | None:
        with self.lock:
            return self.badges.get(cwd)

    def loop(self) -> None:
        while True:
            with self.lock:
                due = [c for c in self.wanted if time.time() - self.checked.get(c, 0) > PR_POLL_S]
            for cwd in due:
                badge = pr_badge(cwd)
                with self.lock:
                    self.badges[cwd] = badge
                    self.checked[cwd] = time.time()
            time.sleep(2)


def draw_pr_icon(kind: str, size: int) -> "Image.Image":
    """A smooth PR status icon: drawn 6x larger, then scaled down for clean edges."""
    from PIL import Image, ImageDraw

    k = 6
    n = size * k
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = k // 2
    box = (pad, pad, n - 1 - pad, n - 1 - pad)
    white = (255, 255, 255, 255)
    stroke = max(2, round(n * 0.13))
    u = n / 48  # draw in a 48-unit space

    def pt(x, y):
        return (x * u, y * u)

    color = {"pass": GREEN, "fail": RED, "pending": AMBER, "merged": PURPLE,
             "closed": GREY, "draft": GREY}[kind]
    if kind in ("pending", "draft"):
        d.ellipse(box, outline=color, width=stroke)
        if kind == "pending":
            r = n * 0.17
            d.ellipse((n / 2 - r, n / 2 - r, n / 2 + r, n / 2 + r), fill=color)
    else:
        d.ellipse(box, fill=color)
        if kind in ("pass", "merged"):
            d.line([pt(13, 25), pt(21, 33), pt(35, 16)], fill=white, width=stroke, joint="curve")
        elif kind in ("fail", "closed"):
            d.line([pt(16, 16), pt(32, 32)], fill=white, width=stroke)
            d.line([pt(32, 16), pt(16, 32)], fill=white, width=stroke)
    return img.resize((size, size), Image.LANCZOS)


def last_typed_at(path: Path) -> float:
    """When you last sent a message in this session (epoch seconds), read from the end of its transcript."""
    from datetime import datetime

    size = path.stat().st_size
    span = 256 * 1024
    with open(path, "rb") as f:
        while True:
            start = max(0, size - span)
            f.seek(start)
            lines = f.read(size - start).split(b"\n")
            if start > 0:
                lines = lines[1:]  # first line is probably cut in half
            for raw in reversed(lines):
                if b'"kind":"human"' not in raw or b'"type":"user"' not in raw:
                    continue
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                if d.get("type") == "user" and (d.get("origin") or {}).get("kind") == "human":
                    try:
                        return datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
                    except Exception:
                        return 0.0
            if start == 0 or span >= 8 * 1024 * 1024:
                return 0.0
            span *= 4


class PromptWatcher:
    """Keeps, per session, when you last typed in it; re-reads a transcript only when it changes."""

    def __init__(self) -> None:
        self.typed: dict[str, float] = {}
        self.paths: dict[str, Path | None] = {}
        self.mtimes: dict[str, float] = {}
        self.wanted: set[str] = set()
        self.lock = threading.Lock()
        threading.Thread(target=self.loop, daemon=True).start()

    def want(self, session_ids: list[str]) -> None:
        with self.lock:
            self.wanted = set(session_ids)

    def get(self, session_id: str) -> float:
        with self.lock:
            return self.typed.get(session_id, 0.0)

    def loop(self) -> None:
        while True:
            with self.lock:
                ids = list(self.wanted)
            for sid in ids:
                try:
                    path = self.paths.get(sid)
                    if path is None or not path.exists():
                        path = next(iter(PROJECTS.glob(f"*/{sid}.jsonl")), None)
                        self.paths[sid] = path
                    if path is None:
                        continue
                    mtime = path.stat().st_mtime
                    if self.mtimes.get(sid) == mtime:
                        continue
                    when = last_typed_at(path)
                    with self.lock:
                        self.typed[sid] = when
                        self.mtimes[sid] = mtime
                except Exception:
                    continue
            time.sleep(2)


def fit(text: str, font: tkfont.Font, max_px: int) -> str:
    """Cut text with an ellipsis so it fits in max_px."""
    if max_px <= 0:
        return ""
    if font.measure(text) <= max_px:
        return text
    while text and font.measure(text + "\u2026") > max_px:
        text = text[:-1]
    return text.rstrip() + "\u2026"


# ---------- data ----------

def load_sessions() -> list[dict]:
    rows = []
    if not SESSIONS.is_dir():
        return rows
    for f in SESSIONS.glob("*.json"):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue  # mid-write or malformed; try again next tick
        pid = int(raw.get("pid") or 0)
        if not pid_alive(pid):
            continue
        cwd = str(raw.get("cwd") or "")
        rows.append({
            "id": str(raw.get("sessionId") or pid),
            "host": str(raw.get("hostSessionId") or ""),
            "name": str(raw.get("name") or "(untitled session)"),
            "status": str(raw.get("status") or "unknown"),
            "folder": Path(cwd).name or cwd,
            "cwd": cwd,
            "since": float(raw.get("statusUpdatedAt") or raw.get("updatedAt") or f.stat().st_mtime * 1000) / 1000,
        })
    return rows


def ago(seconds: float) -> str:
    m = max(0, int(seconds)) // 60
    if m < 1:
        return "<1m"
    if m < 60:
        return f"{m}m"
    h = m // 60
    if h < 48:
        return f"{h}h {m % 60}m" if h < 10 else f"{h}h"
    return f"{h // 24}d"


def shorten(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------- UI ----------

class Widget:
    def __init__(self) -> None:
        self.cfg = self.load_cfg()
        self.prev_status: dict[str, str] = {}
        self.done_at: dict[str, float] = {}
        self.rows: list[dict] = []
        self.ticks = 0
        self._layout = None
        self._subs: list[tk.Label] = []
        self._hidden = False
        self._fullscreen_hits = 0
        self._docked_at = None
        self._redock_ticks = 0
        self._hwnd = None
        self.prs = PrWatcher()
        self.prompts = PromptWatcher()
        self.clicked: dict[str, float] = {}
        self.current_id = ""

        self.root = tk.Tk()
        self.root.title("Claude Taskbar")
        try:
            self.root.iconbitmap(default=str(HERE / "claude_taskbar.ico"))
        except tk.TclError:
            pass  # icon file missing: keep Tk's default
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", KEY)
        self.name_font = tkfont.Font(root=self.root, font=FONT)
        self.bold_font = tkfont.Font(root=self.root, font=FONT_BOLD)
        self.small_font = tkfont.Font(root=self.root, font=FONT_SMALL)
        self.icon_size = max(10, round(12 * self.root.winfo_fpixels("1i") / 96))
        self.icons: dict = {}
        try:
            from PIL import ImageTk
            for kind in ("pass", "fail", "pending", "merged", "closed", "draft"):
                self.icons[kind] = ImageTk.PhotoImage(draw_pr_icon(kind, self.icon_size), master=self.root)
        except Exception:
            self.icons = {}  # no Pillow: PR icons are simply left out

        # right-click menu
        self.menu = tk.Menu(self.root, tearoff=0)
        self.docked = tk.BooleanVar(value=self.cfg["mode"] == "taskbar")
        self.menu.add_checkbutton(label="Dock on taskbar", variable=self.docked, command=self.on_toggle_mode)
        self.show_idle = tk.BooleanVar(value=self.cfg["show_idle"])
        self.menu.add_checkbutton(label="Show idle sessions", variable=self.show_idle, command=self.on_toggle_idle)
        self.menu.add_separator()
        self.transparent = tk.BooleanVar(value=self.cfg["transparent"])
        self.menu.add_checkbutton(label="Floating: fully transparent background", variable=self.transparent,
                                  command=self.on_toggle_transparent)
        op = tk.Menu(self.menu, tearoff=0)
        for a in (1.0, 0.9, 0.8, 0.7, 0.6):
            op.add_command(label=f"{int(a * 100)}%", command=lambda a=a: self.set_alpha(a))
        self.menu.add_cascade(label="Floating: opacity", menu=op)
        self.menu.add_command(label="Reset position", command=self.reset_position)
        self.menu.add_separator()
        self.menu.add_command(label="Exit", command=self.quit)
        self.root.bind_all("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))

        self.build()
        self.refresh()
        self.keep_on_top()
        self.watch_window_switches()

    # ----- config -----
    def load_cfg(self) -> dict:
        cfg = {"mode": "taskbar", "alpha": 0.8, "collapsed": False, "show_idle": True,
               "transparent": False, "x": None, "y": None, "tb_right": None}
        try:
            cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
        except Exception:
            pass
        return cfg

    def save_cfg(self) -> None:
        if self.cfg["mode"] == "taskbar":
            self.cfg["tb_right"] = self.root.winfo_x() + self.root.winfo_width()
        else:
            self.cfg["x"], self.cfg["y"] = self.root.winfo_x(), self.root.winfo_y()
        try:
            CONFIG.write_text(json.dumps(self.cfg, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ----- building the window for the current mode -----
    @property
    def bg(self) -> str:
        if self.cfg["mode"] == "taskbar" or self.cfg["transparent"]:
            return KEY
        return SOLID_BG

    def build(self) -> None:
        for child in self.root.winfo_children():
            if child is not self.menu:
                child.destroy()
        self._layout = None
        self._subs = []
        docked = self.cfg["mode"] == "taskbar"
        self.root.attributes("-alpha", 1.0 if docked else self.cfg["alpha"])
        self.root.configure(bg=self.bg, highlightthickness=0 if (docked or self.cfg["transparent"]) else 1,
                            highlightbackground="#3a3936")
        self.root.minsize(1, 1)
        if docked:
            self.header = None
            self.body = tk.Frame(self.root, bg=self.bg)
            self.body.pack(fill="both", expand=True)
            self.place_on_taskbar()
        else:
            self.build_float_header()
            self.body = tk.Frame(self.root, bg=self.bg, width=FLOAT_WIDTH)
            if not self.cfg["collapsed"]:
                self.body.pack(fill="both")
            x, y = self.cfg.get("x"), self.cfg.get("y")
            if x is None or y is None:
                x, y = self.root.winfo_screenwidth() - FLOAT_WIDTH - 24, 24
            self.root.minsize(FLOAT_WIDTH, 1)
            self.root.geometry("")  # drop the taskbar strip's fixed size; fit the rows again
            self.root.geometry(f"+{x}+{y}")
        self.draw()

    def build_float_header(self) -> None:
        hbg = KEY if self.cfg["transparent"] else SOLID_HEADER_BG
        self.header = tk.Frame(self.root, bg=hbg)
        self.header.pack(fill="x")
        title = tk.Label(self.header, text="Claude", bg=hbg, fg=FG, font=FONT_BOLD, anchor="w", padx=6, pady=0)
        title.pack(side="left")
        self.summary = tk.Label(self.header, text="", bg=hbg, fg=DIM, font=FONT_SMALL, anchor="w")
        self.summary.pack(side="left", fill="x", expand=True)
        close = tk.Label(self.header, text="×", bg=hbg, fg=DIM, font=("Segoe UI", 9), padx=5, cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.quit())
        collapse = tk.Label(self.header, text="–", bg=hbg, fg=DIM, font=("Segoe UI", 9), padx=2, cursor="hand2")
        collapse.pack(side="right")
        collapse.bind("<Button-1>", lambda e: self.toggle_collapse())
        for w in (self.header, title, self.summary):
            w.bind("<ButtonPress-1>", self.float_drag_start)
            w.bind("<B1-Motion>", self.float_drag)
            w.bind("<ButtonRelease-1>", lambda e: self.save_cfg())
            w.bind("<Double-Button-1>", lambda e: self.toggle_collapse())

    def taskbar_target(self) -> tuple[int, int, int, int] | None:
        """Where the docked strip belongs right now: (width, height, x, y); None if there is no taskbar yet."""
        geo = taskbar_geometry()
        if not geo:
            return None
        (left, top, right, bottom), tray_left = geo
        if bottom - top <= 0 or right - left <= 0:
            return None
        h = bottom - top
        cols = max(1, -(-min(MAX_ROWS, max(1, len(self.visible_specs()))) // TB_ROWS))
        w = CELL_W * cols
        r = self.cfg.get("tb_right")
        if r is None:
            r = tray_left - TRAY_GAP
        x = max(left, min(int(r) - w, right - w))
        return w, h, x, top

    def place_on_taskbar(self) -> int:
        """Size and place the docked strip on the taskbar; returns its height."""
        t = self.taskbar_target()
        if not t:
            return 48  # no taskbar yet (just signed in): redock() moves us once it appears
        w, h, x, y = t
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self._docked_at = t
        return h

    def redock(self) -> None:
        """Follow the taskbar: it may not exist yet right after sign-in, or may move or resize later."""
        if self.cfg["mode"] != "taskbar" or self._hidden:
            return
        if user32.GetAsyncKeyState(0x01) & 0x8000:
            return  # mouse button held: possibly mid-drag, leave it alone
        t = self.taskbar_target()
        if t and t != self._docked_at:
            height_changed = not self._docked_at or t[1] != self._docked_at[1]
            self.place_on_taskbar()
            if height_changed:
                self._layout = None  # rows are laid out for the bar's height: redraw them
                self.draw()

    # ----- moving -----
    def float_drag_start(self, e) -> None:
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def float_drag(self, e) -> None:
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def cell_press(self, e) -> None:
        self._press_x = e.x_root
        self._start_x = self.root.winfo_x()
        self._moved = False

    def cell_motion(self, e) -> None:
        if self.cfg["mode"] != "taskbar":
            return
        dx = e.x_root - self._press_x
        if abs(dx) > 4:
            self._moved = True
        if self._moved:
            self.root.geometry(f"+{self._start_x + dx}+{self.root.winfo_y()}")

    def cell_release(self, e, host: str, sid: str = "") -> None:
        if getattr(self, "_moved", False):
            self.save_cfg()
            self.place_on_taskbar()
        elif host:
            open_session(host)
            if sid:
                self.clicked[sid] = time.time()
                self.root.after(1, self.after_click)

    def after_click(self) -> None:
        self.pick_current()
        self.draw()

    # ----- menu actions -----
    def on_toggle_mode(self) -> None:
        self.save_cfg()
        self.cfg["mode"] = "taskbar" if self.docked.get() else "float"
        self.save_cfg_quiet()
        self.build()

    def save_cfg_quiet(self) -> None:
        try:
            CONFIG.write_text(json.dumps(self.cfg, indent=2), encoding="utf-8")
        except Exception:
            pass

    def toggle_collapse(self) -> None:
        self.cfg["collapsed"] = not self.cfg["collapsed"]
        if self.cfg["collapsed"]:
            self.body.pack_forget()
        else:
            self.body.pack(fill="both")
        self.save_cfg()

    def on_toggle_idle(self) -> None:
        self.cfg["show_idle"] = self.show_idle.get()
        self.save_cfg()
        self.draw()

    def on_toggle_transparent(self) -> None:
        self.cfg["transparent"] = self.transparent.get()
        self.save_cfg()
        if self.cfg["mode"] == "float":
            self.build()

    def set_alpha(self, a: float) -> None:
        self.cfg["alpha"] = a
        if self.cfg["mode"] == "float":
            self.root.attributes("-alpha", a)
        self.save_cfg()

    def reset_position(self) -> None:
        if self.cfg["mode"] == "taskbar":
            self.cfg["tb_right"] = None
            self.save_cfg_quiet()
            self.place_on_taskbar()
        else:
            self.cfg["x"] = self.cfg["y"] = None
            self.save_cfg_quiet()
            self.build()

    def quit(self) -> None:
        self.save_cfg()
        self.root.destroy()

    # ----- staying visible above the taskbar -----
    def raise_now(self) -> None:
        """Put the docked widget back above the taskbar (or hide it behind a full-screen app)."""
        try:
            if self.cfg["mode"] == "taskbar":
                # Hide only once full screen has held for ~0.6s, so a passing flicker of focus never hides it.
                self._fullscreen_hits = self._fullscreen_hits + 1 if foreground_is_fullscreen() else 0
                if self._fullscreen_hits >= 2:
                    if not self._hidden:
                        self.root.withdraw()
                        self._hidden = True
                    return
                if self._hidden:
                    self.root.deiconify()
                    self._hidden = False
                hwnd = user32.GetAncestor(self.root.winfo_id(), 2)  # GA_ROOT
                self._hwnd = hwnd
                if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
                    user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE: restore, leave focus where it is
                # HWND_TOPMOST, no move/size/activate: back above the taskbar without repainting.
                user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
            elif self._hidden:
                self.root.deiconify()
                self._hidden = False
        except tk.TclError:
            pass

    def keep_on_top(self) -> None:
        self.raise_now()
        self._redock_ticks += 1
        if self._redock_ticks % 7 == 0:  # about every 2 seconds
            try:
                self.redock()
            except tk.TclError:
                pass
        self.root.after(TOPMOST_MS, self.keep_on_top)

    def watch_window_switches(self) -> None:
        """Windows raises the taskbar over us whenever the active window changes. Hear about each
        switch the moment it happens and step back on top at once, instead of waiting for the poll."""
        proc_type = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                       wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)

        switched = threading.Event()

        def on_event(hook, event, hwnd, id_object, id_child, thread, ms):
            switched.set()  # nothing else here: no Tk calls from inside this callback

        def raiser() -> None:
            while True:
                switched.wait()
                switched.clear()
                last = 0.0
                for at in (0.0, 0.03, 0.12, 0.35):  # the taskbar can re-raise itself a moment later
                    time.sleep(at - last)
                    last = at
                    hwnd = self._hwnd
                    if hwnd and not self._hidden and self.cfg.get("mode") == "taskbar":
                        user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)

        threading.Thread(target=raiser, daemon=True).start()
        self._win_event_proc = proc_type(on_event)  # keep a reference so it is not garbage-collected
        user32.SetWinEventHook.restype = wintypes.HANDLE
        user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, proc_type,
                                           wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        # EVENT_SYSTEM_FOREGROUND (3) .. EVENT_SYSTEM_FOREGROUND, out of context, all processes
        self._win_event_hook = user32.SetWinEventHook(3, 3, None, self._win_event_proc, 0, 0, 0)

    # ----- data loop -----
    def refresh(self) -> None:
        now = time.time()
        rows = load_sessions()
        for r in rows:
            before = self.prev_status.get(r["id"])
            if before == "busy" and r["status"] != "busy":
                self.done_at[r["id"]] = now
            if r["status"] == "busy":
                self.done_at.pop(r["id"], None)
            self.prev_status[r["id"]] = r["status"]
            r["done"] = now - self.done_at.get(r["id"], 0) < DONE_SECONDS

        def rank(r):
            return (0 if r["status"] == "busy" else 1 if r["done"] else 2, -r["since"])

        self.rows = sorted(rows, key=rank)
        self.prs.want([r["cwd"] for r in self.rows[:MAX_ROWS]])
        self.prompts.want([r["id"] for r in self.rows])
        self.pick_current()
        self.draw()
        self.ticks += 1
        if self.cfg["mode"] == "float" and self.ticks % 10 == 0:
            self.root.attributes("-topmost", True)
        self.root.after(POLL_MS, self.refresh)

    def pick_current(self) -> None:
        """The session you most likely have open: the one you last typed in or last clicked here."""
        best, best_t = "", 0.0
        for r in self.rows:
            t = max(self.prompts.get(r["id"]), self.clicked.get(r["id"], 0.0))
            if t > best_t:
                best, best_t = r["id"], t
        self.current_id = best

    def visible_specs(self) -> list[dict]:
        now = time.time()
        out = []
        for r in self.rows:
            busy = r["status"] == "busy"
            if not (self.cfg["show_idle"] or busy or r["done"]):
                continue
            if busy:
                color, label = ORANGE, ago(now - r["since"])
            elif r["done"]:
                color, label = GREEN, "done"
            elif r["status"] == "idle":
                color, label = GREY, ago(now - r["since"])
            else:
                color, label = AMBER, r["status"].replace("_", " ")
            pr = self.prs.get(r["cwd"])
            current = r["id"] == self.current_id
            out.append({
                "key": (r["id"], color, r["name"], busy, r["done"], pr and pr["kind"], current),
                "id": r["id"],
                "current": current,
                "pr": pr,
                "color": color,
                "name": r["name"],
                "name_fg": FG if (busy or r["done"] or current) else DIM,
                "name_font": FONT_BOLD if busy else FONT,
                "sub": label,
                "sub_fg": color if (busy or r["done"]) else DIM,
                "host": r["host"],
            })
        return out

    def draw(self) -> None:
        all_specs = self.visible_specs()
        specs = all_specs[:MAX_ROWS]
        cur = next((x for x in all_specs if x["current"]), None)
        if cur is not None and cur not in specs:
            specs = specs[:-1] + [cur]
        hidden = len(all_specs) - len(specs)

        if self.cfg["mode"] == "float" and self.header is not None:
            working = sum(r["status"] == "busy" for r in self.rows)
            done = sum(r["done"] and r["status"] != "busy" for r in self.rows)
            idle = len(self.rows) - working - done
            parts = [f"{working} working"] + ([f"{done} done"] if done else []) + [f"{idle} idle"]
            summary = "  " + " · ".join(parts)
            if self.summary.cget("text") != summary:
                self.summary.configure(text=summary, fg=ORANGE if working else DIM)

        layout = (self.cfg["mode"], bool(self.rows), hidden, tuple(s["key"] for s in specs))
        if layout == self._layout:
            # Same rows in the same order: only update text that changed, no rebuild (no flicker).
            for s, sub in zip(specs, self._subs):
                if sub.cget("text") != s["sub"]:
                    sub.configure(text=s["sub"])
            return
        self._layout = layout
        for child in self.body.winfo_children():
            child.destroy()
        self._subs = []
        if self.cfg["mode"] == "taskbar":
            self.rebuild_taskbar(specs, hidden)
        else:
            self.rebuild_float(specs)

    def rebuild_taskbar(self, specs: list[dict], hidden: int) -> None:
        bar_h = self.place_on_taskbar()
        cell_h = bar_h // TB_ROWS
        pad_top = (bar_h - cell_h * TB_ROWS) // 2
        cells = list(specs)
        if not cells:
            cells = [{"empty": True}]
        for i, s in enumerate(cells):
            col, row = divmod(i, TB_ROWS)
            base = CURRENT_BG if s.get("current") else KEY
            cell = tk.Frame(self.body, bg=base, width=CELL_W, height=cell_h)
            cell.place(x=col * CELL_W, y=pad_top + row * cell_h)
            cell.pack_propagate(False)
            if "more" in s:
                tk.Label(cell, text=f"   +{s['more']} more", bg=base, fg=DIM, font=FONT_SMALL, anchor="w").pack(
                    side="left")
                continue
            if "empty" in s:
                tk.Label(cell, text="No Claude sessions", bg=base, fg=DIM, font=FONT_SMALL, anchor="w").pack(
                    side="left")
                continue
            dot = tk.Label(cell, text="●", bg=base, fg=s["color"], font=("Segoe UI", 6), padx=0, bd=0)
            dot.pack(side="left", padx=(4, 2))
            sub = tk.Label(cell, text=s["sub"], bg=base, fg=s["sub_fg"], font=FONT_SMALL, anchor="e", padx=0)
            sub.pack(side="right", padx=(4, 6))
            room = CELL_W - 4 - 10 - 6 - self.small_font.measure("10h 59m") - 6
            icon = s["pr"] and self.icons.get(s["pr"]["kind"])
            if icon:
                tk.Label(cell, image=icon, bg=base, bd=0, padx=0, pady=0).pack(side="right", padx=(4, 0))
                room -= self.icon_size + 4
            font = self.bold_font if s["name_font"] == FONT_BOLD else self.name_font
            name = tk.Label(cell, text=fit(s["name"], font, room), bg=base, fg=s["name_fg"],
                            font=s["name_font"], anchor="w", padx=0)
            name.pack(side="left", fill="x", expand=True)
            self._subs.append(sub)
            self.make_interactive(cell, s["host"], base, s["id"])

    def rebuild_float(self, specs: list[dict]) -> None:
        bg = self.bg
        if not specs:
            tk.Label(self.body, text="No sessions running" if not self.rows else "Nothing working right now",
                     bg=bg, fg=DIM, font=FONT, padx=6, pady=2, anchor="w").pack(fill="x")
            return
        for s in specs:
            rbg = CURRENT_BG if s["current"] else bg
            row = tk.Frame(self.body, bg=rbg, padx=6, pady=0)
            row.pack(fill="x")
            tk.Label(row, text="●", bg=rbg, fg=s["color"], font=("Segoe UI", 7), padx=0).grid(row=0, column=0, padx=(0, 2))
            tk.Label(row, text=shorten(s["name"], 30), bg=rbg, fg=s["name_fg"], font=s["name_font"],
                     anchor="w").grid(row=0, column=1, sticky="w")
            icon = s["pr"] and self.icons.get(s["pr"]["kind"])
            if icon:
                tk.Label(row, image=icon, bg=rbg, bd=0, padx=0, pady=0).grid(
                    row=0, column=2, sticky="e", padx=(6, 0))
            sub = tk.Label(row, text=s["sub"], bg=rbg, fg=s["sub_fg"], font=FONT_SMALL, anchor="e")
            sub.grid(row=0, column=3, sticky="e", padx=(6, 0))
            row.columnconfigure(1, weight=1)
            self._subs.append(sub)
            self.make_interactive(row, s["host"], rbg, s["id"])
        tk.Frame(self.body, bg=bg, height=2).pack()

    def make_interactive(self, frame: tk.Frame, host: str, bg: str, sid: str = "") -> None:
        widgets = [frame, *frame.winfo_children()]

        def paint(color: str) -> None:
            for w in widgets:
                try:
                    w.configure(bg=color)
                except tk.TclError:
                    pass

        for w in widgets:
            w.configure(cursor="hand2" if host else "")
            w.bind("<Enter>", lambda e: paint(CURRENT_HOVER if bg == CURRENT_BG else ROW_HOVER))
            w.bind("<Leave>", lambda e: paint(bg))
            w.bind("<ButtonPress-1>", self.cell_press)
            w.bind("<B1-Motion>", self.cell_motion)
            w.bind("<ButtonRelease-1>", lambda e, h=host, i=sid: self.cell_release(e, h, i))

    def run(self) -> None:
        self.root.mainloop()


def open_log():
    """pythonw has no console: send errors (and a native crash's Python stack) to widget.log."""
    import faulthandler
    import traceback

    log_path = HERE / "widget.log"
    try:
        if log_path.exists() and log_path.stat().st_size > 512_000:
            log_path.unlink()
        log = open(log_path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return None
    log.write(f"\n--- started {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    sys.stderr = log
    faulthandler.enable(log)

    def report(exc, val, tb):
        log.write(f"{time.strftime('%H:%M:%S')} callback error:\n")
        traceback.print_exception(exc, val, tb, file=log)

    return report


if __name__ == "__main__":
    if not single_instance():
        sys.exit(0)
    dpi_aware()
    report = open_log()
    widget = Widget()
    if report:
        widget.root.report_callback_exception = report
    widget.run()
