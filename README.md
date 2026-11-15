# claude-taskbar

A small Windows 11 widget that sits on the taskbar and shows your running Claude Code sessions at a glance.

```
● Fix login redirect loop             ✓  2m   ● Investigate flaky tests          •  3m
● Add CSV export to reports…              8m   ● Update onboarding copy           ✓  1h
● Refactor payment service                1m   ● Write release notes                 6h
```

## What it shows

- **Up to 6 sessions**, two columns of three, docked in the empty space left of the tray icons.
- **Status dot:** orange = working, green = just finished (2 minutes), grey = idle.
- **Time:** how long a session has been working or idle.
- **PR icon** for the branch the session is on (via `gh`): green ✓ checks passed, red ✕ failing,
  amber ring running, purple ✓ merged, grey closed / draft.
- **Highlight** on the session you're most likely looking at: the one you last typed in, or last
  clicked in the widget.

## Using it

- **Click** a session to open it in the Claude desktop app.
- **Drag sideways** to move it along the taskbar.
- **Right-click** for: dock on taskbar / floating window, show idle sessions, opacity, reset position, exit.
- It hides itself while a full-screen video or game is in front.

## Requirements

- Windows 11, Python 3.12+ with Tkinter (the python.org installer includes it).
- [Pillow](https://pypi.org/project/pillow/) for the PR icons (optional; without it the icons are left out).
- [GitHub CLI](https://cli.github.com/) signed in, for PR status (optional).
- The Claude desktop app, for click-to-open.

## Run

```
pythonw claude_sessions_widget.pyw
```

To start it with Windows, put a shortcut to that command in your Startup folder
(`Win+R`, `shell:startup`).

## How it works

- Reads the status files Claude Code writes to `%USERPROFILE%\.claude\sessions\*.json`
  (only the `.json` files; the `.key` files beside them are never opened).
- "Last typed in" comes from the end of each session's transcript in `%USERPROFILE%\.claude\projects\`.
- Opens sessions with the desktop app's own link, `claude://code/continue?session=<id>`.
- Settings (position, mode, opacity) are saved to `config.json`, and errors to `widget.log`,
  both next to the script.

## Known limits

- While the Start menu or search is open, Windows draws the taskbar above every other window, so
  the widget is covered until it closes. Windows 11 doesn't let apps place windows inside the taskbar.
- The Claude app doesn't expose which session is on screen, so the highlight can't follow you if you
  switch sessions in the app's sidebar without typing.
