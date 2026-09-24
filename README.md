# WireSpot

**WireGuard × Mobile Hotspot** for Windows 10/11 (formerly *ProtonRelay CLI*).

Turns a Windows laptop into a Wi-Fi router whose only way out is a WireGuard
(Proton VPN) tunnel:

**Website:** https://wirespot.vercel.app · **Installer:** [WireSpotSetup.exe](https://github.com/svyixiu/WireSpot/releases/latest/download/WireSpotSetup.exe) (Windows 10/11, v0.2.1).

    Existing Wi-Fi → laptop → WireGuard (Proton) → Mobile Hotspot → phone / console

## Quick start

1. Install [WireGuard for Windows](https://www.wireguard.com/install/).
2. Run **`WireSpotSetup.exe`**. A short wizard explains what WireSpot is and what it
   changes, checks for WireGuard and an older WireSpot, then shows where things go
   (program: `%LOCALAPPDATA%\Programs\WireSpot`, data: `%APPDATA%\WireSpot`) with options:
   desktop shortcut, Start menu entry, Start with Windows, open when done. Run next to an old
   `portable\` folder, it offers to move those VPN profiles over (moved, not copied, so each
   private key exists once). An older WireSpot that won't close is closed for you on request;
   the VPN and hotspot keep running.
3. Get a WireGuard `.conf` from your VPN provider. For Proton VPN, [sign in or create
   an account](https://account.protonvpn.com/), open **Downloads → WireGuard configuration**,
   choose Windows and a server in the country you want, then select **Create → Download**.
   [Proton's official guide](https://protonvpn.com/support/wireguard-configurations) shows
   the screens. Keep the `.conf` private: it contains a VPN key. In WireSpot, open
   **Profiles → Import a .conf file…**; new Proton configs in Downloads are also offered
   as a review card.
4. Set the hotspot name and password under **Hotspot**, then press **Go live**.

The command line opens from the app (**Open WireSpot CLI**); there is no separate
shortcut for it.

## Desktop app

The window **is the tray menu**, as an app: the same list, the same rows, the same live
values (`panel.py` renders both). It is tall and narrow, with fully custom chrome: no
Windows title bar, no resize border, two mac-style dots top-left (**close** hides it to
the tray, **minimise** sends it to the taskbar), and you drag it by the header. It is still a
normal app on the taskbar: click the button to minimise or restore it, right-click it for
*Close window*; Alt-Tab and Alt+F4 work. Every
symbol is an SVG icon (`wirespot/icons.py`, exported to `assets/icons/`), not a Unicode
glyph.

Six tiles open pages that slide in: **Devices**, **Profiles**, **Hotspot**, **Checks**,
**Activity** and **Settings**. Rows fade on hover, switches slide, sheets rise over a
dimmed copy of the page, scrolling glides, and live values update in place (no rebuild,
no blinking).

| Page | What it does |
|---|---|
| **Devices** | waiting devices (Allow / Block), connected devices (IP, name, device guess, MAC, vendor), blocked and remembered devices |
| **Profiles** | WireGuard configs as cards: import, open the VPN folder, make default, check endpoint/handshake health, go live with a profile |
| **Hotspot** | name, password (show/hide), band, security; balanced vs strict protection |
| **Checks** | Quick, Wi-Fi, VPN, Hotspot, Sharing, Network, Devices and Full checks; user-started Cloudflare speed test; save a report |
| **Activity** | what WireSpot did, in the app **and in the CLI** (secrets redacted) |
| **Settings** | Start with Windows, Go live at startup, Approve new devices, fail-closed guard, DNS lock, watch Downloads, debug logging; open the data folder, the VPN folder, settings.json, the logs, the CLI; **Uninstall** |

Decisions (for example "use balanced for this session?") and new `.conf` files appear as
sheets with the recommended choice marked. **Quit WireSpot** exits; the VPN and hotspot
keep running. Launching WireSpot again brings the running window forward.

## Device approval

With **Approve new devices** on (the default), a device that joins the hotspot gets
**no network until you allow it**: a toast with **Allow / Block** appears, the tray
menu and window show it under *Waiting for approval*, and the CLI prints it (`allow 1`,
`block 1`, `waiting`). Allowed devices are remembered; **Forget** asks again next time.

Mobile Hotspot has no per-device API, so WireSpot enforces this on the laptop: within
about a second of a new device's first packet it pins that device's address on the
hotspot adapter to a hardware address nobody owns, so nothing the laptop sends it
(internet replies through the VPN, DNS) arrives. Allowing removes the pin. Honest limits:
the device still sees the Wi-Fi and can talk for that first second; a device that
rejoins with a new random MAC shows up as a new request; the enforcement runs while
WireSpot (app or CLI) runs. Turning approval on while live trusts the devices that are
already connected. The pins are removed when the hotspot stops.

## Uninstall

**Settings → Uninstall WireSpot…** (or *Apps & features*) first stops WireSpot's own
tunnel, hotspot and DNS lock, then removes the program, shortcuts, the logon task and
`%ProgramData%\WireSpot`. You choose: **keep your settings and VPN profiles**
(`%APPDATA%\WireSpot` stays) or **delete everything**. A portable copy is never
uninstalled; delete its folder.

## Commands

| Everyday | |
|---|---|
| `start [n] [--band 5] [--vpn-only] [--balanced\|--strict] [--yes]` | full pipeline with verification and rollback |
| `stop` | removes everything WireSpot set up |
| `status` | VPN, hotspot, devices and exit IP |
| `clients` | who is connected: IP, name, device type guess, MAC, vendor |

Profiles: `profiles`, `profile use|info <n>`, `profile <n>`, `use <n>`, `import <path>`, `inbox`
VPN: `vpn status|connect|disconnect`, `connect`, `disconnect`
Hotspot: `hotspot start|stop|status`, `bind` (restart a Settings-started hotspot so it shares the VPN)
Diagnostics: `doctor [quick|os|wifi|vpn|hotspot|ics|network|clients|full] [save]`, `network status|adapters`, `ics`, `ip`, `logs`
Settings: `set …`, `settings`, `show password`, `protection`, `debug on|off`, `autostart on|off|status`
Devices: `waiting`, `allow [n|mac]`, `block [n|mac]`, `set approval on|off`
App: `app` (alias `tray`) opens the desktop app

The CLI shell looks and behaves like Claude Code: an input box with a **live autocomplete
menu**. Start typing and matching commands, profiles, settings and values appear with
descriptions. ↑/↓ moves through the menu (or history when it is closed), Tab or →
accepts, and Enter runs the line. On a partial word, Enter fills in the suggestion
first, so `sto` + Enter never silently runs `stop`. Esc closes the menu. History is kept
without the password. It uses only Claude's palette: clay #D97757, shimmer #EB9F7F, crail #C15F3C, cream #FAF9F5, and warm greys. There is no green or blue; status is told apart by shape (● ok, ▲ warning, ✖ error). It has a shimmering ✻ spinner and ⎿
connectors. On the legacy console (Consolas font) it switches to glyphs that font
has (● · └ ┌ ›), so nothing renders as a box. When a step fails, WireSpot shows a
**recommended recovery** and lets you pick it or stop.

## Icon

`assets/wirespot.svg` is the vector master: a single lightning bolt, in the
Claude palette. `assets/wirespot.ico` is generated from the same geometry (`python -m
wirespot.icon out.ico out.svg`); the tray recolours it per state (live, VPN only, idle,
busy, paused, error).

## Tray

The WireSpot icon sits next to the clock and shows the state: clay = live, clay mark on
dark = VPN only, grey = idle, pause bars = paused, crail "!" = needs attention. Clicking
it opens the **same panel as the window** (not the stock Windows menu). It stays open
while you use it and closes only when you click somewhere else or press Esc, and it
updates in place, so it never blinks.

Toasts in the same design announce devices joining, waiting for approval (with Allow /
Block buttons) or leaving, the guard stopping the hotspot, and the result of every action,
including actions run in the CLI.

**Start with Windows** registers a Task Scheduler logon task with highest privileges
(`\WireSpot\WireSpot Tray`). WireSpot starts elevated in the tray at logon **without
a UAC prompt**. It keeps running on battery and is never time-limited.

## CLI, app and tray stay in sync

The app (window + tray) is one process, the CLI another. They share the ownership
record, settings, one operation lock, and (`wirespot/sync.py`):

- **what is running right now**: start something in the CLI and the app shows
  "Going live (from the CLI)" within half a second, and refuses a conflicting action with
  that reason instead of a lock timeout; and the other way round;
- **the result**: each side gets a toast or notice ("WireSpot app: Disconnecting") and
  refreshes at once (it watches the shared files; no 15-second wait);
- **the pause timer**: `status` in the CLI shows "paused, resumes in 12m"; an explicit
  start or stop anywhere cancels it;
- **one activity journal** (redacted): the app's Activity page shows what the CLI did;
- **the guard and device approval**: whichever process is running keeps them armed, and
  the other takes over if it exits.

## Ethernet, Wi-Fi or USB uplink

The uplink is found from the default route, not assumed to be Wi-Fi. `start` reports
it, for example `Uplink Ethernet 'Ethernet' (Realtek PCIe GbE) · gateway 192.168.0.1`.
With a cable, the Wi-Fi radio is free, so the hotspot can use any band the adapter
supports. The hotspot is still sourced from the WireGuard tunnel, so this works the
same over Ethernet, Wi-Fi or USB tethering. The Wi-Fi *radio* must be on (it hosts the
hotspot). With no uplink at all, `start` stops before touching anything.

## How `start` works

States: `DISCONNECTED → VPN_CONNECTING → VPN_CONNECTED → HOTSPOT_STARTING →
HOTSPOT_ACTIVE → SHARING_CONFIGURING → READY` (plus `ERROR`, `STOPPING`).

1. Preflight: admin, WireGuard, settings, profile (strict validation), Wi-Fi radio and
   uplink, tethering capability, band and security support.
2. WireGuard: a runtime copy of the config goes to `%ProgramData%\WireSpot\runtime`,
   readable only by SYSTEM and Administrators, and the tunnel service is installed.
3. Verify tunnel: adapter up (found by name, then tracked by GUID), handshake, and
   internet routes resolving to the tunnel.
4. Hotspot: tethering is started **from the WireGuard connection profile**, so Windows
   NATs hotspot clients into the tunnel. `StartTetheringAsync` counts as success only
   if Status = `Success` **and** `TetheringOperationalState` reaches `On` **and** the
   source Windows reports is the tunnel. Anything else is stopped. There is no
   "share Wi-Fi" fallback.
5. Identify the hotspot interface: a *Microsoft Wi-Fi Direct Virtual Adapter* that is
   Up and carries 192.168.137.1. It is never matched by name.
6. Routing: WireSpot confirms the tethering source is the tunnel and checks classic ICS
   for a conflicting path (see below).
7. Verify: hotspot still On, client gateway, DNS lock, and routes.

If a step fails after the VPN is verified, WireSpot rolls back the hotspot and DNS
lock, keeps the VPN up, and says so. If it fails earlier, everything is rolled back.
While READY, a guard checks every 20 s. If the tunnel drops or a classic-ICS path into
the hotspot appears, the guard stops the hotspot (fail closed). It also announces
devices joining and leaving.

## Protection modes

- **strict** keeps `AllowedIPs = 0.0.0.0/0`. WireGuard for Windows then enables its
  kill-switch firewall
  ([rules.go](https://github.com/WireGuard/wireguard-windows/blob/master/tunnel/firewall/rules.go)).
  That firewall blocks all local inbound traffic on non-tunnel interfaces except DHCP
  *client* replies. This includes the hotspot's DHCP *server* (UDP 67) and ICS DNS
  proxy (53), so **phones join but never get an IP address**. It suits VPN-only use.
  `start` explains this and offers balanced for the session.
- **balanced** rewrites `/0` to `0.0.0.0/1 + 128.0.0.0/1` and `::/1 + 8000::/1` in the
  runtime copy only. It gives full VPN routing, and hotspot DHCP and DNS work. The DNS
  lock (an NRPT rule tagged `WireSpot-owned`) sends every lookup to the tunnel DNS,
  including the hotspot's DNS proxy that the phones use. New installs default to balanced.

## Sharing: Mobile Hotspot NAT, not classic ICS, not WinNAT

This was measured on the reporting machine. While Windows Mobile Hotspot is running and
sharing, **no connection carries the classic ICS public/private flags**
(`root\Microsoft\HomeNet` shows none before, during, or after). Mobile Hotspot's own
service, `icssvc`, runs NAT, DHCP and the DNS proxy (192.168.137.1) for its clients,
sharing whichever connection the hotspot was started from.

- WireSpot therefore starts the hotspot from the **WireGuard connection** and does not
  touch ICS at all.
- WireSpot 0.2.0 still forced classic ICS onto the Wi-Fi Direct adapter after the
  hotspot started. That fights `icssvc`, and Windows rejects it with
  **`0x80040201` ("An event was unable to invoke any of the subscribers")**. That was
  the failure in the first 0.2.0 run.
- Classic ICS is now only *watched*. If other software shares a non-VPN connection into
  the hotspot adapter, that is a second path around the VPN. WireSpot reports it and
  offers to turn it off (or stops). `doctor ics` shows all of this without admin.
- WinNAT (`New-NetNat`) has no DHCP or DNS for the phones and is meant for
  Hyper-V/containers, so it is not used.

## WiFiDeviceOff

`TetheringOperationStatus.WiFiDeviceOff` means Windows could not bring up the Wi-Fi
*access-point role* (the Wi-Fi Direct virtual adapter). It does not mean your Wi-Fi
connection is off. Known causes:

- the radio is switched off
- the Wi-Fi Direct adapter is disabled
- the driver refuses a band next to the current uplink (Intel documents regional 5 GHz limits)
- a transient state during a network change

On an Intel AX201 a 5 GHz hotspot did start while the uplink was on 2.4 GHz, so a
band mismatch is only a hint. When the error happens, WireSpot prints the radio,
uplink and Wi-Fi Direct adapter state at that moment and offers `band auto`.

## Security

- Private keys are never printed, logged or sent anywhere. The profile review card shows
  only that the key is valid and the public key derived from it.
- Imported configs are untrusted. They are size-limited and must be UTF-8. Only
  standard WireGuard keys are accepted. `PreUp`/`PostUp`/`PreDown`/`PostDown` are
  rejected. The runtime copy is rebuilt from the parsed model without comments.
- Runtime copies are restricted to SYSTEM and Administrators and deleted on `stop`.
  Copies left behind by ProtonRelay 0.1 (which were readable by all users) are
  locked down or removed at startup.
- The hotspot password is passed to PowerShell through an environment variable, never
  on the command line. It is masked in `settings`, redacted from logs and kept out of
  history.
- `settings.json` stores the hotspot password in plain text in your data folder, so use a
  unique one.

## Files

```
Installed (WireSpotSetup.exe)
  %LOCALAPPDATA%\Programs\WireSpot\   WireSpot.exe (app: window + tray), WireSpotCLI.exe, install.json
  %APPDATA%\WireSpot\                  settings.json, vpn\*.conf (your profiles), logs\
  Desktop\WireSpot.lnk                  the app
Portable (WireSpot.exe next to settings.json or vpn\)
  <folder>\                            the same data files, next to the exe
Both
  %ProgramData%\WireSpot\              runtime configs (admin-only), state.json (ownership record),
                                        devices.json (approved/blocked), pause.json, bus.json + activity.log (sync), icons
```

## Build

`build.bat` runs the unit tests, draws the icon and exports the SVG icon set, builds
`WireSpot.exe` (app), `WireSpotCLI.exe` (CLI) and `WireSpotSetup.exe` (the installer,
with both inside) with PyInstaller, then puts the installer in `release\` and refreshes
`portable\` (your `portable\settings.json` and `portable\vpn\` are kept).

Tests: `py -3 -m unittest discover -s tests -t .`

To build from source, install Python 3.11+ and run `py -3 -m pip install -r requirements.txt`, then `build.bat` on Windows. It runs the tests and bundles the app, CLI, and installer with PyInstaller. The source requires WireGuard for Windows and a Wi-Fi adapter that supports Mobile Hotspot for live use.

## License and privacy

WireSpot is licensed under **GPL-3.0-only**. See [LICENSE](LICENSE) for the full license, [PRIVACY.md](PRIVACY.md) for local and website data handling, and [TERMS.md](TERMS.md) for website and download terms. The Terms do not limit GPL rights.

Unofficial; not affiliated with Proton AG, WireGuard LLC, Jason A. Donenfeld, or Microsoft.
