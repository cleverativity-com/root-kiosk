#!/usr/bin/env python3
"""Contract tests for the kiosk settings API and its network rendering."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "kiosk_skeleton" / "opt" / "kiosk-settings"
STATE = Path(tempfile.mkdtemp(prefix="kiosk-settings-"))

os.environ["KIOSK_STATE_DIR"] = str(STATE)
os.environ["KIOSK_REMOUNT"] = "0"
os.environ["KIOSK_STATE_LOCK"] = str(STATE / "state.lock")
os.environ["KIOSK_WPA_CONF"] = str(STATE / "wpa.conf")
os.environ["KIOSK_INET_CONF"] = str(STATE / "wlan0-inet")
os.environ["KIOSK_RESOLV_CONF"] = str(STATE / "resolv.conf")
os.environ["KIOSK_WIFI_INI"] = str(ROOT / "kiosk_skeleton" / "boot" / "firmware" / "kioskbrowser.ini")
os.environ["KIOSK_APPLY_TIMEOUT"] = "0"
os.environ["KIOSK_SCAN_ATTEMPTS"] = "2"
sys.path.insert(0, str(PACKAGE))

from fastapi.testclient import TestClient  # noqa: E402

from kiosk_settings.apply import main as apply_main  # noqa: E402
from kiosk_settings.main import app  # noqa: E402
from kiosk_settings.network import (  # noqa: E402
    CommandResult,
    NetworkError,
    apply_saved,
    inet_stanza,
    netmask_to_prefix,
    parse_scan_results,
    prefix_to_netmask,
    scan_wifi,
    security_from_flags,
    validate_static,
    wpa_conf,
)
from kiosk_settings.store import StateError, delete_json, read_json, write_json  # noqa: E402

SCAN_TEXT = "\n".join(
    [
        "bssid / frequency / signal level / flags / ssid",
        "aa:bb:cc:dd:ee:01\t2437\t-80\t[WPA2-PSK-CCMP][ESS]\tOffice",
        "aa:bb:cc:dd:ee:02\t5180\t-40\t[WPA2-PSK-CCMP][ESS]\tOffice",
        "aa:bb:cc:dd:ee:03\t2412\t-55\t[RSN-SAE-CCMP][ESS]\tCafe",
        "aa:bb:cc:dd:ee:04\t2412\t-60\t[ESS]\tGuest",
        "aa:bb:cc:dd:ee:05\t2412\t-70\t[WEP][ESS]\tOld",
        "aa:bb:cc:dd:ee:06\t2412\t-30\t[ESS]\t",
    ]
)


def _connected_runner(calls: list[list[str]]):
    def runner(args: list[str]) -> CommandResult:
        calls.append(list(args))
        if args[:2] == ["wpa_cli", "-i"] and args[-1] == "status":
            return CommandResult(0, "bssid=aa:bb:cc:dd:ee:02\nssid=Office\nwpa_state=COMPLETED\n")
        if args[:3] == ["ip", "-4", "-o"]:
            return CommandResult(0, "2: wlan0    inet 192.168.1.50/24 brd 192.168.1.255 scope global wlan0\n")
        if args[:4] == ["ip", "-4", "route", "show"]:
            return CommandResult(
                0,
                "default via 10.0.0.1 dev eth0\ndefault via 192.168.1.1 dev wlan0\n",
            )
        if args[:2] == ["ip", "link"]:
            return CommandResult(0, "2: wlan0: <BROADCAST,UP>\n")
        if args[0] == "ifup":
            return CommandResult(0, "", "")
        return CommandResult(0, "", "")

    return runner


class KioskSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["KIOSK_REMOUNT"] = "0"
        os.environ["KIOSK_STATE_DIR"] = str(STATE)
        for name in ("network.json", "dispenser.json"):
            path = STATE / name
            if path.exists():
                path.unlink()
        self.client = TestClient(app)

    def test_store_round_trip_without_remount(self) -> None:
        write_json("dispenser.json", {"dispenserId": "D-1"})
        self.assertEqual(read_json("dispenser.json"), {"dispenserId": "D-1"})
        delete_json("dispenser.json")
        self.assertIsNone(read_json("dispenser.json"))
        delete_json("dispenser.json")

    def test_store_ignores_invalid_json(self) -> None:
        (STATE / "network.json").write_text("{")
        self.assertIsNone(read_json("network.json"))

    def test_write_remounts_boot_partition(self) -> None:
        os.environ["KIOSK_REMOUNT"] = "1"
        try:
            with patch("kiosk_settings.store.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                write_json("dispenser.json", {"dispenserId": "A"})
            modes = [call.args[0][2] for call in run.call_args_list]
            self.assertEqual(modes, ["remount,rw", "remount,ro"])
            self.assertEqual(run.call_args_list[0].args[0][3], "/boot/firmware")
        finally:
            os.environ["KIOSK_REMOUNT"] = "0"

    def test_write_failure_still_remounts_readonly(self) -> None:
        blocked = STATE / "blocked"
        blocked.write_text("not a directory")
        os.environ["KIOSK_STATE_DIR"] = str(blocked)
        os.environ["KIOSK_REMOUNT"] = "1"
        try:
            with patch("kiosk_settings.store.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                with self.assertRaises(StateError):
                    write_json("dispenser.json", {"dispenserId": "A"})
            modes = [call.args[0][2] for call in run.call_args_list]
            self.assertEqual(modes[0], "remount,rw")
            self.assertIn("remount,ro", modes)
        finally:
            os.environ["KIOSK_REMOUNT"] = "0"
            os.environ["KIOSK_STATE_DIR"] = str(STATE)

    def test_netmask_and_static_validation(self) -> None:
        self.assertEqual(netmask_to_prefix("255.255.255.0"), 24)
        self.assertEqual(prefix_to_netmask(24), "255.255.255.0")
        with self.assertRaises(ValueError):
            netmask_to_prefix("255.0.255.0")
        validate_static(
            {
                "address": "192.168.1.50",
                "netmask": "255.255.255.0",
                "gateway": "192.168.1.1",
                "dns": "8.8.8.8",
            }
        )
        with self.assertRaises(ValueError):
            validate_static(
                {
                    "address": "192.168.1.50",
                    "netmask": "255.255.255.0",
                    "gateway": "10.0.0.1",
                    "dns": "8.8.8.8",
                }
            )

    def test_security_and_scan_parsing(self) -> None:
        self.assertEqual(security_from_flags("[WPA2-PSK-CCMP][ESS]"), "wpa-psk")
        self.assertEqual(security_from_flags("[RSN-SAE-CCMP][ESS]"), "wpa3")
        self.assertEqual(security_from_flags("[ESS]"), "open")
        found = {item["ssid"]: item for item in parse_scan_results(SCAN_TEXT)}
        self.assertEqual(set(found), {"Office", "Cafe", "Guest", "Old"})
        self.assertEqual(found["Office"]["signal"], -40)
        self.assertEqual(found["Office"]["bssid"], "aa:bb:cc:dd:ee:02")
        self.assertEqual(found["Cafe"]["security"], "wpa3")
        self.assertEqual(found["Guest"]["security"], "open")
        self.assertEqual(found["Old"]["security"], "wep")

    def test_scan_wifi_polls_until_results(self) -> None:
        replies = {"n": 0}

        def runner(args: list[str]) -> CommandResult:
            if args[0] == "ip":
                return CommandResult(0, "2: wlan0: <BROADCAST,UP>\n")
            if args[-1] == "scan":
                return CommandResult(0, "OK\n")
            replies["n"] += 1
            if replies["n"] == 1:
                return CommandResult(0, "bssid / frequency / signal level / flags / ssid\n")
            return CommandResult(0, SCAN_TEXT)

        found = scan_wifi(runner, sleep=lambda _seconds: None)
        self.assertEqual(replies["n"], 2)
        self.assertEqual(found[0]["ssid"], "Office")

    def test_scan_wifi_reports_missing_interface(self) -> None:
        with self.assertRaises(NetworkError) as caught:
            scan_wifi(lambda _args: CommandResult(1, "", "no such device"), sleep=lambda _seconds: None)
        self.assertEqual(caught.exception.code, "WIFI_UNAVAILABLE")

    def test_wpa_and_inet_rendering(self) -> None:
        secured = wpa_conf({"ssid": 'Say "hi"', "password": 'secret"\\x'}, "IT")
        self.assertIn('ssid="Say \\"hi\\""', secured)
        self.assertIn('psk="secret\\"\\\\x"', secured)
        self.assertIn("key_mgmt=WPA-PSK SAE", secured)
        self.assertIn("ieee80211w=1", secured)
        self.assertIn("country=IT", secured)
        opened = wpa_conf({"ssid": "Guest", "password": ""}, "IT")
        self.assertIn("key_mgmt=NONE", opened)
        self.assertNotIn("psk=", opened)
        dhcp = inet_stanza({"mode": "dhcp"})
        self.assertIn("iface wlan0 inet dhcp", dhcp)
        self.assertNotIn("iface wlan0 inet static", dhcp)
        static = inet_stanza(
            {
                "mode": "static",
                "address": "192.168.1.50",
                "netmask": "255.255.255.0",
                "gateway": "192.168.1.1",
            }
        )
        self.assertIn("iface wlan0 inet static", static)
        self.assertIn("\tnetmask 255.255.255.0", static)
        self.assertIn("\tgateway 192.168.1.1", static)
        shell = (ROOT / "kiosk_skeleton" / "usr" / "bin" / "kiosk-wifi").read_text()
        init = (ROOT / "kiosk_skeleton" / "usr" / "bin" / "kiosk-init").read_text()
        self.assertIn(dhcp, shell)
        self.assertIn(dhcp, init)
        interfaces = (ROOT / "kiosk_skeleton" / "etc" / "network" / "interfaces").read_text()
        self.assertIn("source /tmp/wlan0-inet", interfaces)
        self.assertNotIn("iface wlan0 inet dhcp", interfaces)

    def test_apply_saved_joins_and_keeps_wlan_gateway(self) -> None:
        calls: list[list[str]] = []
        config = {
            "ssid": "Office",
            "password": "correct horse",
            "ipv4": {
                "mode": "static",
                "address": "192.168.1.50",
                "netmask": "255.255.255.0",
                "gateway": "192.168.1.1",
                "dns": "1.1.1.1",
            },
        }
        result = apply_saved(config, mode="now", runner=_connected_runner(calls), sleep=lambda _seconds: None)
        self.assertTrue(result["associated"])
        self.assertTrue(result["addressing"])
        self.assertIsNone(result["detail"])
        self.assertEqual(result["status"]["gateway"], "192.168.1.1")
        self.assertEqual(result["status"]["netmask"], "255.255.255.0")
        self.assertEqual(result["status"]["dns"], ["1.1.1.1"])
        self.assertIn(["ifdown", "wlan0"], calls)
        self.assertIn(["ifup", "wlan0"], calls)
        self.assertIn("nameserver 1.1.1.1", (STATE / "resolv.conf").read_text())

    def test_apply_boot_does_not_ifup(self) -> None:
        calls: list[list[str]] = []
        config = {"ssid": "Office", "password": "", "ipv4": {"mode": "dhcp"}}
        result = apply_saved(config, mode="boot", runner=_connected_runner(calls), sleep=lambda _seconds: None)
        self.assertFalse(any(command[0] == "ifup" for command in calls))
        self.assertFalse(result["associated"])
        self.assertIn("key_mgmt=NONE", (STATE / "wpa.conf").read_text())
        self.assertIn("iface wlan0 inet dhcp", (STATE / "wlan0-inet").read_text())

    def test_apply_reports_ifup_failure_without_dropping_config(self) -> None:
        def runner(args: list[str]) -> CommandResult:
            if args[0] == "ifup":
                return CommandResult(1, "", "no dhcp leases")
            if args[:2] == ["ip", "link"]:
                return CommandResult(0, "up\n")
            if args[:2] == ["wpa_cli", "-i"] and args[-1] == "status":
                return CommandResult(0, "wpa_state=SCANNING\n")
            return CommandResult(0, "", "")

        result = apply_saved(
            {"ssid": "Office", "password": "correct horse", "ipv4": {"mode": "dhcp"}},
            mode="now",
            runner=runner,
            sleep=lambda _seconds: None,
        )
        self.assertFalse(result["associated"])
        self.assertFalse(result["addressing"])
        self.assertIn("no dhcp leases", result["detail"])

    def test_apply_cli_boot_and_invalid_file(self) -> None:
        self.assertEqual(apply_main(["boot"]), 1)
        (STATE / "network.json").write_text("{")
        self.assertEqual(apply_main(["boot"]), 1)
        write_json(
            "network.json",
            {"ssid": "Office", "password": "correct horse", "ipv4": {"mode": "dhcp"}},
        )
        with patch("kiosk_settings.apply.apply_saved", return_value={}) as apply:
            self.assertEqual(apply_main(["boot"]), 0)
        self.assertEqual(apply.call_args.kwargs["mode"], "boot")
        self.assertEqual(apply_main(["sideways"]), 2)

    def test_dispenser_round_trip(self) -> None:
        missing = self.client.get("/api/v1/dispenser")
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(missing.json(), {"configured": False})

        saved = self.client.put("/api/v1/dispenser", json={"dispenserId": "D-1042", "name": "Roma Termini"})
        self.assertEqual(saved.status_code, 200)
        body = saved.json()
        self.assertTrue(body["configured"])
        self.assertEqual(body["dispenserId"], "D-1042")
        self.assertEqual(body["name"], "Roma Termini")
        self.assertRegex(body["updatedAt"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        again = self.client.get("/api/v1/dispenser").json()
        self.assertEqual(again["dispenserId"], "D-1042")

        cleared = self.client.delete("/api/v1/dispenser")
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.json(), {"configured": False})
        self.assertFalse((STATE / "dispenser.json").exists())

    def test_dispenser_rejects_slash_and_blank(self) -> None:
        slash = self.client.put("/api/v1/dispenser", json={"dispenserId": "a/b"})
        self.assertEqual(slash.status_code, 422)
        self.assertEqual(slash.json()["error"]["code"], "DISPENSER_INVALID")
        blank = self.client.put("/api/v1/dispenser", json={"dispenserId": "  "})
        self.assertEqual(blank.status_code, 422)
        self.assertFalse((STATE / "dispenser.json").exists())

    def test_network_put_redacts_password_and_keeps_it(self) -> None:
        status = {
            "connected": True,
            "ssid": "Office",
            "address": "192.168.1.20",
            "netmask": "255.255.255.0",
            "gateway": "192.168.1.1",
            "dns": ["8.8.8.8"],
        }
        with patch("kiosk_settings.main.apply_saved") as apply:
            apply.return_value = {
                "associated": True,
                "addressing": True,
                "status": status,
                "detail": None,
            }
            created = self.client.put(
                "/api/v1/network",
                json={"ssid": "Office", "password": "correct horse", "ipv4": {"mode": "dhcp"}},
            )
            self.assertEqual(created.status_code, 200)
            self.assertNotIn("correct horse", created.text)
            self.assertTrue(created.json()["hasPassword"])
            self.assertTrue(created.json()["saved"])
            self.assertTrue(created.json()["associated"])
            self.assertEqual(created.json()["ipv4"]["mode"], "dhcp")
            stored = json.loads((STATE / "network.json").read_text())
            self.assertEqual(stored["password"], "correct horse")
            self.assertNotIn("address", stored["ipv4"])

            kept = self.client.put("/api/v1/network", json={"ssid": "Office", "ipv4": {"mode": "dhcp"}})
            self.assertEqual(kept.status_code, 200)
            self.assertEqual(json.loads((STATE / "network.json").read_text())["password"], "correct horse")

            opened = self.client.put(
                "/api/v1/network",
                json={"ssid": "Guest", "password": "", "ipv4": {"mode": "dhcp"}},
            )
            self.assertFalse(opened.json()["hasPassword"])
            self.assertEqual(json.loads((STATE / "network.json").read_text())["password"], "")

        current = self.client.get("/api/v1/network")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["ssid"], "Guest")
        self.assertNotIn("password", current.json())

    def test_network_static_validation_and_scan_errors(self) -> None:
        broken = self.client.put(
            "/api/v1/network",
            json={
                "ssid": "Office",
                "password": "correct horse",
                "ipv4": {
                    "mode": "static",
                    "address": "192.168.1.50",
                    "netmask": "255.0.255.0",
                    "gateway": "192.168.1.1",
                    "dns": "8.8.8.8",
                },
            },
        )
        self.assertEqual(broken.status_code, 422)
        self.assertEqual(broken.json()["error"]["code"], "NETWORK_INVALID")
        self.assertFalse((STATE / "network.json").exists())

        short = self.client.put(
            "/api/v1/network",
            json={"ssid": "Office", "password": "short", "ipv4": {"mode": "dhcp"}},
        )
        self.assertEqual(short.status_code, 422)
        outside = self.client.put(
            "/api/v1/network",
            json={
                "ssid": "Office",
                "password": "correct horse",
                "ipv4": {
                    "mode": "static",
                    "address": "192.168.1.50",
                    "netmask": "255.255.255.0",
                    "gateway": "10.0.0.1",
                    "dns": "8.8.8.8",
                },
            },
        )
        self.assertEqual(outside.status_code, 422)
        missing = self.client.put(
            "/api/v1/network",
            json={"ssid": "Office", "ipv4": {"mode": "static"}},
        )
        self.assertEqual(missing.status_code, 422)

        with patch(
            "kiosk_settings.main.scan_wifi",
            side_effect=NetworkError("WIFI_UNAVAILABLE", "wlan0 is not available"),
        ):
            scan = self.client.get("/api/v1/network/wifi")
        self.assertEqual(scan.status_code, 503)
        self.assertEqual(scan.json()["error"]["code"], "WIFI_UNAVAILABLE")

    def test_failed_join_keeps_the_saved_network(self) -> None:
        with patch("kiosk_settings.main.apply_saved") as apply:
            apply.return_value = {
                "associated": False,
                "addressing": False,
                "status": {
                    "connected": False,
                    "ssid": None,
                    "address": None,
                    "netmask": None,
                    "gateway": None,
                    "dns": [],
                },
                "detail": "Wi-Fi did not associate",
            }
            response = self.client.put(
                "/api/v1/network",
                json={
                    "ssid": "Office",
                    "password": "correct horse",
                    "ipv4": {
                        "mode": "static",
                        "address": "192.168.1.50",
                        "netmask": "255.255.255.0",
                        "gateway": "192.168.1.1",
                        "dns": "8.8.8.8",
                    },
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["saved"])
        self.assertFalse(response.json()["associated"])
        self.assertEqual(response.json()["detail"], "Wi-Fi did not associate")
        stored = json.loads((STATE / "network.json").read_text())
        self.assertEqual(stored["ipv4"]["address"], "192.168.1.50")
        self.assertEqual(stored["ipv4"]["dns"], "8.8.8.8")

    def test_openapi_lists_settings_paths(self) -> None:
        spec = self.client.get("/settings/openapi.json")
        self.assertEqual(spec.status_code, 200)
        paths = spec.json()["paths"]
        self.assertIn("/api/v1/network/wifi", paths)
        self.assertIn("/api/v1/network", paths)
        self.assertIn("put", paths["/api/v1/network"])
        self.assertIn("/api/v1/network/status", paths)
        self.assertIn("/api/v1/dispenser", paths)
        self.assertIn("delete", paths["/api/v1/dispenser"])
        self.assertEqual(spec.json()["info"]["title"], "Kiosk settings")
        nginx = (ROOT / "kiosk_skeleton" / "etc" / "nginx" / "sites-enabled" / "default").read_text()
        self.assertIn("proxy_pass http://127.0.0.1:8766;", nginx)
        self.assertIn("location /settings/docs", nginx)
        self.assertIn("location /settings/openapi.json", nginx)

    def test_shell_scripts_parse(self) -> None:
        scripts = [
            ["bash", "-n", str(ROOT / "kiosk_skeleton" / "usr" / "bin" / "kiosk-wifi")],
            ["bash", "-n", str(ROOT / "kiosk_skeleton" / "usr" / "bin" / "kiosk-settings-api")],
            ["sh", "-n", str(ROOT / "kiosk_skeleton" / "usr" / "bin" / "kiosk-init")],
        ]
        for command in scripts:
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
