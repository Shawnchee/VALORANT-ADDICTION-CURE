"""Coordinator — wires state, watcher, enforcer, and tray into one agent.

Flow:
  startup     reconcile state with reality (was a block active? has it expired?)
  per match   watcher -> on_game_end -> count++ -> if limit hit, block
  per poll    watcher -> on_tick -> if cooldown expired, lift the block
  quit        stop the watcher cleanly
"""

import logging

from . import enforcer
from .state import State
from .tray import TrayUI
from .watcher import ProcessWatcher

log = logging.getLogger(__name__)


class App:
    def __init__(self):
        self._state = State()
        self._tray = TrayUI(self._state, on_quit=self._teardown)
        self._watcher = ProcessWatcher(
            on_game_end=self._on_game_end,
            on_tick=self._on_tick,
        )

    # --- run / teardown ---------------------------------------------------

    def run(self) -> None:
        self._reconcile_on_startup()
        self._watcher.start()
        # Tray loop blocks the main thread until Quit.
        self._tray.run()

    def _teardown(self) -> None:
        log.info("shutting down")
        self._watcher.stop()

    # --- startup reconciliation ------------------------------------------

    def _reconcile_on_startup(self) -> None:
        """Make the world match persisted state after a (re)launch or reboot.

        Firewall rules persist across reboots, so on startup we must decide
        whether an existing block should stay, be re-applied, or be lifted.
        """
        if self._state.blocked:
            if self._state.cooldown_expired():
                log.info("cooldown already expired at startup; lifting block")
                self._lift_block()
            else:
                # Still serving time. Make sure the rule is actually in place
                # (it should be, but re-apply defensively in case it was removed
                # while the agent wasn't running).
                if not enforcer.is_block_active():
                    enforcer.apply_block()
                log.info("cooldown still active at startup; block enforced")
                self._tray.refresh()
        else:
            # Not blocked — clear any stale rule left behind by a crash.
            if enforcer.is_block_active():
                enforcer.remove_block()
            self._tray.refresh()

    # --- watcher callbacks ------------------------------------------------

    def _on_game_end(self) -> None:
        count = self._state.increment_game()
        log.info("game %d of %d", count, self._state.limit)
        self._tray.refresh()
        if count >= self._state.limit:
            self._trigger_block()

    def _on_tick(self) -> None:
        # Cheap per-poll check: has an active cooldown elapsed?
        if self._state.blocked and self._state.cooldown_expired():
            log.info("cooldown elapsed; lifting block")
            self._lift_block()

    # --- block lifecycle --------------------------------------------------

    def _trigger_block(self) -> None:
        log.info("limit reached; killing Valorant and applying block")
        # Use the install path the watcher captured live during the match. By
        # now the binary is dead (grace elapsed), so we can't resolve it fresh;
        # apply_block falls back to the default path if it was never captured.
        path = self._watcher.game_path
        enforcer.kill_valorant()
        enforcer.apply_block(path)
        self._state.start_cooldown()
        self._tray.refresh()
        self._tray.notify_block()

    def _lift_block(self) -> None:
        enforcer.remove_block()
        self._state.clear_cooldown_and_reset()
        self._tray.refresh()


def run() -> None:
    App().run()
