# Claude Usage Widget

A tiny, always-visible **macOS desktop widget** that shows your Claude usage as
coral progress bars:

- **Session (5h)** — your rolling 5-hour window
- **Weekly (7d)** — your rolling 7-day window
- **Model weekly (7d)** — the model-specific weekly limit (e.g. **Fable**), shown
  automatically when your plan reports one; the label follows the model name the
  API returns

It's a single Python file. **PyQt6 is the only dependency** (everything else is
the Python standard library). It runs out of the box with convincing **demo
data**, so it looks right before you wire up real numbers.

> ⚠️ There is **no official/public API** for consumer Claude usage. The numbers
> on `claude.ai/settings/usage` come from an internal endpoint the site calls
> with your logged-in session. This widget lets you point it at that endpoint
> yourself (see [Showing real data](#showing-real-data)). The endpoint is
> undocumented and may change at any time.

---

## What it looks like

A dark, frosted, rounded card that floats on your wallpaper:

- Coral dot + **"Claude usage"** header, with a small **`demo`** badge while it's
  showing demo data.
- Two or three sections, each with a label, an integer percentage, a thin coral
  progress bar, and a muted "resets in …" line. The card grows to fit when the
  model-specific meter (e.g. Fable) is present.
- A muted `updated HH:MM` footer that refreshes on each fetch.

### Desktop-widget behavior

- **Frameless & translucent** — just the rounded card, no title bar.
- **Sits on the desktop, behind your windows** — other app windows stack on top
  of it; it never covers the app you're using.
- **Always visible** — it does *not* hide when you switch to another app, and it
  appears on **every Space/Desktop**.
- **No Dock icon and no ⌘-Tab entry** — it stays out of your way.
- **Draggable** — click and drag it anywhere; its position is remembered across
  launches.

> The desktop-layer / always-visible / no-Dock-icon behaviors are macOS-specific
> (implemented via the Cocoa window APIs through Python's stdlib `ctypes`). On
> other platforms it still runs as a normal frameless window.

---

## Requirements

- **macOS**
- **Python 3.9+** (developed on 3.14)
- **PyQt6**

---

## Install

PyQt6 is the only thing you need to install. Pick one of the following.

### Option A — quick (user install)

```bash
pip3 install --user PyQt6
```

If your Python is "externally managed" (Homebrew/system Python) and the command
above is blocked, add the override flag:

```bash
pip3 install --user --break-system-packages PyQt6
```

### Option B — clean (virtual environment, recommended)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install PyQt6
```

---

## Run

```bash
python3 claude_usage_widget.py
```

(If you used the venv in Option B, run it with that venv's Python:
`.venv/bin/python claude_usage_widget.py`.)

The card appears in the **top-right of your screen** on first launch. Drag it
wherever you like — it remembers the spot next time.

### Quit

**Right-click the card → Quit.** (There's no Dock icon, so the right-click menu
is how you control it.)

### Right-click menu

- **Refresh now** — fetch immediately
- **Settings…** — set the usage URL, cookie, and refresh interval
- **Quit**

---

## Showing real data

Demo data is shown until you give the widget a usage endpoint + your session
cookie. Because the endpoint is internal/undocumented, you copy it out of your
browser's DevTools:

1. Log in at **https://claude.ai** and open **https://claude.ai/settings/usage**.
2. Open **DevTools**: `Cmd-Option-I` (or right-click → Inspect).
3. Go to the **Network** tab.
4. Filter to **Fetch/XHR**.
5. **Reload** the page (`Cmd-R`) so the requests are captured.
6. Click through the requests and look at each **Response / Preview**. Find the
   one whose JSON contains your usage numbers (look for keys like `five_hour`,
   `seven_day`, `limits`, `utilization`, `percent_used`, `resets_at`).
7. From that request's **Headers** tab:
   - Copy the full **Request URL** → paste into Settings → **Usage URL**.
   - Under **Request Headers**, copy the entire **`Cookie:`** value (one long
     line) → paste into Settings → **Cookie**.
8. Set a **refresh interval** and click **OK**.

The widget fetches on a background thread and replaces the demo data with your
real numbers (the `demo` badge disappears).

### If it stays on "demo" after configuring

The endpoint's JSON shape probably differs from what the parser expects. The
parser first looks for the endpoint's `limits` array (whose `percent` values are
unambiguously 0–100, and which carries the model-specific meter); if that's
absent it falls back to a generic key search. Open `claude_usage_widget.py` and
tweak the key lists near `parse_usage()`:

- `SESSION_KEYS`, `WEEKLY_KEYS` — names that identify each bucket
- `PERCENT_KEYS` — keys holding the percentage/utilization
- `RESET_KEYS` — keys holding the ISO reset timestamp

The model-specific meter is optional: if the response has no model-scoped entry
in `limits`, the widget simply shows the two standard meters.

The widget **never blanks or crashes**: if the URL/cookie are empty, the fetch
fails, or the JSON can't be parsed, it silently falls back to demo data.

---

## Configuration & privacy

Settings are stored locally at:

```
~/.claude_usage_widget/config.json
```

It holds: `usage_url`, `cookie`, `refresh_seconds`, and the saved window
position.

> 🔒 **Your cookie is your session — treat it like a password.** It's stored
> locally and is only ever sent to the URL you configure. The included
> `.gitignore` excludes `config.json` and the `.claude_usage_widget/` directory
> so you never accidentally commit it.

---

## Keep it running / launch at login (optional)

The simplest approach: drag `claude_usage_widget.py` (or a small launcher) into
**System Settings → General → Login Items → Open at Login**. Or run it in the
background from a terminal:

```bash
nohup python3 claude_usage_widget.py >/dev/null 2>&1 &
```

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ModuleNotFoundError: No module named 'PyQt6'` | Install PyQt6 (see [Install](#install)); make sure you run it with the **same** Python you installed into. |
| `pip` says the environment is "externally managed" | Use `--break-system-packages` (Option A) or a venv (Option B). |
| Widget isn't visible | It sits **behind** windows — minimize/move them, or check another Space. It launches top-right; if it's off-screen, delete `~/.claude_usage_widget/config.json` to reset its position. |
| Always shows the `demo` badge | URL/cookie missing or wrong, or the JSON shape differs — see [If it stays on "demo"](#if-it-stays-on-demo-after-configuring). |

---

## Files

- `claude_usage_widget.py` — the entire widget (single file, well-commented).
- `.gitignore` — excludes macOS/Python junk and your private config.
- `README.md` — this file.
