"""State store — a single JSON file at %APPDATA%\\ValorantLimiter\\state.json.

Holds the game count, the configurable limit and cooldown, and the cooldown
end timestamp. Persists across reboots. No database, no registry writes.

All public mutators persist immediately so a crash never loses a counted game.
A lock guards reads/writes because the watcher thread and the tray thread both
touch state.
"""

import json
import os
import threading
import time
from typing import Optional

from . import config


class State:
    def __init__(self, path: Optional[str] = None):
        self._path = path or config.state_file_path()
        self._lock = threading.RLock()

        # Defaults — overwritten by _load() if a file already exists.
        self.game_count: int = 0
        self.limit: int = config.DEFAULT_LIMIT
        self.cooldown_hours: float = config.DEFAULT_COOLDOWN_HOURS
        self.cooldown_end: Optional[float] = None  # epoch seconds, or None
        self.blocked: bool = False

        self._load()

    # --- persistence ------------------------------------------------------

    def _load(self) -> None:
        with self._lock:
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                # First run or corrupt file -> start clean and write defaults.
                self.save()
                return

            self.game_count = int(data.get("game_count", 0))
            self.limit = int(data.get("limit", config.DEFAULT_LIMIT))
            self.cooldown_hours = float(
                data.get("cooldown_hours", config.DEFAULT_COOLDOWN_HOURS)
            )
            ce = data.get("cooldown_end")
            self.cooldown_end = float(ce) if ce is not None else None
            self.blocked = bool(data.get("blocked", False))

    def save(self) -> None:
        with self._lock:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            payload = {
                "game_count": self.game_count,
                "limit": self.limit,
                "cooldown_hours": self.cooldown_hours,
                "cooldown_end": self.cooldown_end,
                "blocked": self.blocked,
            }
            # Atomic write: write to a temp file then replace, so a crash
            # mid-write can never leave a half-written state.json.
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, self._path)

    # --- derived ----------------------------------------------------------

    def games_remaining(self) -> int:
        return max(0, self.limit - self.game_count)

    def cooldown_active(self) -> bool:
        """True while a cooldown timer is set and has not yet elapsed."""
        with self._lock:
            return self.cooldown_end is not None and time.time() < self.cooldown_end

    def cooldown_expired(self) -> bool:
        """True when a cooldown was set and its end time has passed."""
        with self._lock:
            return self.cooldown_end is not None and time.time() >= self.cooldown_end

    def seconds_until_unlock(self) -> int:
        with self._lock:
            if self.cooldown_end is None:
                return 0
            return max(0, int(self.cooldown_end - time.time()))

    # --- mutators (all persist immediately) -------------------------------

    def increment_game(self) -> int:
        with self._lock:
            self.game_count += 1
            self.save()
            return self.game_count

    def start_cooldown(self) -> None:
        with self._lock:
            self.cooldown_end = time.time() + self.cooldown_hours * 3600
            self.blocked = True
            self.save()

    def clear_cooldown_and_reset(self) -> None:
        """Cooldown fully completed: wipe the count and unblock."""
        with self._lock:
            self.game_count = 0
            self.cooldown_end = None
            self.blocked = False
            self.save()

    def set_limit(self, limit: int) -> None:
        with self._lock:
            self.limit = max(1, int(limit))
            self.save()

    def set_cooldown_hours(self, hours: float) -> None:
        with self._lock:
            self.cooldown_hours = max(0.1, float(hours))
            self.save()
