"""Tray UI — system tray icon, status menu, settings dialog, block popup.

Green icon with the games-remaining number while you still have games left;
turns red when blocked. Right-click for status, settings, and quit. No
persistent window.

pystray drives the tray loop. The settings dialog is a small tkinter window
opened on demand in its own thread (tkinter and pystray each want their own
event loop, so we don't share a root between them).
"""

import logging
import threading
import time

from PIL import Image, ImageDraw
import pystray

from . import config

log = logging.getLogger(__name__)


class TrayUI:
    def __init__(self, state, on_quit):
        """
        state:   the shared State object (read for display, written by settings)
        on_quit: called when the user picks Quit (app does its own teardown)
        """
        self._state = state
        self._on_quit = on_quit
        self._icon = pystray.Icon(
            config.APP_NAME,
            icon=self._render_icon(),
            title=self._tooltip(),
            menu=self._build_menu(),
        )

    # --- icon rendering ---------------------------------------------------

    def _render_icon(self) -> Image.Image:
        """A filled circle with the games-remaining count. Red when blocked."""
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        blocked = self._state.blocked
        fill = (211, 47, 47, 255) if blocked else (56, 142, 60, 255)  # red / green
        draw.ellipse([2, 2, size - 2, size - 2], fill=fill)

        label = "X" if blocked else str(self._state.games_remaining())
        # Center the label; default bitmap font keeps zero extra dependencies.
        try:
            bbox = draw.textbbox((0, 0), label)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            tw, th = 8 * len(label), 11
        draw.text(((size - tw) / 2, (size - th) / 2 - 2), label, fill="white")
        return img

    def _tooltip(self) -> str:
        if self._state.blocked:
            return f"{config.APP_NAME} — blocked, {self._unlock_str()}"
        return f"{config.APP_NAME} — {self._state.games_remaining()} games left"

    def _unlock_str(self) -> str:
        secs = self._state.seconds_until_unlock()
        if secs <= 0:
            return "unlocking…"
        unlock_at = time.localtime(time.time() + secs)
        return "unlocks " + time.strftime("%I:%M %p", unlock_at).lstrip("0")

    # --- menu -------------------------------------------------------------

    def _status_text(self) -> str:
        if self._state.blocked:
            return f"Blocked — {self._unlock_str()}"
        return (
            f"{self._state.games_remaining()} of {self._state.limit} games left"
        )

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(lambda _: self._status_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings…", self._open_settings),
            pystray.MenuItem("Quit", self._quit),
        )

    # --- public API (called from app/watcher threads) --------------------

    def run(self) -> None:
        """Blocks on the tray event loop. Call on the main thread."""
        self._icon.run()

    def refresh(self) -> None:
        """Redraw icon + tooltip after a state change. Thread-safe.

        Safe to call before the tray loop is running (e.g. startup
        reconciliation): setting icon/title just updates attributes, and
        update_menu is best-effort since the menu uses callable text that
        re-evaluates whenever it's opened anyway.
        """
        try:
            self._icon.icon = self._render_icon()
            self._icon.title = self._tooltip()
        except Exception:
            log.exception("tray icon refresh failed")
        try:
            self._icon.update_menu()
        except Exception:
            pass  # icon not running yet; menu refreshes itself on open

    def notify_block(self) -> None:
        """Popup shown the moment the limit is hit."""
        msg = (
            f"Session limit of {self._state.limit} games reached.\n"
            f"Valorant is blocked — {self._unlock_str()}."
        )
        try:
            self._icon.notify(msg, "Valorant Limiter")
        except Exception:
            # notify() isn't supported on every backend; the red icon + tooltip
            # still communicate the block, so this is non-fatal.
            log.info("block notification: %s", msg.replace("\n", " "))

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:
            pass

    # --- menu callbacks ---------------------------------------------------

    def _quit(self, icon=None, item=None) -> None:
        self._on_quit()
        self.stop()

    def _open_settings(self, icon=None, item=None) -> None:
        # Run the dialog in its own thread with its own tkinter root so it
        # doesn't fight the pystray loop.
        threading.Thread(target=self._settings_dialog, daemon=True).start()

    def _settings_dialog(self) -> None:
        try:
            import tkinter as tk
            from tkinter import messagebox
        except Exception:
            log.error("tkinter unavailable; cannot open settings")
            return

        root = tk.Tk()
        root.title("Valorant Limiter — Settings")
        root.resizable(False, False)

        tk.Label(root, text="Game limit:").grid(row=0, column=0, padx=10, pady=8, sticky="e")
        limit_var = tk.StringVar(value=str(self._state.limit))
        tk.Entry(root, textvariable=limit_var, width=8).grid(row=0, column=1, padx=10, pady=8)

        tk.Label(root, text="Cooldown (hours):").grid(row=1, column=0, padx=10, pady=8, sticky="e")
        cooldown_var = tk.StringVar(value=str(self._state.cooldown_hours))
        tk.Entry(root, textvariable=cooldown_var, width=8).grid(row=1, column=1, padx=10, pady=8)

        def save():
            try:
                new_limit = int(limit_var.get())
                new_cooldown = float(cooldown_var.get())
                if new_limit < 1 or new_cooldown <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Invalid", "Limit must be a whole number ≥ 1 and "
                    "cooldown must be a positive number of hours."
                )
                return
            self._state.set_limit(new_limit)
            self._state.set_cooldown_hours(new_cooldown)
            self.refresh()
            root.destroy()

        tk.Button(root, text="Save", command=save, width=10).grid(
            row=2, column=0, columnspan=2, pady=(4, 12)
        )

        root.update_idletasks()
        root.mainloop()
