# WireSpot Privacy Policy

Effective 2026-09-25 · Version 3

WireSpot is a local Windows application. It has no accounts, telemetry, analytics, or remote crash reporting. WireSpot does not upload your VPN configuration or device list.

## Data on your PC

WireSpot stores settings, VPN profiles, and logs in `%APPDATA%\WireSpot`. `settings.json` contains your hotspot password in plain text, and `vpn\*.conf` files contain private VPN keys. Protect access to your Windows account and use a unique hotspot password. A portable copy stores these files beside the executable.

WireSpot also uses `%ProgramData%\WireSpot` for runtime tunnel configurations with administrator-only access, state, an activity journal, and `devices.json`. The device file can contain MAC addresses and names of hotspot devices you allowed or blocked. Logs and local crash logs are redacted to remove keys and the hotspot password. Uninstall offers to keep your settings and profiles or delete WireSpot's local data.

## Network connections

While connected, WireSpot checks your exit IP with `https://api.ipify.org` or `https://api64.ipify.org`; ipify sees the VPN exit IP. In WireGuard profile mode, checks involving `1.1.1.1` and `9.9.9.9` inspect your PC's local routing table without contacting those addresses. In NordVPN Profile-less Mode, WireSpot also opens a short TCP connection to port 443 at those addresses through the NordVPN interface and queries `example.com` through DNS servers assigned to that interface. These checks validate the VPN path before hosting and during reconnection. The destination servers and your VPN provider may see connection metadata; no account credentials or VPN configuration are sent.

If you press **Check** on a profile, WireSpot resolves its VPN server address and sends two ICMP ping requests to it. A server may ignore ping, so a missing reply does not prove the profile is broken. If the profile is connected, WireSpot also reads the local WireGuard handshake time. If you press **Run speed test** while connected, WireSpot sends three tiny latency requests, downloads about 5 MB, and uploads 1 MB of generated zero bytes to `https://speed.cloudflare.com` over the laptop's current route without using an HTTP proxy. Cloudflare may see the IP used for that route (normally your VPN exit IP) and request metadata. No VPN key, configuration, device list, or file from your PC is uploaded. These tests do not run in the background.

Your traffic goes to the VPN provider you chose. That provider's privacy policy applies. WireSpot does not send your `.conf` files or NordVPN credentials to this website.

## Website and downloads

This website is static. WireSpot does not put cookies or analytics on it. Vercel hosts the site and may process request logs, including IP address and browser user agent, under [Vercel's Privacy Notice](https://vercel.com/legal/privacy-notice). Installer downloads are served by GitHub, whose [Privacy Statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement) applies.

## Contact and changes

For privacy questions, open an issue at https://github.com/svyixiu/WireSpot/issues. Do not post VPN keys, passwords, or private configuration files in an issue. Material changes will appear in this document with a new effective date and version.
