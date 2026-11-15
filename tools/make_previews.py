"""Regenerate the README previews with made-up sessions (no real session data is ever shown).

    python tools/make_previews.py

Close the real widget first: the demo docks in the same spot on the taskbar.
Writes docs/taskbar.gif, docs/taskbar.png, docs/floating.png and the legend icons in docs/icons/.
"""

import ctypes
import json
import sys
import tempfile
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

from PIL import Image, ImageDraw, ImageGrab

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SCALE = 2           # previews are saved at 2x so they stay readable on GitHub
FRAME_MS = 100

ctypes.windll.shcore.SetProcessDpiAwareness(1)
m = SourceFileLoader("widget", str(ROOT / "claude_sessions_widget.pyw")).load_module()

# ---------- made-up sessions ----------
NOW = time.time()
MIN = 60
SESSIONS = [
    {"id": "s1", "name": "Fix login redirect loop", "status": "busy", "since": NOW - 2 * MIN},
    {"id": "s2", "name": "Add CSV export to reports", "status": "busy", "since": NOW - 8 * MIN},
    {"id": "s3", "name": "Investigate flaky checkout tests", "status": "busy", "since": NOW - 14 * MIN},
    {"id": "s4", "name": "Update onboarding copy", "status": "idle", "since": NOW - 65 * MIN},
    {"id": "s5", "name": "Refactor payment service", "status": "idle", "since": NOW - 3 * 60 * MIN},
    {"id": "s6", "name": "Write release notes", "status": "idle", "since": NOW - 6 * 60 * MIN},
]
PRS = {"s1": "pending", "s3": "fail", "s4": "merged", "s5": "pass", "s6": "draft"}
TYPED = {"s1": NOW - 30}  # "you last typed in s1", so s1 starts highlighted


def demo_sessions() -> list[dict]:
    return [dict(s, host="", folder="demo", cwd=s["id"]) for s in SESSIONS]


class DemoPrs:
    def want(self, cwds):
        pass

    def get(self, cwd):
        kind = PRS.get(cwd)
        return {"kind": kind} if kind else None


class DemoPrompts:
    def want(self, ids):
        pass

    def get(self, sid):
        return TYPED.get(sid, 0.0)


demo_config = Path(tempfile.gettempdir()) / "claude-taskbar-demo.json"
demo_config.write_text(json.dumps({"mode": "taskbar", "show_idle": True}), encoding="utf-8")
m.CONFIG = demo_config
m.POLL_MS = 250
m.load_sessions = demo_sessions
m.PrWatcher = DemoPrs
m.PromptWatcher = DemoPrompts

w = m.Widget()
frames: list[tuple[Image.Image, int]] = []


def grab_widget(margin: int) -> Image.Image:
    w.root.update_idletasks()
    x, y = w.root.winfo_rootx(), w.root.winfo_rooty()
    width, height = w.root.winfo_width(), w.root.winfo_height()
    img = ImageGrab.grab(bbox=(x - margin, y, x + width + margin, y + height))
    return img.resize((img.width * SCALE, img.height * SCALE), Image.LANCZOS)


def capture() -> None:
    img = grab_widget(10)
    if frames and img.tobytes() == frames[-1][0].tobytes():
        frames[-1] = (frames[-1][0], frames[-1][1] + FRAME_MS)
    else:
        frames.append((img, FRAME_MS))


def finish_one() -> None:
    SESSIONS[1]["status"] = "idle"  # "Add CSV export" finishes: it turns green and says done
    SESSIONS[1]["since"] = time.time()


def click_other() -> None:
    w.clicked["s4"] = time.time()  # as if "Update onboarding copy" was clicked in the widget
    w.pick_current()
    w.draw()


def checks_pass() -> None:
    PRS["s3"] = "pass"  # the flaky-test PR goes green


def run_timeline() -> None:
    events = {20: finish_one, 40: click_other, 58: checks_pass}
    for i in range(80):
        if i in events:
            w.root.after(i * FRAME_MS, events[i])
        w.root.after(i * FRAME_MS + 60, capture)
    w.root.after(80 * FRAME_MS + 200, save_taskbar)


def save_taskbar() -> None:
    DOCS.mkdir(exist_ok=True)
    first = frames[0][0]
    # One palette built from every frame, so colours (the purple "merged" icon) survive and stay stable.
    rgb = [f.convert("RGB") for f, _ in frames]
    sheet = Image.new("RGB", (first.width, first.height * len(rgb)))
    for i, f in enumerate(rgb):
        sheet.paste(f, (0, i * first.height))
    palette = sheet.quantize(colors=255, method=Image.Quantize.MAXCOVERAGE)
    pal = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in rgb]
    pal[0].save(DOCS / "taskbar.gif", save_all=True, append_images=pal[1:],
                duration=[d for _, d in frames], loop=0, optimize=True, disposal=1)
    frames[-1][0].save(DOCS / "taskbar.png")
    print("taskbar.gif:", len(frames), "frames,", first.size)
    # floating mode, solid background, somewhere on screen
    w.cfg.update({"mode": "float", "transparent": False, "alpha": 1.0, "collapsed": False, "x": 240, "y": 240})
    w.build()
    w.root.after(900, save_floating)


def save_floating() -> None:
    grab_widget(0).save(DOCS / "floating.png")
    print("floating.png saved")
    save_icons()
    w.root.destroy()


def save_icons() -> None:
    out = DOCS / "icons"
    out.mkdir(parents=True, exist_ok=True)
    for kind in ("pass", "fail", "pending", "merged", "closed", "draft"):
        m.draw_pr_icon(kind, 32).save(out / f"pr-{kind}.png")
    for name, color in (("working", m.ORANGE), ("done", m.GREEN), ("idle", m.GREY)):
        k = 6
        img = Image.new("RGBA", (32 * k, 32 * k), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((8 * k, 8 * k, 24 * k, 24 * k), fill=color)
        img.resize((32, 32), Image.LANCZOS).save(out / f"dot-{name}.png")
    print("icons saved")


w.root.after(1200, run_timeline)
w.root.mainloop()
demo_config.unlink(missing_ok=True)
