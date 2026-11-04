"""Boot-time entry point. `kiosk-wifi` runs `python -m kiosk_settings.apply boot`."""

from __future__ import annotations

import sys

from kiosk_settings.network import apply_saved
from kiosk_settings.store import read_json


def usable(config: dict | None) -> bool:
    if not config or not isinstance(config.get("ssid"), str) or not config["ssid"]:
        return False
    ipv4 = config.get("ipv4")
    if not isinstance(ipv4, dict):
        return False
    mode = ipv4.get("mode")
    if mode == "dhcp":
        return True
    if mode != "static":
        return False
    return all(isinstance(ipv4.get(key), str) and ipv4[key] for key in ("address", "netmask", "gateway", "dns"))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode = args[0] if args else "boot"
    if mode not in {"boot", "now"}:
        print("usage: python -m kiosk_settings.apply {boot|now}", file=sys.stderr)
        return 2
    saved = read_json("network.json")
    if not usable(saved):
        print(
            "kiosk-settings: network.json is missing or invalid",
            file=sys.stderr,
        )
        return 1
    if "password" not in saved:
        saved["password"] = ""
    try:
        apply_saved(saved, mode=mode)
    except OSError as exc:
        print(f"kiosk-settings: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
