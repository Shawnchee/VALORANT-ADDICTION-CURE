"""UAC auto-elevation.

Admin rights are required to kill processes and manage firewall rules. On
launch we check for admin; if we don't have it, we relaunch ourselves elevated
via the standard Windows "runas" verb, which triggers the grey UAC prompt. The
user clicks Yes once and the elevated instance takes over.

This is the same pattern installers and Task Manager use. No third-party deps.
"""

import ctypes
import logging
import os
import sys

log = logging.getLogger(__name__)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        # Non-Windows (no windll) or the call failed -> treat as not admin.
        return False


def ensure_admin() -> None:
    """Guarantee the process runs elevated, relaunching via UAC if needed.

    If already admin: returns and the caller continues.
    If not admin: spawns an elevated copy of this same script and exits the
    current (unelevated) process so only the elevated instance runs.
    """
    if not sys.platform.startswith("win"):
        # Off-Windows there is no UAC; let it run so dev/testing works.
        log.warning("elevation skipped: not running on Windows")
        return

    if is_admin():
        return

    # Re-exec ourselves elevated. ShellExecuteW with "runas" raises the UAC
    # prompt. An elevated process starts in system32, NOT the original cwd, so
    # we make the script path absolute and pass its directory as the working
    # directory — otherwise the elevated copy can't find main.py.
    script = os.path.abspath(sys.argv[0])
    workdir = os.path.dirname(script)
    params = " ".join(f'"{arg}"' for arg in [script, *sys.argv[1:]])
    rc = ctypes.windll.shell32.ShellExecuteW(
        None,          # parent window
        "runas",       # verb -> request elevation (UAC prompt)
        sys.executable,  # the python interpreter
        params,        # absolute script path + args, quoted
        workdir,       # working directory = the script's folder
        1,             # SW_SHOWNORMAL
    )
    # ShellExecuteW returns a value > 32 on success.
    if rc <= 32:
        log.error("UAC elevation failed or was declined (code %s)", rc)
    # Exit the unelevated process either way — the elevated copy (if launched)
    # is now the live instance. If declined, the app simply does not run.
    sys.exit(0)
