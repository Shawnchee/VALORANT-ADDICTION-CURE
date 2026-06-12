"""Log watcher — detects matches by tailing VALORANT's ShooterGame.log.

Why log-tailing, not process-watching: VALORANT-Win64-Shipping.exe lives for
the whole client session — from clicking Play in the Riot Client until the
user fully quits the game. A deathmatch ending sends you back to the lobby
without exiting the binary, so the old process-exit signal under-counted.

Signals read from the log (cheap substring checks per new line):
  - Match start: '[Match Setup: TRUE | Changed: TRUE]'   (LogMapLoadModel)
  - Match end:   'LogShooterGameState: Match Ended:'

State machine:
    IDLE       no match in progress, no grace timer
    IN_MATCH   saw a start marker, not yet an end marker
    GRACE      saw an end marker, 70s wall-clock timer armed
On grace elapse: count += 1, back to IDLE. A new start during grace cancels
the timer (paranoid guard against the log double-emitting an end line).

Log rotation: when Valorant relaunches it renames ShooterGame.log to a
ShooterGame-backup-*.log and starts a fresh file at the same path. The tail
notices the file shrunk (or disappeared) and resets to byte 0.

Startup: position is seeded to the current end of the log so prior matches
already written there are not retroactively counted. State is re-derived by
scanning the tail of the file for the most recent start/end marker, so an
agent restart mid-match correctly resumes in IN_MATCH.

psutil is still used (lazily, only while the binary exists) to capture the
real install path so the firewall block can target a non-default install.
"""

import logging
import os
import threading
import time
from typing import Callable, Optional

import psutil

from . import config

log = logging.getLogger(__name__)

# How far back to scan on startup when re-deriving state from an existing
# log. 256 KiB easily covers a full match's worth of lines.
_STARTUP_TAIL_BYTES = 256 * 1024


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

        # State machine.
        self._in_match = False       # saw a start since the last end?
        self._grace_until = 0.0      # epoch; >0 means grace timer is armed

        # Log tail bookkeeping.
        self._log_path = config.log_file_path()
        self._log_pos = 0            # byte offset into the current log file
        self._log_inode_size = -1    # last-known size, for rotation detection

        # Real exe path of the game binary, captured opportunistically while
        # it's running. Used by the enforcer to aim the firewall block at the
        # actual install path (not the hard-coded default).
        self.game_path: Optional[str] = None

    # --- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._seed_from_existing_log()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout=None) -> None:
        self._thread.join(timeout)

    # --- log tail ---------------------------------------------------------

    def _seed_from_existing_log(self) -> None:
        """On startup, jump to EOF and recover state from the recent tail.

        If the log already contains a Match Setup line newer than the last
        Match Ended line, we resume in IN_MATCH so the next end-marker counts
        the in-progress match. Otherwise we start IDLE. Either way we do not
        replay historical matches.
        """
        try:
            size = os.path.getsize(self._log_path)
        except OSError:
            # Log not present yet — Valorant hasn't run since install, or
            # the path is wrong. Start IDLE; the main loop will pick the file
            # up when it appears.
            self._log_pos = 0
            self._log_inode_size = -1
            return

        self._log_pos = size
        self._log_inode_size = size

        try:
            with open(self._log_path, "rb") as fh:
                fh.seek(max(0, size - _STARTUP_TAIL_BYTES))
                tail = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return

        last_start = tail.rfind(config.LOG_MATCH_START_MARKER)
        last_end = tail.rfind(config.LOG_MATCH_END_MARKER)
        if last_start > last_end:
            self._in_match = True
            log.info("startup: log shows a match already in progress")

    def _read_new_lines(self) -> list[str]:
        """Return any new lines appended since the last poll.

        Handles rotation: if the file got smaller (or vanished) since last
        read, reset to byte 0 of whatever is at the path now.
        """
        try:
            size = os.path.getsize(self._log_path)
        except OSError:
            # Log gone — Valorant probably not running. Forget our position
            # so we re-seed when it returns.
            self._log_pos = 0
            self._log_inode_size = -1
            return []

        if size < self._log_pos:
            # Truncated or rotated. Start fresh from the top.
            self._log_pos = 0
        self._log_inode_size = size

        if size == self._log_pos:
            return []

        try:
            with open(self._log_path, "rb") as fh:
                fh.seek(self._log_pos)
                chunk = fh.read(size - self._log_pos)
                self._log_pos = fh.tell()
        except OSError:
            return []

        text = chunk.decode("utf-8", errors="replace")
        return text.splitlines()

    # --- main loop --------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:  # never let the watcher thread die silently
                log.exception("watcher poll failed")

            interval = (
                config.POLL_RUNNING_SECONDS
                if (self._in_match or self._grace_until)
                else config.POLL_IDLE_SECONDS
            )
            self._stop.wait(interval)

    def _poll_once(self) -> None:
        self._on_tick()

        # Opportunistically capture install path while the binary is alive.
        # Cheap enough at 5s cadence and only matters until we have a path.
        if self.game_path is None:
            self.game_path = _capture_game_path()

        for line in self._read_new_lines():
            if config.LOG_MATCH_START_MARKER in line:
                self._handle_start()
            elif config.LOG_MATCH_END_MARKER in line:
                self._handle_end()

        # Grace timer is checked every poll, regardless of new log activity,
        # so a quiet log after Match Ended still triggers the count.
        if self._grace_until and time.time() >= self._grace_until:
            self._grace_until = 0.0
            self._in_match = False
            log.info("match completed (grace elapsed)")
            self._on_game_end()

    def _handle_start(self) -> None:
        # New match beginning. If a grace timer was somehow armed (e.g. the
        # log re-emitted an end line), cancel it — this single match should
        # only count once, when it actually ends.
        if self._grace_until:
            log.debug("match start during grace; cancelling timer")
            self._grace_until = 0.0
        self._in_match = True
        log.info("match started")

    def _handle_end(self) -> None:
        if not self._in_match:
            # End marker without a preceding start (agent started mid-match
            # with the tail empty, or duplicate end line). Count it anyway —
            # the user really did finish a match.
            log.debug("match end without observed start; counting anyway")
            self._in_match = True
        if not self._grace_until:
            self._grace_until = time.time() + config.GRACE_SECONDS
            log.info("match ended; grace armed for %ds", config.GRACE_SECONDS)


# --- helpers --------------------------------------------------------------


def _capture_game_path() -> Optional[str]:
    """One-off lookup of the running game binary's real exe path.

    Returns None if the binary isn't running right now. The watcher caches
    the first non-None result for the firewall to use later, after the
    binary is gone.
    """
    target = config.GAME_BINARY.lower()
    for proc in psutil.process_iter(["name", "exe"]):
        try:
            if (proc.info.get("name") or "").lower() == target:
                return proc.info.get("exe") or config.DEFAULT_GAME_PATH
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None
