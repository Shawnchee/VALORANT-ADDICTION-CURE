"""Static configuration and constants for the Valorant Session Agent.

Everything the user can change at runtime (limit, cooldown) lives in state.json
and is handled by state.py. This file holds only the fixed knobs.
"""

import os

# --- App identity ---------------------------------------------------------

APP_NAME = "ValorantLimiter"
FIREWALL_RULE_NAME = "ValorantLimiter-Block"

# --- Process detection ----------------------------------------------------

# Only this binary spawns during an actual match and dies when it ends.
# Watching it = counting matches played, not launcher uptime.
GAME_BINARY = "VALORANT-Win64-Shipping.exe"

# Confirmed default install path of the game binary. The firewall block
# points directly at this path. If a user installed Valorant elsewhere the
# path is resolved at runtime from the running process (see enforcer.py).
DEFAULT_GAME_PATH = (
    r"C:\Riot Games\VALORANT\live\ShooterGame\Binaries\Win64"
    r"\VALORANT-Win64-Shipping.exe"
)

# Processes killed when the limit is hit. We deliberately do NOT touch
# Vanguard (vgc.exe / vgk.sys) — zero interaction with the kernel driver,
# zero ban risk. The firewall rule is what actually enforces the block.
KILL_PROCESS_NAMES = (
    "VALORANT-Win64-Shipping.exe",
    "VALORANT.exe",
    "RiotClientServices.exe",
    "RiotClientUx.exe",
    "RiotClientUxRender.exe",
)

# --- Timing ---------------------------------------------------------------

# Grace window: the binary briefly restarts during agent-select -> map-load.
# 70s safely absorbs that flicker so one match never counts as two.
GRACE_SECONDS = 70

# Poll cadence. Slower while a match is running (we only need to notice the
# binary disappear), faster while idle/in-grace for responsiveness.
POLL_IDLE_SECONDS = 5
POLL_RUNNING_SECONDS = 10

# --- Defaults (seed values for a fresh state.json) ------------------------

DEFAULT_LIMIT = 3
DEFAULT_COOLDOWN_HOURS = 2.0

# --- Paths ----------------------------------------------------------------


def app_data_dir() -> str:
    """Return %APPDATA%\\ValorantLimiter, falling back to ~ off-Windows.

    Off-Windows (dev/test on macOS or Linux) APPDATA is unset, so we fall
    back to the home directory. This keeps state.py importable and testable
    everywhere while the real target stays Windows.
    """
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)


def state_file_path() -> str:
    return os.path.join(app_data_dir(), "state.json")
