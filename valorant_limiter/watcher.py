"""Process watcher — detects matches by watching the game binary.

Single background thread. Polls psutil for VALORANT-Win64-Shipping.exe and runs
a small state machine:

    IDLE      binary absent                         -> poll every 5s
    RUNNING   binary present                        -> poll every 10s
    GRACE     binary just vanished, 70s timer armed -> poll every 5s

A match is counted exactly once, on confirmed end:
  - binary appears                -> match running (no count yet)
  - binary disappears             -> arm 70s grace timer
  - reappears within 70s          -> cancel timer (this was the agent-select
                                     -> map-load flicker, still one match)
  - 70s elapse with no reappear   -> count += 1, back to IDLE

Because the whole lifecycle of one binary instance maps to one count, a
surrender or remake (which ends the binary early) correctly counts as 1 game.

Grace is measured by wall-clock timestamps, not poll counts, so the 10s poll
cadence never distorts the 70s window.
"""

import logging
import threading
import time
from typing import Callable

import psutil

from . import config

log = logging.getLogger(__name__)


class ProcessWatcher:
    def __init__(self, on_game_end: Callable[[], None],
                 on_tick: Callable[[], None] = lambda: None):
        """
        on_game_end: called once per completed match (after the grace window).
        on_tick:     called every poll; used by the app to check cooldown
                     expiry without spawning a second thread.
        """
        self._on_game_end = on_game_end
        self._on_tick = on_tick
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name="ProcessWatcher", daemon=True
        )

        # State machine flags.
        self._running = False        # is a match currently considered active?
        self._grace_until = 0.0      # epoch; >0 means grace timer is armed

        # Real exe path of the game binary, captured once while it's running
        # (the binary is dead by count-time, so we must grab it live). Used to
        # aim the firewall block at the actual install path, default or not.
        self.game_path = None

    # --- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout=None) -> None:
        self._thread.join(timeout)

    # --- detection --------------------------------------------------------

    @staticmethod
    def _binary_present() -> bool:
        target = config.GAME_BINARY.lower()
        for proc in psutil.process_iter(["name"]):
            try:
                if (proc.info.get("name") or "").lower() == target:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    @staticmethod
    def _capture_path():
        """One-off lookup of the running game binary's real exe path.

        Heavier than _binary_present (reads exe), so it's called only once per
        match — when we first see the binary — not on every idle poll.
        """
        target = config.GAME_BINARY.lower()
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                if (proc.info.get("name") or "").lower() == target:
                    return proc.info.get("exe") or config.DEFAULT_GAME_PATH
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None

    # --- main loop --------------------------------------------------------

    def _loop(self) -> None:
        # Seed state from reality so a binary already running at startup is
        # treated as a match in progress rather than a fresh start.
        self._running = self._binary_present()

        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:  # never let the watcher thread die silently
                log.exception("watcher poll failed")

            # Faster cadence while idle or mid-grace; slower while a match runs.
            interval = (
                config.POLL_RUNNING_SECONDS
                if (self._running and self._grace_until == 0.0)
                else config.POLL_IDLE_SECONDS
            )
            self._stop.wait(interval)

    def _poll_once(self) -> None:
        self._on_tick()
        present = self._binary_present()
        now = time.time()

        if present:
            # Capture the real install path once, while the binary is alive.
            if self.game_path is None:
                self.game_path = self._capture_path()
            # If a grace timer was armed, this is the flicker coming back —
            # cancel it; still the same single match.
            if self._grace_until:
                log.debug("binary reappeared within grace; not counting")
                self._grace_until = 0.0
            self._running = True
            return

        # Binary is absent.
        if self._running and not self._grace_until:
            # Match just ended (or flickered). Arm the grace window.
            self._grace_until = now + config.GRACE_SECONDS
            log.debug("binary gone; grace armed for %ds", config.GRACE_SECONDS)
            return

        if self._grace_until and now >= self._grace_until:
            # Grace elapsed with no reappearance -> a real, completed match.
            self._grace_until = 0.0
            self._running = False
            log.info("match completed (grace elapsed)")
            self._on_game_end()
