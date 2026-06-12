# Valorant Session Agent

A lightweight Windows tray app that counts your Valorant matches and enforces a
hard session limit. When you hit your limit it closes the game and blocks it
with a Windows Firewall rule — so you can't just relaunch — until a cooldown
expires.

It removes the decision: you don't have to be the one who leaves.

> **Windows only.** It uses UAC elevation, `netsh` firewall rules, and the
> Windows process table. It never touches Vanguard's kernel driver, so there's
> **zero ban risk**.

---

## 1. Install

You need **Python 3.8+** on Windows. The standard Python installer already
includes `tkinter` (used by the settings window).

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

That installs the three dependencies: `psutil`, `pystray`, `Pillow`.

---

## 2. Run

```cmd
python main.py
```

The moment it starts, Windows shows the standard UAC prompt:

> "Do you want to allow this app to make changes to your device?"

Click **Yes**. The app relaunches itself with admin rights (needed to close
processes and manage firewall rules) and a tray icon appears near the clock.

**Want it fully silent (no console window)?** Run it with `pythonw` instead:

```cmd
pythonw main.py
```

Logs still go to a file (see below), so you lose nothing by hiding the console.

---

## 3. Using it — the tray icon

Look in your system tray (bottom-right, you may need to click the `^` arrow):

- **Green icon with a number** — that number is how many games you have left.
- **Red icon with an X** — you've hit your limit; Valorant is blocked.

**Right-click the icon** for the menu:

| Menu item | What it does |
|-----------|--------------|
| *(top line)* | Live status — games left, or "Blocked — unlocks 9:45 PM" |
| **Settings…** | Opens a small window to change your limit and cooldown |
| **Quit** | Stops the agent |

That's the whole interface. No window sits open; it just lives in the tray.

---

## 4. Configure it — the Settings window

Right-click the tray icon → **Settings…**. A small window opens with two fields:

- **Game limit** — how many matches before you're blocked (default **3**)
- **Cooldown (hours)** — how long the block lasts (default **2**)

Change them, click **Save**, and the tray updates immediately. Your settings are
saved and survive restarts and reboots.

> There's no web page or separate app — the tray icon *is* the UI, and Settings
> is the only window. That's deliberate: it stays under 8 MB of RAM and out of
> your way.

---

## 5. What actually happens when you hit your limit

1. The agent tails Valorant's own game log
   (`%LOCALAPPDATA%\VALORANT\Saved\Logs\ShooterGame.log`) for the lines the
   game writes when a match begins and ends. This works per-match — going back
   to the lobby between games still counts as one game finished.
2. After a match-end line appears, a 70-second grace window runs. If nothing
   contradicts it in that window, the match counts. (Quitting Valorant entirely
   is not required — the count ticks up while you're still in the client.)
3. On your last allowed game, the agent closes all Valorant/Riot processes and
   adds an outbound firewall block aimed at the game file.
4. Valorant can still *open*, but it can't reach Riot's servers — it hangs on
   the loading/auth screen until the cooldown lifts.
5. When the cooldown ends, the block is removed automatically and your counter
   resets to zero. (The counter only resets after a full cooldown — never at
   midnight, never early.)

---

## 6. Where your data lives

Everything is local — no network calls, no telemetry, no accounts.

```
%APPDATA%\ValorantLimiter\state.json   your limit, cooldown, and current count
%APPDATA%\ValorantLimiter\agent.log    a plain-text activity log
```

---

## 7. Undoing a block manually

This is a **self-discipline tool, not a parental lock** — every block is
bypassable on purpose:

- **Remove the firewall rule:** open *Windows Defender Firewall → Advanced
  Settings → Outbound Rules*, find the rule named **`ValorantLimiter-Block`**,
  and delete it.
- **Reset your count:** edit `state.json` (above) and set `game_count` to `0`.

The point is to make *quitting* the default — not to make playing impossible.

---

## 8. Good to know

- **If you quit the agent while a cooldown is running, the block stays.** Windows
  keeps firewall rules even when the app isn't open, and only the agent removes
  it — on its next launch. So if a block outlived the app, just start the agent
  again and it lifts the expired block on startup. (Auto-start on boot is a
  planned v2 feature; for now you launch it yourself.)
- **Relaunching Valorant while blocked just hangs at the loading screen** —
  that's the firewall rule doing its job: the client opens but can't reach
  Riot's servers, so login never completes. Killing the agent's tray process
  doesn't free you either; the rule lives in Windows Firewall, not in the
  agent. To get back in early you have to delete the rule manually (see §7).
- Closing Valorant yourself between games is totally fine — the count is based on
  matches actually played, not on how long the launcher is open.
