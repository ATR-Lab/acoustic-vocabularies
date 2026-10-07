# Operator-machine setup checklist

Status: **procedure only; no station has been set up or checked against it**.
Issue: [O5.6.2 / #73](https://github.com/ATR-Lab/acoustic-vocabularies/issues/73).

Common procedures §1 and §2 require an operator display that is separate from
the participant view, with mirroring and notifications turned off. The console
is a web page. It cannot enforce Windows or headset display settings, so staff
must apply and record them on each station. This document lists those settings
and gives a blank table for the results.

Applying these settings is a hardware step. Do it on the actual operator machine
and headset. Nothing in this repository changes OS, account, network, headset or
display settings. Test runs, simulator runs and screenshots of this document do
not count as a completed check.

Related procedures:

- [Quest provisioning](../provisioning/O5.3.2-runbook.md) covers per-unit headset
  settings: update deferral, notifications, boundary, audio route and sleep. This
  checklist does not repeat them. It confirms they still hold before a session.
- [Operator console runbook](../spikes/O5.6.2-runbook.md) covers starting the
  console, the mailbox and the paired capture.
- [Operator display contract](../interfaces/operator-console.md) covers visit
  windows, which use the station's local calendar.

## Scope and rules

- Run these checks once when a station is set up. Repeat them after any OS,
  headset, browser or app update, and before the first session of each day.
- Use only the supported settings screens. Do not change Group Policy, registry,
  firewall, network or domain settings. Contact the lab IT owner if a setting is
  managed or cannot be changed.
- Record the actual menu path and the value you saw on that OS build. Menu names
  change between Windows and Quest OS versions; the names below are a guide.
- If a setting cannot be changed, record "unavailable" and the reason. Do not
  mark it as passed.
- Keep serial numbers, account names and participant data out of this record.
  Use the logical station and unit IDs from the Quest provisioning procedure.

## 1. Windows operator machine

| # | Setting | Expected state | How to check |
|---|---|---|---|
| W1 | Display mode | Operator monitor is a separate display. Displays are **extended**, not duplicated. | Settings > System > Display > Multiple displays. Confirm the console window is not on any display the participant can see. |
| W2 | Casting from this PC | No active cast. No wireless display connected. | Quick Settings > Cast (Win+K). Disconnect any listed device. |
| W3 | Projecting to this PC | Off | Settings > System > Projecting to this PC: "Always off" where available. Record if the optional feature is not installed. |
| W4 | Remote screen sharing | No remote desktop, screen-share or meeting app running or sharing the screen | Check the taskbar and system tray. Close any sharing session. Record the apps you checked. |
| W5 | Notifications | Notifications off, or Do not disturb on for the whole session | Settings > System > Notifications. On Windows 10 this is called Focus Assist. Record the rule used (manual or scheduled). |
| W6 | Notification sounds | No system or app sounds during the session | Same screen as W5, plus Settings > System > Sound. A system sound on a shared audio device could reach the participant route. |
| W7 | OS updates | No update can install or restart during a session | Settings > Windows Update. Pause updates or set active hours to cover session times. Record the pause end date. Check for pending restarts before each session day. |
| W8 | App and driver updates | No automatic app, GPU driver or headset software update during sessions | Record how each is held (for example Microsoft Store auto-update off, Meta Quest Link app version noted). |
| W9 | Sleep and screen off | The machine does not sleep or turn off the operator display during a session | Settings > System > Power > Screen and sleep. Record the values. |
| W10 | Screen lock policy | Follows the lab policy. The screen does not lock in the middle of a session, and staff lock it when they leave the machine. | Record the lab policy and the lock timeout you saw. Do not turn off a lock policy that the lab or IT owner manages. |
| W11 | Screen saver | Off, or longer than the longest booking | Settings > Personalization > Lock screen > Screen saver. |

## 2. Headset (Quest)

These confirm the provisioning settings before a session. They do not replace
the per-unit procedure.

| # | Setting | Expected state | How to check |
|---|---|---|---|
| H1 | Quest casting | No active cast to a phone, browser, PC or TV | Headset quick settings, and the Meta Horizon app on any paired phone. Stop any cast. |
| H2 | Screen recording and live streaming | Not running | Headset camera/sharing menu. |
| H3 | Headset notifications | Off or suppressed, as set during provisioning | Headset Settings > Notifications. Look at the actual headset view for leftover prompts. |
| H4 | Headset OS updates | No update can install during a session | Confirm the deferral method recorded at provisioning still holds. Check for a pending update before each session day. |
| H5 | PC desktop inside the headset (Link only) | The Windows desktop and the console are never shown inside the headset | In Link, the Quest Link home can show a desktop panel. Do not open it. Confirm with the paired headset capture in the runbook that no console content appears. |
| H6 | Sleep, boundary and audio | Same values as the signed provisioning record | Recheck as described in the provisioning procedure. |

## 3. Clock and time zone

The console checks visit windows against the station's local calendar. A wrong
clock or time zone can make a visit look in or out of window.

| # | Check | Expected state | How to check |
|---|---|---|---|
| T1 | Time zone | Matches the lab's local time zone | Settings > Time & language > Date & time, or read-only `Get-TimeZone` in PowerShell. |
| T2 | Automatic time | Set time automatically is on, and the last sync succeeded | Settings > Time & language > Date & time, or read-only `w32tm /query /status`. Do not change the time service setup; contact the IT owner if sync fails. |
| T3 | Clock offset | Within the lab's agreed tolerance of a reference clock | Compare with the lab's reference time source. Record the reference and the offset you saw. No tolerance has been agreed yet. |
| T4 | Daylight-saving dates | Any change falls outside scheduled sessions, or is noted for that day | Check the calendar for visits near a change. |
| T5 | Headset clock (if read) | Same date and time zone as the operator machine | Read only. Note the method used. |

## 4. Console launch

| # | Step | Expected state |
|---|---|---|
| L1 | Start the console | Run the server command from the [runbook](../spikes/O5.6.2-runbook.md) with the private `--config`, `--audit` and `--protocol` values. It prints `Operator console: http://127.0.0.1:<port>`. |
| L2 | Open the page | Open exactly `http://127.0.0.1:<port>` on the operator monitor. Use `127.0.0.1`, not `localhost`: the server rejects a different Host or Origin. |
| L3 | Browser window | One console tab, full screen on the operator monitor. A kiosk or full-screen mode is optional; if used, record the browser, version and launch command. Do not use a browser profile that syncs to a personal account. |
| L4 | Browser extensions and prompts | No extensions that read page content. No password-save, translate or notification prompts covering the console. |
| L5 | Placement | The operator monitor faces away from the participant's seat. The participant cannot see it when the headset is off. |
| L6 | Single console | Only one console process uses the mailbox. A second console is refused by the mailbox lock. After a crash, inspect a stale lock; do not delete evidence. |

## 5. Results record (blank)

Fill one table per station per setup or recheck. Leave rows blank until the
setting has been observed on the actual machine or headset. Copy the observed
values into the private station record. Set `station_time_zone_verified` and
`casting_mirroring_notifications_disabled` in
`apparatus/spikes/O5.6.2/results.template.json` only after every row below is
complete. Keep those fields `null` until then.

Station ID: ______  Unit ID: ______  Purpose (setup / daily recheck / after update): ______

| Item | Setting | Expected | Observed | Operator | Date |
|---|---|---|---|---|---|
| W1 | Display mode | Extended; console on operator monitor only | | | |
| W2 | Casting from this PC | No active cast | | | |
| W3 | Projecting to this PC | Off | | | |
| W4 | Remote screen sharing | None running | | | |
| W5 | Notifications | Off / Do not disturb on | | | |
| W6 | Notification sounds | None | | | |
| W7 | OS updates | Paused or outside session times; no pending restart | | | |
| W8 | App and driver updates | Held; versions recorded | | | |
| W9 | Sleep and screen off | No sleep during session | | | |
| W10 | Screen lock policy | Lab policy recorded; no mid-session lock | | | |
| W11 | Screen saver | Off or longer than booking | | | |
| H1 | Quest casting | No active cast | | | |
| H2 | Recording and streaming | Not running | | | |
| H3 | Headset notifications | Suppressed | | | |
| H4 | Headset OS updates | Deferred; no pending update | | | |
| H5 | PC desktop in headset (Link) | Not shown | | | |
| H6 | Sleep, boundary, audio | Match provisioning record | | | |
| T1 | Time zone | Lab local time zone | | | |
| T2 | Automatic time | On; last sync succeeded | | | |
| T3 | Clock offset | Within agreed tolerance (not yet agreed) | | | |
| T4 | Daylight-saving dates | None during sessions, or noted | | | |
| T5 | Headset clock | Matches operator machine | | | |
| L1 | Console start | Server prints 127.0.0.1 address | | | |
| L2 | Console URL | `http://127.0.0.1:<port>` loads | | | |
| L3 | Browser window | One full-screen console tab | | | |
| L4 | Extensions and prompts | None covering the console | | | |
| L5 | Monitor placement | Not visible from participant seat | | | |
| L6 | Single console | One console on the mailbox | | | |

## Limitations

- No station has been set up or checked with this list. All results are blank.
- Menu paths are a guide for Windows 11 and current Quest OS. Record the actual
  path on the installed build.
- The clock tolerance in T3 has not been agreed. The check stays incomplete
  until the apparatus owner sets it.
- A completed checklist does not prove that no console content reaches the
  headset. That still needs the paired operator/headset capture in the runbook.
