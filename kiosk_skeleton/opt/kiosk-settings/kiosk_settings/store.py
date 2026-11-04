"""JSON files on the boot partition, written through a short read-write window."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class StateError(Exception):
    """The boot partition could not be updated."""


def state_dir() -> Path:
    return Path(os.environ.get("KIOSK_STATE_DIR", "/boot/firmware/kiosk-state"))


def remount_enabled() -> bool:
    return os.environ.get("KIOSK_REMOUNT", "1") != "0"


def boot_mount() -> str:
    return os.environ.get("KIOSK_BOOT_MOUNT", "/boot/firmware")


def _lock_path() -> Path:
    return Path(os.environ.get("KIOSK_STATE_LOCK", "/tmp/kiosk-state.lock"))


def _check_name(name: str) -> None:
    if name != Path(name).name or not name.endswith(".json") or name.startswith("."):
        raise StateError("invalid state file name")


def _remount(mode: str) -> None:
    if not remount_enabled():
        return
    completed = subprocess.run(
        ["mount", "-o", f"remount,{mode}", boot_mount()],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "mount failed").strip()
        raise StateError(detail)


def _remount_readonly() -> None:
    try:
        _remount("ro")
    except StateError:
        # The write already failed or the data is in place. A second
        # remount error must not hide the original failure.
        return


@contextmanager
def _locked() -> Iterator[None]:
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_json(name: str) -> dict | None:
    _check_name(name)
    path = state_dir() / name
    try:
        raw = path.read_text()
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_json(name: str, payload: dict) -> None:
    _check_name(name)
    text = json.dumps(payload, indent=2) + "\n"
    with _locked():
        _remount("rw")
        try:
            directory = state_dir()
            directory.mkdir(parents=True, exist_ok=True)
            temporary = directory / f".{name}.tmp"
            temporary.write_text(text)
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, directory / name)
            os.sync()
        except OSError as exc:
            _remount_readonly()
            raise StateError(str(exc)) from exc
        _remount("ro")


def delete_json(name: str) -> None:
    _check_name(name)
    with _locked():
        _remount("rw")
        try:
            path = state_dir() / name
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            os.sync()
        except OSError as exc:
            _remount_readonly()
            raise StateError(str(exc)) from exc
        _remount("ro")
