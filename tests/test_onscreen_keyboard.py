#!/usr/bin/env python3
"""Chromium stays in fullscreen kiosk mode, with no Linux on-screen keyboard."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTOSTART = ROOT / "kiosk_skeleton" / "home" / "pi" / ".config" / "openbox" / "autostart"
BUILD = ROOT / "kiosk_skeleton" / "build.sh"
INI = ROOT / "kiosk_skeleton" / "boot" / "firmware" / "kioskbrowser.ini"
ONBOARD_DEFAULTS = ROOT / "kiosk_skeleton" / "etc" / "onboard" / "onboard-defaults.conf"
ONBOARD_OVERRIDE = (
    ROOT
    / "kiosk_skeleton"
    / "usr"
    / "share"
    / "glib-2.0"
    / "schemas"
    / "99_kiosk-onboard.gschema.override"
)

# Installed only so Onboard could see text focus and compile its gsettings override.
ONBOARD_ONLY_PACKAGES = (
    "onboard",
    "at-spi2-core",
    "dbus-x11",
    "dconf-cli",
    "dconf-gsettings-backend",
    "libglib2.0-bin",
)


class FullscreenKioskSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.autostart = AUTOSTART.read_text(encoding="utf-8")
        self.build = BUILD.read_text(encoding="utf-8")

    def test_chromium_launches_fullscreen_kiosk(self) -> None:
        self.assertIn("--kiosk", self.autostart)
        self.assertIn("--start-fullscreen", self.autostart)
        self.assertIn("${URL} &", self.autostart)
        self.assertNotIn("--app=", self.autostart)
        self.assertNotIn("--start-maximized", self.autostart)
        self.assertNotIn("--force-renderer-accessibility", self.autostart)
        self.assertNotIn("MAXIMIZED_VERT", self.autostart)
        self.assertNotIn("MAXIMIZED_HORZ", self.autostart)

    def test_linux_onscreen_keyboard_is_not_started(self) -> None:
        lowered = self.autostart.lower()
        self.assertNotIn("onboard", lowered)
        self.assertNotIn("at-spi", lowered)
        self.assertNotIn("onscreen", lowered)

    def test_onboard_packages_and_config_are_removed(self) -> None:
        for package in ONBOARD_ONLY_PACKAGES:
            self.assertNotIn(package, self.build)
        self.assertNotIn("glib-compile-schemas", self.build)
        self.assertNotIn(".config/onboard", self.build)
        self.assertFalse(ONBOARD_DEFAULTS.exists())
        self.assertFalse(ONBOARD_OVERRIDE.exists())
        ini = INI.read_text(encoding="utf-8")
        self.assertNotIn("[keyboard]", ini)
        self.assertNotIn("onscreen", ini)


if __name__ == "__main__":
    unittest.main()
