"""Wi-Fi scan results and ifupdown configuration rendered from network.json."""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
import time
from pathlib import Path


class NetworkError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CommandResult:
    def __init__(self, returncode: int, stdout: str, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def iface() -> str:
    return os.environ.get("KIOSK_IFACE", "wlan0")


def wpa_path() -> Path:
    return Path(os.environ.get("KIOSK_WPA_CONF", "/tmp/wpa_supplicant.conf"))


def inet_path() -> Path:
    return Path(os.environ.get("KIOSK_INET_CONF", "/tmp/wlan0-inet"))


def resolv_path() -> Path:
    return Path(os.environ.get("KIOSK_RESOLV_CONF", "/etc/resolv.conf"))


def run(args: list[str], timeout: float = 30) -> CommandResult:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return CommandResult(127, "", f"missing {args[0]}")
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", "timeout")
    return CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def wifi_country() -> str:
    path = Path(os.environ.get("KIOSK_WIFI_INI", "/boot/firmware/kioskbrowser.ini"))
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return "IT"
    section = ""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(";") or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section != "wifi" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().lower() != "country":
            continue
        country = value.strip().strip('"').strip("'")
        return country or "IT"
    return "IT"


def wpa_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def wpa_conf(config: dict, country: str) -> str:
    password = config.get("password") or ""
    lines = []
    if country:
        lines.append(f"country={country}")
    lines.extend(
        [
            "ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev",
            "update_config=0",
            "network={",
            f"\tssid={wpa_quote(config['ssid'])}",
        ]
    )
    if password:
        lines.extend(
            [
                f"\tpsk={wpa_quote(password)}",
                "\tkey_mgmt=WPA-PSK SAE",
                "\tieee80211w=1",
            ]
        )
    else:
        lines.append("\tkey_mgmt=NONE")
    lines.extend(["\tpriority=10", "}", ""])
    return "\n".join(lines)


def inet_stanza(ipv4: dict, interface: str | None = None) -> str:
    name = interface or iface()
    lines = [f"auto {name}", f"allow-hotplug {name}"]
    if ipv4.get("mode") == "static":
        lines.extend(
            [
                f"iface {name} inet static",
                f"\taddress {ipv4['address']}",
                f"\tnetmask {ipv4['netmask']}",
                f"\tgateway {ipv4['gateway']}",
                "\twpa-conf /etc/wpa_supplicant/wpa_supplicant.conf",
            ]
        )
    else:
        lines.extend(
            [
                f"iface {name} inet dhcp",
                "\twpa-conf /etc/wpa_supplicant/wpa_supplicant.conf",
            ]
        )
    lines.extend(["", f"iface {name} inet6 auto", ""])
    return "\n".join(lines)


def resolv_text(dns: str) -> str:
    return f"nameserver {dns}\n"


def netmask_to_prefix(mask: str) -> int:
    try:
        packed = int(ipaddress.IPv4Address(mask))
    except ipaddress.AddressValueError as exc:
        raise ValueError("netmask is not an IPv4 address") from exc
    inverted = (~packed) & 0xFFFFFFFF
    if packed == 0 or (inverted & (inverted + 1)) != 0:
        raise ValueError("netmask is not contiguous")
    return bin(packed).count("1")


def prefix_to_netmask(prefix: int) -> str:
    return str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)


def _ipv4(value: str, label: str) -> ipaddress.IPv4Address:
    try:
        return ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValueError(f"{label} is not an IPv4 address") from exc


def validate_static(ipv4: dict) -> None:
    address = _ipv4(str(ipv4.get("address", "")), "address")
    gateway = _ipv4(str(ipv4.get("gateway", "")), "gateway")
    _ipv4(str(ipv4.get("dns", "")), "dns")
    prefix = netmask_to_prefix(str(ipv4.get("netmask", "")))
    if address.is_unspecified or address.is_multicast or address.is_loopback:
        raise ValueError("address is not usable")
    if gateway == address:
        raise ValueError("gateway must differ from the address")
    network = ipaddress.IPv4Network(f"{address}/{prefix}", strict=False)
    if address == network.broadcast_address:
        raise ValueError("address is the subnet broadcast")
    if prefix <= 30 and gateway not in network:
        raise ValueError("gateway is outside the subnet")


def security_from_flags(flags: str) -> str:
    upper = flags.upper()
    has_psk = "PSK" in upper
    has_sae = "SAE" in upper
    if "WEP" in upper and not has_psk and not has_sae and "WPA" not in upper:
        return "wep"
    if has_sae and not has_psk:
        return "wpa3"
    if has_psk or "WPA" in upper:
        return "wpa-psk"
    return "open"


def parse_scan_results(text: str) -> list[dict]:
    best: dict[str, dict] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("bssid") or line.startswith("Selected"):
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        bssid, frequency, signal, flags, ssid = parts[0], parts[1], parts[2], parts[3], parts[4]
        ssid = ssid.strip()
        if not ssid:
            continue
        try:
            signal_dbm = int(signal)
            frequency_mhz = int(frequency)
        except ValueError:
            continue
        item = {
            "ssid": ssid,
            "bssid": bssid.lower(),
            "signal": signal_dbm,
            "frequency": frequency_mhz,
            "security": security_from_flags(flags),
        }
        current = best.get(ssid)
        if current is None or signal_dbm > current["signal"]:
            best[ssid] = item
    return sorted(best.values(), key=lambda item: item["signal"], reverse=True)


