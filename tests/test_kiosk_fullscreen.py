#!/usr/bin/env python3
"""Chromium stays in kiosk fullscreen. The system keyboard stays off."""

from __future__ import annotations

import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTOSTART = ROOT / "kiosk_skeleton" / "home" / "pi" / ".config" / "openbox" / "autostart"
RC = ROOT / "kiosk_skeleton" / "home" / "pi" / ".config" / "openbox" / "rc.xml"
HOLD = ROOT / "kiosk_skeleton" / "usr" / "bin" / "kiosk-hold-fullscreen"
BUILD = ROOT / "kiosk_skeleton" / "build.sh"
INI = ROOT / "kiosk_skeleton" / "boot" / "firmware" / "kioskbrowser.ini"
OB_NS = {"o": "http://openbox.org/3.4/rc"}


class KioskFullscreenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.autostart = AUTOSTART.read_text(encoding="utf-8")
        self.build = BUILD.read_text(encoding="utf-8")
        self.hold = HOLD.read_text(encoding="utf-8")
        self.ini = INI.read_text(encoding="utf-8")

    def test_chromium_launches_in_kiosk_fullscreen(self) -> None:
        self.assertIn("--kiosk", self.autostart)
        self.assertIn("--start-fullscreen", self.autostart)
        self.assertIn("--ozone-platform=x11", self.autostart)
        self.assertIn("--window-position=0,0", self.autostart)
        self.assertIn(
            "--disable-features=Translate,TranslateUI,VirtualKeyboard",
            self.autostart,
        )
        self.assertEqual(self.autostart.count("--disable-features="), 1)
        self.assertIn('"${URL}"', self.autostart)
        self.assertNotIn("--app=", self.autostart)
        self.assertNotIn("--start-maximized", self.autostart)
        self.assertNotIn("--force-renderer-accessibility", self.autostart)
        self.assertIn("/usr/bin/kiosk-hold-fullscreen", self.autostart)

    def test_onscreen_keyboard_is_not_installed_or_started(self) -> None:
        self.assertNotIn("onboard", self.autostart)
        self.assertNotIn("onboard", self.build)
        self.assertNotIn("at-spi2-core", self.build)
        self.assertNotIn("dconf-cli", self.build)
        self.assertNotIn("dconf-gsettings-backend", self.build)
        self.assertNotIn("libglib2.0-bin", self.build)
        self.assertNotIn("glib-compile-schemas", self.build)
        self.assertNotIn("dbus-x11", self.build)
        self.assertNotIn("onscreen", self.ini)
        self.assertNotIn("[keyboard]", self.ini)
        removed = [
            ROOT / "kiosk_skeleton" / "etc" / "onboard" / "onboard-defaults.conf",
            ROOT
            / "kiosk_skeleton"
            / "usr"
            / "share"
            / "glib-2.0"
            / "schemas"
            / "99_kiosk-onboard.gschema.override",
        ]
        for path in removed:
            self.assertFalse(path.exists(), path)

    def test_openbox_locks_chromium_fullscreen(self) -> None:
        root = ET.parse(RC).getroot()
        self.assertEqual(list(root.find("o:keyboard", OB_NS)), [])
        self.assertEqual(list(root.find("o:mouse", OB_NS)), [])
        rules = root.findall("o:applications/o:application", OB_NS)
        matched = {rule.get("class") or rule.get("name") for rule in rules}
        self.assertIn("Chromium*", matched)
        self.assertIn("chromium*", matched)
        self.assertIn("chromium-browser", matched)
        for rule in rules:
            self.assertEqual(rule.findtext("o:decor", default="", namespaces=OB_NS), "no")
            self.assertEqual(rule.findtext("o:fullscreen", default="", namespaces=OB_NS), "yes")
            self.assertEqual(rule.findtext("o:maximized", default="", namespaces=OB_NS), "yes")

    def test_fullscreen_is_reapplied_for_the_session(self) -> None:
        self.assertIn("add,fullscreen", self.hold)
        self.assertIn("remove,fullscreen", self.hold)
        self.assertIn("getdisplaygeometry", self.hold)
        self.assertIn("chromium-browser", self.hold)
        self.assertIn("while true", self.hold)
        self.assertIn("wmctrl", self.build)
        install = (
            ROOT / "kiosk_skeleton" / "usr" / "lib" / "tbw" / "install-tbw.sh"
        ).read_text(encoding="utf-8")
        self.assertNotIn("kiosk-qwerty-keyboard", install)
        self.assertNotIn("kiosk-os-keyboard.css", install)
        self.assertNotIn("kiosk-step-name__keyboard", install)

    def test_shell_scripts_parse(self) -> None:
        for script in (
            AUTOSTART,
            HOLD,
            BUILD,
            ROOT / "kiosk_skeleton" / "usr" / "bin" / "kiosk-api-hold",
            ROOT / "kiosk_skeleton" / "usr" / "bin" / "tbw-root-api",
            ROOT / "kiosk_skeleton" / "usr" / "lib" / "tbw" / "install-tbw.sh",
        ):
            subprocess.run(["bash", "-n", str(script)], check=True)


if __name__ == "__main__":
    unittest.main()
