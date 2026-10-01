#!/usr/bin/env python3
"""The kiosk on-screen keyboard appears only while a text field is focused."""

from __future__ import annotations

import configparser
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ROOT / "kiosk_skeleton" / "etc" / "onboard" / "onboard-defaults.conf"
AUTOSTART = ROOT / "kiosk_skeleton" / "home" / "pi" / ".config" / "openbox" / "autostart"
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


class OnscreenKeyboardConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.defaults = configparser.ConfigParser()
        self.defaults.read(DEFAULTS, encoding="utf-8")
        self.autostart = AUTOSTART.read_text(encoding="utf-8")

    def test_keyboard_starts_hidden_and_follows_text_focus(self) -> None:
        self.assertEqual(self.defaults.get("main", "start-minimized"), "True")
        self.assertEqual(self.defaults.get("auto-show", "enabled"), "True")
        # A Pi touchscreen is not a tablet. Leaving detection on keeps the
        # keyboard hidden even when a password field is focused.
        self.assertEqual(
            self.defaults.get("auto-show", "tablet-mode-detection-enabled"),
            "False",
        )
        self.assertEqual(
            self.defaults.get("gnome-desktop-interface", "toolkit-accessibility"),
            "True",
        )

    def test_keyboard_docks_instead_of_using_a_removed_fullscreen_key(self) -> None:
        self.assertEqual(self.defaults.get("window", "docking-enabled"), "True")
        self.assertEqual(self.defaults.get("window", "docking-edge"), "bottom")
        self.assertEqual(self.defaults.get("window", "force-to-top"), "True")
        self.assertNotIn("disable-in-fullscreen", DEFAULTS.read_text(encoding="utf-8"))

    def test_session_uses_an_app_window_so_the_dock_can_show(self) -> None:
        self.assertIn('--app="${URL}"', self.autostart)
        self.assertIn("--force-renderer-accessibility", self.autostart)
        self.assertIn("touch /tmp/onboard-use-system-defaults", self.autostart)
        self.assertNotIn("--kiosk", self.autostart)
        self.assertNotIn("--start-fullscreen", self.autostart)
        self.assertNotIn("disable-in-fullscreen", self.autostart)

    def test_accessibility_default_is_compiled_into_the_image(self) -> None:
        override = OVERRIDE.read_text(encoding="utf-8")
        self.assertIn("[org.gnome.desktop.interface]", override)
        self.assertIn("toolkit-accessibility=true", override)
        self.assertIn("glib-compile-schemas", BUILD.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
