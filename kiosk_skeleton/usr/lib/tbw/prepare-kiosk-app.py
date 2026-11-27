#!/usr/bin/env python3
"""Adjust the checked-out kiosk app before the image's production build.

The status bar on the Pi photo reads Change dispenser, Maintenance, IT, EN,
then the version, then the gear. The version label belongs first in that
group. The rest of the bar stays.
"""

from __future__ import annotations

import sys
from pathlib import Path

VERSION_SPAN = '<span class="kiosk-status-bar__version">{{ releaseLabel }}</span>'
ACTIONS_OPEN = '<div class="kiosk-status-bar__actions row items-center">'


def move_version_first(text: str) -> str:
    if text.count(VERSION_SPAN) != 1:
        raise SystemExit("kiosk status bar version label was not found once")
    if text.count(ACTIONS_OPEN) != 1:
        raise SystemExit("kiosk status bar actions group was not found once")
    without = text.replace(VERSION_SPAN, "", 1)
    # The label sat on its own line, with a blank line before the gear button.
    without = without.replace("\n\n\n", "\n\n", 1)
    insert_at = without.index(ACTIONS_OPEN) + len(ACTIONS_OPEN)
    return without[:insert_at] + "\n      " + VERSION_SPAN + without[insert_at:]


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    path = root / "apps/tbw-root-kiosk-app/src/components/kiosk/KioskStatusBar.vue"
    updated = move_version_first(path.read_text(encoding="utf-8"))
    version_at = updated.index(VERSION_SPAN)
    change_at = updated.index("onChangeDispenser")
    if version_at > change_at:
        raise SystemExit("version label is still after Change dispenser")
    path.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