def empty_status() -> dict:
    return {
        "connected": False,
        "ssid": None,
        "address": None,
        "netmask": None,
        "gateway": None,
        "dns": [],
    }


def read_dns(path: Path | None = None) -> list[str]:
    target = path or resolv_path()
    try:
        lines = target.read_text(errors="replace").splitlines()
    except OSError:
        return []
    servers = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            servers.append(parts[1])
    return servers


def live_status(runner=run) -> dict:
    status = empty_status()
    wpa = runner(["wpa_cli", "-i", iface(), "status"])
    if wpa.returncode == 0:
        fields = {}
        for line in wpa.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                fields[key] = value
        status["connected"] = fields.get("wpa_state") == "COMPLETED"
        status["ssid"] = fields.get("ssid") or None
    address = runner(["ip", "-4", "-o", "addr", "show", "dev", iface()])
    if address.returncode == 0:
        match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", address.stdout)
        if match:
            status["address"] = match.group(1)
            status["netmask"] = prefix_to_netmask(int(match.group(2)))
    route = runner(["ip", "-4", "route", "show", "default"])
    if route.returncode == 0:
        name = iface()
        for line in route.stdout.splitlines():
            parts = line.split()
            if "dev" not in parts:
                continue
            dev_index = parts.index("dev") + 1
            if dev_index >= len(parts) or parts[dev_index] != name:
                continue
            match = re.search(r"\bvia (\d+\.\d+\.\d+\.\d+)\b", line)
            if match:
                status["gateway"] = match.group(1)
                break
    status["dns"] = read_dns()
    return status


def scan_wifi(runner=run, sleep=time.sleep) -> list[dict]:
    link = runner(["ip", "link", "show", "dev", iface()])
    if link.returncode != 0:
        raise NetworkError("WIFI_UNAVAILABLE", f"{iface()} is not available")
    started = runner(["wpa_cli", "-i", iface(), "scan"])
    if started.returncode != 0 or "OK" not in started.stdout:
        raise NetworkError("WIFI_SCAN_FAILED", "wpa_cli scan failed")
    attempts = int(os.environ.get("KIOSK_SCAN_ATTEMPTS", "16"))
    latest = ""
    for _ in range(max(attempts, 1)):
        sleep(0.25)
        current = runner(["wpa_cli", "-i", iface(), "scan_results"])
        if current.returncode != 0:
            raise NetworkError("WIFI_SCAN_FAILED", "wpa_cli scan_results failed")
        latest = current.stdout
        if parse_scan_results(latest):
            break
    return parse_scan_results(latest)


def write_runtime_files(config: dict) -> None:
    country = wifi_country()
    wpa = wpa_path()
    inet = inet_path()
    wpa.parent.mkdir(parents=True, exist_ok=True)
    inet.parent.mkdir(parents=True, exist_ok=True)
    wpa.write_text(wpa_conf(config, country))
    inet.write_text(inet_stanza(config.get("ipv4") or {"mode": "dhcp"}))
    ipv4 = config.get("ipv4") or {}
    if ipv4.get("mode") == "static" and ipv4.get("dns"):
        target = resolv_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(resolv_text(ipv4["dns"]))


def _addressing_ok(config: dict, status: dict) -> bool:
    ipv4 = config.get("ipv4") or {}
    if ipv4.get("mode") == "static":
        return status.get("address") == ipv4.get("address")
    return bool(status.get("address"))


def poll_status(config: dict, runner, sleep) -> dict:
    timeout = float(os.environ.get("KIOSK_APPLY_TIMEOUT", "20"))
    deadline = time.monotonic() + timeout
    status = empty_status()
    while True:
        status = live_status(runner)
        if status["connected"] and _addressing_ok(config, status):
            return status
        if time.monotonic() >= deadline:
            return status
        sleep(0.5)


def apply_saved(config: dict, *, mode: str = "now", runner=run, sleep=time.sleep) -> dict:
    write_runtime_files(config)
    country = wifi_country()
    runner(["rfkill", "unblock", "all"])
    runner(["iw", iface(), "set", "power_save", "off"])
    if country:
        runner(["iw", "reg", "set", country])
    if mode == "boot":
        return {
            "associated": False,
            "addressing": False,
            "status": empty_status(),
            "detail": None,
        }
    link = runner(["ip", "link", "show", "dev", iface()])
    if link.returncode != 0:
        return {
            "associated": False,
            "addressing": False,
            "status": empty_status(),
            "detail": f"{iface()} is not available",
        }
    runner(["ifdown", iface()])
    brought_up = runner(["ifup", iface()])
    status = poll_status(config, runner, sleep)
    associated = bool(status["connected"])
    addressing = _addressing_ok(config, status)
    detail = None
    if brought_up.returncode != 0:
        detail = (brought_up.stderr or brought_up.stdout or "ifup failed").strip()
    elif not associated:
        detail = "Wi-Fi did not associate"
    elif not addressing:
        detail = "Addressing did not apply"
    return {
        "associated": associated,
        "addressing": addressing,
        "status": status,
        "detail": detail,
    }
