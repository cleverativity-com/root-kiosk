#!/usr/bin/env python3
"""Keep the kiosk hardware env on GPIO.

The cloud container's .env uses TBW_HARDWARE=sim. That finishes a dispense
without pulsing the pumps. The kiosk image rewrites those keys to gpio.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HARDWARE_KEYS = (
    "TBW_HARDWARE",
    "TBW_HARDWARE_MODE",
    "HARDWARE",
    "HARDWARE_MODE",
)
ENSURED_KEYS = ("TBW_HARDWARE", "TBW_HARDWARE_MODE")
ENV_NAMES = {".env", ".env.local", ".env.production", ".env.development"}
SKIP_SUFFIXES = (".example", ".sample", ".template")

KIOSK_ENV = """\
# Dispenser pumps on this kiosk. gpio moves the steppers.
# sim, simulation, and simulated complete the job without moving them.
TBW_HARDWARE=gpio
TBW_HARDWARE_MODE=gpio
"""


def parse_assignment(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    if not key:
        return None
    return key, value


def normalize_text(text: str, *, ensure: bool) -> str:
    """Force hardware keys to gpio. Other lines stay as they are."""
    lines_out: list[str] = []
    seen: set[str] = set()
    ends_with_newline = text.endswith("\n") or text == ""
    body = text.splitlines()
    for line in body:
        parsed = parse_assignment(line)
        if parsed is None or parsed[0] not in HARDWARE_KEYS:
            lines_out.append(line)
            continue
        lines_out.append(f"{parsed[0]}=gpio")
        seen.add(parsed[0])
    if ensure:
        for key in ENSURED_KEYS:
            if key not in seen:
                lines_out.append(f"{key}=gpio")
    rendered = "\n".join(lines_out)
    if lines_out and (ends_with_newline or ensure):
        rendered += "\n"
    return rendered


def normalize_file(path: Path, *, ensure: bool) -> bool:
    original = path.read_text(encoding="utf-8") if path.is_file() else ""
    updated = normalize_text(original, ensure=ensure)
    if path.is_file() and updated == original:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    return True


def _interesting_env(path: Path) -> bool:
    name = path.name
    if name in ENV_NAMES:
        return True
    return name.startswith(".env.") and not name.endswith(SKIP_SUFFIXES)


def normalize_tree(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    changed: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not _interesting_env(path):
            continue
        if any(part in {"venv", ".venv", "node_modules"} for part in path.parts):
            continue
        if path.name.endswith(SKIP_SUFFIXES):
            continue
        if normalize_file(path, ensure=True):
            changed.append(path)
    return changed


def write_kiosk_env(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(KIOSK_ENV, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", type=Path, help="Write the kiosk GPIO .env to this path")
    parser.add_argument("--normalize", type=Path, help="Rewrite hardware keys in one env file")
    parser.add_argument(
        "--ensure",
        action="store_true",
        help="With --normalize, add TBW_HARDWARE and TBW_HARDWARE_MODE if missing",
    )
    parser.add_argument(
        "--normalize-tree",
        type=Path,
        help="Rewrite .env files under a tbw-root-api checkout or install",
    )
    args = parser.parse_args(argv)
    if args.write is None and args.normalize is None and args.normalize_tree is None:
        parser.error("pass --write, --normalize, or --normalize-tree")
    if args.write is not None:
        write_kiosk_env(args.write)
        print(f"wrote {args.write}")
    if args.normalize is not None:
        normalize_file(args.normalize, ensure=args.ensure)
        print(f"normalized {args.normalize}")
    if args.normalize_tree is not None:
        changed = normalize_tree(args.normalize_tree)
        print(f"normalized {len(changed)} env file(s) under {args.normalize_tree}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
