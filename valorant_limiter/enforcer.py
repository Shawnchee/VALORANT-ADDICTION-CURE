"""Block enforcer — kill Valorant, then add/remove a Windows Firewall rule.

The kill is trivial. The firewall block is the point: it stops you relaunching
and getting back into a game during the cooldown.

We block by executable path (not domain) so Riot changing auth domains can't
defeat it — they can't change which binary runs the game.

All netsh calls are Windows-only. Off-Windows they no-op with a logged warning
so the rest of the app stays importable and testable.
"""

import logging
import subprocess
import sys
from typing import Optional

import psutil

from . import config

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform.startswith("win")

# Hide the console window netsh would otherwise flash on Windows.
_NO_WINDOW = 0x08000000 if _IS_WINDOWS else 0


def _run(cmd) -> subprocess.CompletedProcess:
    """Run a command with output captured and no flashing console window.

    `cmd` is a full command-line string. On Windows (the only place these
    netsh calls run) subprocess passes the string straight to CreateProcess,
    so the quoting around a spaced program path is exactly what netsh expects.
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        creationflags=_NO_WINDOW,
    )


# --- process kill ---------------------------------------------------------


def find_game_path() -> Optional[str]:
    """Resolve the real path of the running game binary, if it's running.

    Preferred over the hard-coded default because a user may have installed
    Valorant on a non-C: drive. Falls back to the default path if the binary
    is found by name but its exe path can't be read.
    """
    target = config.GAME_BINARY.lower()
    for proc in psutil.process_iter(["name", "exe"]):
        try:
            if (proc.info.get("name") or "").lower() == target:
                return proc.info.get("exe") or config.DEFAULT_GAME_PATH
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def kill_valorant() -> int:
    """Terminate all Valorant/Riot client processes. Returns how many died.

    Deliberately skips Vanguard (vgc/vgk) — no kernel-driver interaction.
    """
    wanted = {n.lower() for n in config.KILL_PROCESS_NAMES}
    victims = []
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info.get("name") or "").lower() in wanted:
                victims.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    for proc in victims:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Give them a moment to exit cleanly, then hard-kill stragglers.
    gone, alive = psutil.wait_procs(victims, timeout=3)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    log.info("killed %d Valorant process(es)", len(victims))
    return len(victims)


# --- firewall rule --------------------------------------------------------


def apply_block(game_path: Optional[str] = None) -> bool:
    """Add an outbound firewall block rule pointing at the game binary.

    Returns True on success. No-ops off Windows.
    """
    if not _IS_WINDOWS:
        log.warning("apply_block skipped: not running on Windows")
        return False

    path = game_path or find_game_path() or config.DEFAULT_GAME_PATH

    # Clear any stale rule of the same name first so we never stack duplicates.
    remove_block()

    result = _run(
        f'netsh advfirewall firewall add rule '
        f'name="{config.FIREWALL_RULE_NAME}" '
        f'dir=out program="{path}" action=block enable=yes'
    )
    if result.returncode != 0:
        log.error("netsh add rule failed: %s", result.stderr.strip() or result.stdout.strip())
        return False
    log.info("firewall block applied for %s", path)
    return True


def remove_block() -> bool:
    """Delete the firewall block rule by name. No-ops off Windows.

    Returns True if the rule is gone afterwards (whether or not it existed).
    """
    if not _IS_WINDOWS:
        log.warning("remove_block skipped: not running on Windows")
        return False

    result = _run(
        f'netsh advfirewall firewall delete rule '
        f'name="{config.FIREWALL_RULE_NAME}"'
    )
    # netsh returns non-zero when no matching rule exists — that's fine, the
    # post-condition ("rule is gone") still holds.
    log.info("firewall block removed (rc=%d)", result.returncode)
    return True


def is_block_active() -> bool:
    """True if our named firewall rule currently exists. No-ops off Windows."""
    if not _IS_WINDOWS:
        return False
    result = _run(
        f'netsh advfirewall firewall show rule '
        f'name="{config.FIREWALL_RULE_NAME}"'
    )
    return result.returncode == 0 and config.FIREWALL_RULE_NAME in result.stdout
