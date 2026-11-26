#!/usr/bin/env python3
"""The kiosk does not show an on-screen keyboard, and Chromium stays fullscreen."""

from __future__ import annotations

import configparser
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ROOT / "kiosk_skeleton" / "etc" / "onboard" / "onboard-defaults.conf"
AUTOSTART = ROOT / "kiosk_skeleton" / "home" / "pi" / ".config" / "openbox" / "autostart"
INI = ROOT / "kiosk_skeleton" / "boot" / "firmware" / "kioskbrowser.ini"
OVERRIDE = (
    ROOT
    / "kiosk_skeleton"
    / "usr"
    / "share"
    / "glib-2.0"
    / "schemas"
    / "99_kiosk-onboard.gschema.override"
)
BUILD = ROOT / "kiosk_skeleton" / "build.sh"


class OnscreenKeyboardDisabledTests(unittest.TestCase):
    def setUp(self) -> None:
        self.defaults = configparser.ConfigParser()
        self.defaults.read(DEFAULTS, encoding="utf-8")
        self.ini = configparser.ConfigParser()
        self.ini.read(INI, encoding="utf-8")
        self.autostart = AUTOSTART.read_text(encoding="utf-8")
        self.build = BUILD.read_text(encoding="utf-8")

    def test_onscreen_keyboard_is_disabled(self) -> None:
        self.assertEqual(self.ini.get("keyboard", "onscreen"), "0")
        self.assertEqual(self.defaults.get("main", "start-minimized"), "True")
        self.assertEqual(self.defaults.get("auto-show", "enabled"), "False")
        self.assertEqual(self.defaults.get("window", "docking-enabled"), "False")
        self.assertEqual(self.defaults.get("window", "docking-shrink-workarea"), "False")
        self.assertNotIn("toolkit-accessibility", DEFAULTS.read_text(encoding="utf-8"))

    def test_session_does_not_start_the_keyboard(self) -> None:
        self.assertNotIn("onboard", self.autostart)
        self.assertNotIn("at-spi-bus-launcher", self.autostart)
        self.assertNotIn("onboard-use-system-defaults", self.autostart)
        self.assertNotIn("--force-renderer-accessibility", self.autostart)
        self.assertNotIn("libglib2.0-bin", self.build)
        self.assertNotIn("glib-compile-schemas", self.build)
        self.assertFalse(OVERRIDE.exists())

    def test_browser_stays_fullscreen_even_if_a_keyboard_would_dock(self) -> None:
        self.assertIn("--start-fullscreen", self.autostart)
        self.assertIn("--kiosk", self.autostart)
        self.assertNotIn('--app="${URL}"', self.autostart)
        self.assertNotIn("--app=", self.autostart)
        self.assertNotIn("--start-maximized", self.autostart)
        self.assertNotIn("MAXIMIZED_VERT", self.autostart)
        self.assertNotIn("MAXIMIZED_HORZ", self.autostart)
        # The page URL is an argument of the fullscreen kiosk, not an app window.
        self.assertIn("${URL} &", self.autostart)


if __name__ == "__main__":
    unittest.main()
