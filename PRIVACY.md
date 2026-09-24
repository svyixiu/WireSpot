# WireSpot Privacy Policy

Effective 2026-09-24 · Version 1

WireSpot is a local Windows application. It has no accounts, telemetry, analytics, or remote crash reporting. WireSpot does not upload your VPN configuration or device list.

## Data on your PC

WireSpot stores settings, VPN profiles, and logs in `%APPDATA%\WireSpot`. `settings.json` contains your hotspot password in plain text, and `vpn\*.conf` files contain private VPN keys. Protect access to your Windows account and use a unique hotspot password. A portable copy stores these files beside the executable.

WireSpot also uses `%ProgramData%\WireSpot` for runtime tunnel configurations with administrator-only access, state, an activity journal, and `devices.json`. The device file can contain MAC addresses and names of hotspot devices you allowed or blocked. Logs and local crash logs are redacted to remove keys and the hotspot password. Uninstall offers to keep your settings and profiles or delete WireSpot's local data.

## Network connections

The only internet request made by WireSpot itself is an optional exit-IP check to `https://api.ipify.org` or `https://api64.ipify.org`. When connected, that request uses the VPN tunnel, so ipify sees the VPN exit IP. Checks involving `1.1.1.1` and `9.9.9.9` inspect your PC's local routing table; they do not contact those addresses.

Your traffic goes to the VPN provider you chose. That provider's privacy policy applies. WireSpot does not send your `.conf` files to the provider or to this website.

## Website and downloads

This website is static. WireSpot does not put cookies or analytics on it. Vercel hosts the site and may process request logs, including IP address and browser user agent, under [Vercel's Privacy Notice](https://vercel.com/legal/privacy-notice). Installer downloads are served by GitHub, whose [Privacy Statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement) applies.

## Contact and changes

For privacy questions, open an issue at https://github.com/svyixiu/WireSpot/issues. Do not post VPN keys, passwords, or private configuration files in an issue. Material changes will appear in this document with a new effective date and version.
