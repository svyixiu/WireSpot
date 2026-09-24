"""WireSpot - WireGuard x Mobile Hotspot for Windows.

Turns a Windows laptop into a Wi-Fi router whose only way out is a WireGuard
(Proton VPN) tunnel. Formerly "ProtonRelay CLI".
"""

APP_NAME = "WireSpot"
TAGLINE = "WireGuard x Mobile Hotspot"
VERSION = "0.2.1"
WEBSITE = "https://wirespot.vercel.app"
TERMS_VERSION = "2"
PRIVACY_VERSION = "2"

# WireGuard tunnel names are limited to 32 chars of [A-Za-z0-9_=+.-].
TUNNEL_PREFIX = "ws_"
# Tunnels created by ProtonRelay 0.1.x - still recognised as ours for stop/cleanup.
LEGACY_TUNNEL_PREFIXES = ("pr_",)
OWNED_TUNNEL_PREFIXES = (TUNNEL_PREFIX, *LEGACY_TUNNEL_PREFIXES)

# Tag written into every Windows object WireSpot creates (NRPT rule comment…).
OWNER_TAG = "WireSpot-owned"
