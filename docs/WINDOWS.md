# Running a Windows device

> [!WARNING]
> Written on Linux. None of this has run on Windows yet, so expect to fix things on the first pass.

A Windows machine runs as a device, like the MacBook. The hub stays on the NAS.

## Install

1. Install [uv](https://docs.astral.sh/uv/) and [Tailscale](https://tailscale.com), and clone this repository.
2. Install dusha with the optional readers:

   ```powershell
   uv tool install dusha --with psutil --with watchdog --with Pillow --with nvidia-ml-py --with icalendar --with recurring-ical-events --with winrt-Windows.Media.Control --with winrt-Windows.Foundation
   ```

3. Copy [config.device.example.json](../config.device.example.json) to `%USERPROFILE%\.config\dusha\<name>\config.json`, and set `device`, `plugins_dir`, and the tokens.
4. Set `plugins.spawn.options.agents` to an agent installed here, or drop `spawn`.
5. Allow the port through the firewall for Tailscale only:

   ```powershell
   New-NetFirewallRule -DisplayName dusha -Direction Inbound -Protocol TCP -LocalPort 8765 -InterfaceAlias Tailscale -Action Allow
   ```

6. Start it at logon, so it runs inside your session. Idle time, toasts, and screen capture all need the session, which rules out a Windows service:

   ```powershell
   schtasks /Create /SC ONLOGON /TN dusha /TR "%USERPROFILE%\.local\bin\dusha.exe serve <name>"
   ```

`dusha proxy` imports `fcntl` in the 1.1.21 release, so until a release carries the fix, use `report_to` and `forward_to` as `{"url": "...", "token": "..."}` instead of `"proxy"`.

## What works

| Piece | On Windows |
| --- | --- |
| presence | `GetLastInputInfo` and the input desktop for locks. No pre-sleep goodbye, so a sleeping machine shows as `unreachable`. |
| notification | PowerShell toasts, shown as Windows PowerShell. No click events. |
| ambient metrics, gpu | psutil and NVML. |
| ambient screen | `PIL.ImageGrab`, all monitors. |
| ambient files, browser, calendar | watchdog, and profiles under `AppData`. |
| ambient packages | Only whether Windows Update wants a reboot. |
| ambient network | Per-process connections. No DNS. |
| music | Windows media sessions. No per-player volume. Local songs open in the default player. |
| hands | `cmd` for `shell`, `SendKeys` for input, process titles for windows. Keys use SendKeys syntax, such as `{ENTER}`. |
| voice | `Media.SoundPlayer`, so keep `response_format` at `wav`, or set `player`. |
| spawn | Any ACP agent on `PATH`. |

The journal and container sources stay off.

## First run checklist

Run these and fix what breaks:

1. `dusha plugins` shows every plugin active.
2. Lock the screen, and `dusha presence status` should say `locked`. Wait 5 minutes without input for `idle`.
3. `dusha notification send hi there --channel desktop` shows a toast.
4. Play something, and `dusha music status` lists it.
5. `dusha voice say hello` speaks.
6. `dusha hands shell "echo hi"` answers.
7. From the hub: `dusha relay look --device <name>`, then `dusha relay spawn "say hi" --device <name>`.
