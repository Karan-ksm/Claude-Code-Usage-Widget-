#!/usr/bin/env python3
"""
Claude Usage Widget — a tiny always-on-top macOS desktop widget.

Shows your Claude usage as two coral progress bars:
  • Session (5h)  — your rolling 5-hour window
  • Weekly  (7d)  — your rolling 7-day window

It defaults to DEMO data so it looks correct on first launch. There is no
official/public API for consumer Claude usage, so to show *real* numbers you
paste the internal endpoint URL + your logged-in cookie into Settings (see the
DevTools instructions below).

------------------------------------------------------------------------------
INSTALL / RUN / QUIT
------------------------------------------------------------------------------
  pip install PyQt6            # PyQt6 is the only dependency (rest is stdlib)
  python3 claude_usage_widget.py

  • Drag the card anywhere; its position is remembered across launches.
  • Right-click the card -> Refresh now / Settings… / Quit.
  • Pin it on your desktop: it sits on the desktop (behind other windows) and
    shows no Dock / cmd-tab icon — it never covers the app you're using.
  • It stays visible when you switch to other apps, and shows on every Space.

------------------------------------------------------------------------------
HOW TO FIND THE REAL ENDPOINT URL + COOKIE (undocumented — may change!)
------------------------------------------------------------------------------
The numbers on https://claude.ai/settings/usage are loaded by the page from an
*internal* endpoint using your logged-in session. There is no public API, so we
read that same request. Steps (Chrome/Edge/Brave/Firefox are all similar):

  1. Log in at https://claude.ai and open  https://claude.ai/settings/usage
  2. Open DevTools:  Cmd-Option-I  (or right-click -> Inspect).
  3. Click the "Network" tab.
  4. Filter to "Fetch/XHR" (button row near the top of the Network panel).
  5. Reload the page (Cmd-R) so the requests are captured.
  6. Click through the requests and watch the "Response"/"Preview" tab. Find
     the one whose JSON contains your usage numbers (look for things like
     "five_hour", "seven_day", "utilization", "percent_used", "resets_at").
  7. Select that request:
       - "Headers" tab -> copy the full "Request URL"   -> Settings: Usage URL
       - "Headers" tab -> under "Request Headers" find  "Cookie:" and copy its
         ENTIRE value (one long line)                   -> Settings: Cookie
  8. Set a refresh interval and click OK. The widget will fetch on a background
     thread and replace the demo data with your real numbers.

NOTES
  • This endpoint is UNDOCUMENTED and may change name/shape at any time. If the
    widget keeps showing the "demo" badge after you paste a working URL+cookie,
    the JSON shape probably differs from what parse_usage() expects — tweak the
    KEY lists in parse_usage() to match the keys you saw in step 6.
  • Your cookie is a secret (it's your session). It is stored locally in
    ~/.claude_usage_widget/config.json and never sent anywhere except to the
    URL you configure.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QPoint, QRect, QRectF
from PyQt6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QBrush,
)
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QMenu,
    QDialog,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
)

# ---------------------------------------------------------------------------
# Constants — colors, sizes, paths
# ---------------------------------------------------------------------------

CORAL = "#D85A30"           # Claude coral: header dot, bar fill, percent text
TEXT_HEADER = "#ECECEC"     # header title
TEXT_MUTED = "#9A9A9A"      # labels, reset lines, footer

CARD_BG = QColor(21, 23, 27, 235)            # dark frosted card
CARD_BORDER = QColor(255, 255, 255, 26)      # thin white border
TRACK_COLOR = QColor(255, 255, 255, 20)      # faint progress track

WIN_W, WIN_H = 248, 196
CARD_RADIUS = 16
EDGE_MARGIN = 12            # margin from screen edges on first launch
PADDING = 16               # inner padding of the card

CONFIG_DIR = os.path.expanduser("~/.claude_usage_widget")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_REFRESH = 60       # seconds

# Demo data shown on first run / whenever real data is unavailable.
DEMO_DATA = {
    "session": {"pct": 63, "reset_text": "resets in 3h 12m"},
    "weekly": {"pct": 41, "reset_text": "resets Mon 9:00 AM"},
}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def default_config() -> dict:
    return {
        "usage_url": "",
        "cookie": "",
        "refresh_seconds": DEFAULT_REFRESH,
        "pos_x": None,
        "pos_y": None,
    }


def load_config() -> dict:
    """Load config, merging onto defaults so missing keys are always safe."""
    cfg = default_config()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            cfg.update({k: saved[k] for k in cfg if k in saved})
    except (OSError, ValueError):
        pass  # missing/corrupt config -> just use defaults
    return cfg


def save_config(cfg: dict) -> None:
    """Persist config to disk; failures are non-fatal."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Usage parsing — defensive, because the endpoint is undocumented
# ---------------------------------------------------------------------------

# Keys that identify each bucket. Add to these if your JSON uses other names.
SESSION_KEYS = ("five_hour", "fivehour", "fiveHour", "session", "5h", "five_hour_limit")
WEEKLY_KEYS = ("seven_day", "sevenday", "sevenDay", "weekly", "7d", "seven_day_limit")

# Keys that hold a percentage / utilization within a bucket. Note: a bare
# "used" is NOT here — that's a raw count handled by the used/limit ratio below.
PERCENT_KEYS = ("utilization", "percent_used", "percentUsed", "percent",
                "usage_percent", "used_percent", "utilization_percent")

# Keys that hold an ISO reset timestamp within a bucket.
RESET_KEYS = ("resets_at", "resetsAt", "reset_at", "resetAt", "reset",
              "resets", "reset_time", "next_reset")


def _find_key(obj, keys):
    """Recursively search a nested dict/list for the first value whose key
    (case-insensitively) matches any name in `keys`. Returns the value or None."""
    lowered = tuple(k.lower() for k in keys)
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in lowered:
                return v
        for v in obj.values():
            found = _find_key(v, keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_key(item, keys)
            if found is not None:
                return found
    return None


def _normalize_percent(value):
    """Turn a raw value into an integer 0–100, or None if not numeric.
    Fractions (0–1) are scaled to percentages."""
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
    if 0.0 <= value <= 1.0:
        value *= 100.0
    value = max(0.0, min(100.0, value))
    return round(value)


def _percent_from_bucket(bucket):
    """Extract a percent from a bucket dict. Tries direct percent keys first,
    then a used/limit (or used/total) ratio."""
    if bucket is None:
        return None
    # 1) direct percent-ish key anywhere inside the bucket
    raw = _find_key(bucket, PERCENT_KEYS)
    pct = _normalize_percent(raw)
    if pct is not None:
        return pct
    # 2) used / limit ratio, if both are present
    if isinstance(bucket, dict):
        used = _find_key(bucket, ("used", "used_tokens", "consumed", "current"))
        limit = _find_key(bucket, ("limit", "total", "max", "cap", "quota"))
        try:
            if used is not None and limit:
                return _normalize_percent(float(used) / float(limit))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return None


def _reset_text_from_bucket(bucket):
    """Build a 'resets in Xh Ym' string from an ISO timestamp in the bucket."""
    raw = _find_key(bucket, RESET_KEYS)
    if not isinstance(raw, str):
        return ""
    ts = raw.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt - datetime.now(timezone.utc)
    secs = int(delta.total_seconds())
    if secs <= 0:
        return "resets soon"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        return f"resets in {days}d {hours}h"
    return f"resets in {hours}h {minutes}m"


def parse_usage(data) -> dict | None:
    """Parse an arbitrary usage JSON blob into:
        {"session": {"pct": int, "reset_text": str},
         "weekly":  {"pct": int, "reset_text": str}}
    Returns None if either bucket's percent can't be resolved (caller then
    falls back to demo data)."""
    if not isinstance(data, (dict, list)):
        return None

    session_bucket = _find_key(data, SESSION_KEYS)
    weekly_bucket = _find_key(data, WEEKLY_KEYS)

    session_pct = _percent_from_bucket(session_bucket)
    weekly_pct = _percent_from_bucket(weekly_bucket)

    if session_pct is None or weekly_pct is None:
        return None

    return {
        "session": {
            "pct": session_pct,
            "reset_text": _reset_text_from_bucket(session_bucket),
        },
        "weekly": {
            "pct": weekly_pct,
            "reset_text": _reset_text_from_bucket(weekly_bucket),
        },
    }


# ---------------------------------------------------------------------------
# Network fetch (stdlib only)
# ---------------------------------------------------------------------------

def fetch_usage(url: str, cookie: str) -> dict | None:
    """Fetch + parse usage JSON. Returns parsed dict or None. Raises on
    network/HTTP errors (the worker catches everything)."""
    req = urllib.request.Request(url)
    req.add_header("Cookie", cookie)
    req.add_header("Accept", "application/json")
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ClaudeUsageWidget/1.0",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = resp.read().decode("utf-8", errors="replace")
    return parse_usage(json.loads(payload))


# ---------------------------------------------------------------------------
# Background worker thread
# ---------------------------------------------------------------------------

class FetchWorker(QThread):
    """Runs the fetch off the UI thread. Emits (data_or_None, is_demo)."""

    result = pyqtSignal(object, bool)

    def __init__(self, url: str, cookie: str, parent=None):
        super().__init__(parent)
        self._url = url
        self._cookie = cookie

    def run(self):
        # If not configured, go straight to demo (no network).
        if not self._url or not self._cookie:
            self.result.emit(DEMO_DATA, True)
            return
        try:
            data = fetch_usage(self._url, self._cookie)
        except Exception:
            data = None
        if data is None:
            self.result.emit(DEMO_DATA, True)   # fetch/parse failed -> demo
        else:
            self.result.emit(data, False)       # real data


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Claude Usage — Settings")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Usage URL (internal endpoint from DevTools):"))
        self.url_edit = QLineEdit(cfg.get("usage_url", ""))
        self.url_edit.setPlaceholderText("https://claude.ai/api/…/usage")
        layout.addWidget(self.url_edit)

        layout.addWidget(QLabel("Cookie (full Cookie request-header value):"))
        self.cookie_edit = QPlainTextEdit(cfg.get("cookie", ""))
        self.cookie_edit.setPlaceholderText("sessionKey=…; other=…")
        self.cookie_edit.setFixedHeight(110)
        layout.addWidget(self.cookie_edit)

        row = QHBoxLayout()
        row.addWidget(QLabel("Refresh interval (seconds):"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(10, 3600)
        self.interval_spin.setValue(int(cfg.get("refresh_seconds", DEFAULT_REFRESH)))
        row.addWidget(self.interval_spin)
        row.addStretch(1)
        layout.addLayout(row)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("OK")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        layout.addLayout(btns)

    def values(self) -> dict:
        return {
            "usage_url": self.url_edit.text().strip(),
            "cookie": self.cookie_edit.toPlainText().strip(),
            "refresh_seconds": int(self.interval_spin.value()),
        }


# ---------------------------------------------------------------------------
# The widget
# ---------------------------------------------------------------------------

class UsageWidget(QWidget):
    def __init__(self):
        super().__init__()

        self.cfg = load_config()
        self.data = DEMO_DATA
        self.is_demo = True
        self.updated_at = ""
        self._worker = None  # keep a reference so the thread isn't GC'd
        self._pinned = False  # guards the one-time macOS keep-visible patch

        # Frameless desktop widget, no Dock/cmd-tab icon (Tool). NOT
        # always-on-top: the native window level is lowered in
        # _macos_pin_window() so app windows stack on top of it.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(WIN_W, WIN_H)

        # Right-click menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

        self._drag_offset = None

        self._position_window()

        # Auto-refresh timer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(int(self.cfg.get("refresh_seconds", DEFAULT_REFRESH)) * 1000)

        # First fetch right away
        self.refresh()

    # ----- positioning ------------------------------------------------------

    def _position_window(self):
        px, py = self.cfg.get("pos_x"), self.cfg.get("pos_y")
        if isinstance(px, int) and isinstance(py, int):
            self.move(px, py)
            return
        # First launch: top-right of the available screen area.
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1440, 900)
        x = geo.right() - WIN_W - EDGE_MARGIN
        y = geo.top() + EDGE_MARGIN
        self.move(x, y)

    # ----- macOS: stay visible when other apps are focused ------------------

    def showEvent(self, event):
        # Apply the keep-visible patch once, after the native window exists
        # (winId() is only valid once the window is realized).
        if not self._pinned:
            self._pinned = True
            self._macos_pin_window()
        super().showEvent(event)

    def _macos_pin_window(self):
        """Keep a Qt.Tool window from auto-hiding when the app deactivates.

        A Qt.Tool window is an NSPanel whose `hidesOnDeactivate` defaults to
        YES, so macOS hides it whenever another app is focused. We reach the
        underlying NSWindow via the Objective-C runtime (stdlib ctypes — no
        extra pip dependency) and turn that off, and also let it show on all
        Spaces like a proper pinned desktop widget. Any failure silently
        no-ops, leaving the default behavior intact."""
        if sys.platform != "darwin":
            return
        try:
            objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.objc_msgSend.restype = ctypes.c_void_p

            def sel(name):
                return ctypes.c_void_p(objc.sel_registerName(name))

            # On macOS, QWidget.winId() returns the NSView*.
            view = ctypes.c_void_p(int(self.winId()))
            objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            window = objc.objc_msgSend(view, sel(b"window"))
            if not window:
                return
            window = ctypes.c_void_p(window)

            # window.setHidesOnDeactivate_(NO)  -> the actual fix
            objc.objc_msgSend.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool
            ]
            objc.objc_msgSend(window, sel(b"setHidesOnDeactivate:"), False)

            # window.setCollectionBehavior_(CanJoinAllSpaces | Stationary)
            # so the widget appears on every Space and stays put.
            CAN_JOIN_ALL_SPACES = 1 << 0
            STATIONARY = 1 << 4
            objc.objc_msgSend.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong
            ]
            objc.objc_msgSend(
                window,
                sel(b"setCollectionBehavior:"),
                CAN_JOIN_ALL_SPACES | STATIONARY,
            )

            # window.setLevel_(NSNormalWindowLevel - 1) -> sit just below every
            # normal app window so they stack on top of the widget (it behaves
            # like a desktop widget), while staying above the wallpaper.
            NS_DESKTOP_LEVEL = -1  # NSNormalWindowLevel (0) minus 1
            objc.objc_msgSend.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long
            ]
            objc.objc_msgSend(window, sel(b"setLevel:"), NS_DESKTOP_LEVEL)
        except Exception:
            pass  # non-fatal: fall back to default Tool-window behavior

    # ----- refresh / worker -------------------------------------------------

    def refresh(self):
        # Don't pile up workers if one is already running.
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = FetchWorker(
            self.cfg.get("usage_url", ""), self.cfg.get("cookie", ""), self
        )
        self._worker.result.connect(self._on_result)
        self._worker.start()

    def _on_result(self, data, is_demo):
        self.data = data if data else DEMO_DATA
        self.is_demo = bool(is_demo)
        self.updated_at = datetime.now().strftime("%H:%M")
        self.update()  # repaint

    # ----- context menu -----------------------------------------------------

    def _show_menu(self, pos: QPoint):
        menu = QMenu(self)
        menu.addAction("Refresh now", self.refresh)
        menu.addAction("Settings…", self._open_settings)
        menu.addSeparator()
        menu.addAction("Quit", QApplication.quit)
        menu.exec(self.mapToGlobal(pos))

    def _open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.cfg.update(dlg.values())
            save_config(self.cfg)
            self.timer.start(int(self.cfg["refresh_seconds"]) * 1000)
            self.refresh()

    # ----- dragging ---------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        if self._drag_offset is not None:
            self._drag_offset = None
            self.cfg["pos_x"] = self.x()
            self.cfg["pos_y"] = self.y()
            save_config(self.cfg)

    # ----- painting ---------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        rect = QRectF(0.5, 0.5, WIN_W - 1, WIN_H - 1)

        # Rounded card (fill + thin border) — painted manually, no QSS radius.
        path = QPainterPath()
        path.addRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)
        p.fillPath(path, QBrush(CARD_BG))
        p.setPen(QPen(CARD_BORDER, 1))
        p.drawPath(path)

        left = PADDING
        right = WIN_W - PADDING
        width = right - left

        # --- Header row ---
        y = PADDING + 6
        # coral dot
        dot_r = 4
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(CORAL))
        p.drawEllipse(QPoint(left + dot_r, y), dot_r, dot_r)
        # title
        p.setPen(QColor(TEXT_HEADER))
        p.setFont(self._font(13, QFont.Weight.Medium))
        p.drawText(
            QRect(left + 2 * dot_r + 8, y - 10, width, 20),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            "Claude usage",
        )
        # demo badge (only when showing demo data)
        if self.is_demo:
            self._draw_badge(p, right, y, "demo")

        # --- Sections ---
        section_top = PADDING + 28
        section_gap = 64
        self._draw_section(
            p, left, section_top, width,
            "Session (5h)", self.data.get("session", {}),
        )
        self._draw_section(
            p, left, section_top + section_gap, width,
            "Weekly (7d)", self.data.get("weekly", {}),
        )

        # --- Footer ---
        p.setPen(QColor(TEXT_MUTED))
        p.setFont(self._font(10))
        footer = f"updated {self.updated_at}" if self.updated_at else "updating…"
        p.drawText(
            QRect(left, WIN_H - PADDING - 6, width, 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            footer,
        )

        p.end()

    # ----- paint helpers ----------------------------------------------------

    def _font(self, px: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        f = QFont()
        f.setPixelSize(px)
        f.setWeight(weight)
        return f

    def _draw_badge(self, p: QPainter, right: int, center_y: int, text: str):
        p.setFont(self._font(10, QFont.Weight.Medium))
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(text)
        bw, bh = tw + 14, 16
        bx = right - bw
        by = center_y - bh // 2
        badge_rect = QRectF(bx, by, bw, bh)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawRoundedRect(badge_rect, bh / 2, bh / 2)
        p.setPen(QColor(TEXT_MUTED))
        p.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_section(self, p, x, y, width, label, section):
        pct = int(section.get("pct", 0))
        reset_text = section.get("reset_text", "")

        # label (left) + percent (right, coral)
        p.setFont(self._font(11))
        p.setPen(QColor(TEXT_MUTED))
        p.drawText(
            QRect(x, y, width, 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            label,
        )
        p.setFont(self._font(11, QFont.Weight.Medium))
        p.setPen(QColor(CORAL))
        p.drawText(
            QRect(x, y, width, 14),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{pct}%",
        )

        # progress bar
        bar_y = y + 19
        bar_h = 6
        track = QRectF(x, bar_y, width, bar_h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(TRACK_COLOR)
        p.drawRoundedRect(track, bar_h / 2, bar_h / 2)

        frac = max(0, min(100, pct)) / 100.0
        fill_w = width * frac
        if fill_w > 0:
            # keep the fill at least as wide as its own rounded cap
            fill_w = max(fill_w, bar_h)
            fill = QRectF(x, bar_y, fill_w, bar_h)
            p.setBrush(QColor(CORAL))
            p.drawRoundedRect(fill, bar_h / 2, bar_h / 2)

        # reset line under the bar
        if reset_text:
            p.setFont(self._font(10))
            p.setPen(QColor(TEXT_MUTED))
            p.drawText(
                QRect(x, bar_y + bar_h + 4, width, 14),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                reset_text,
            )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    widget = UsageWidget()
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
