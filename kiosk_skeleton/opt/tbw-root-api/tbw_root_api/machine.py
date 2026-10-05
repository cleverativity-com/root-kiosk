"""Dispenser state machine for the local TBW hardware API.

The kiosk UI talks to this process on /api/v1. The image runs this
service when services/tbw-root-api from accleverate-v26 is not installed.
TBW_HARDWARE_MODE=gpio pulses the stepper pumps. TBW_HARDWARE_MODE=sim
only advances the job, which is what the unit tests use.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tbw_root_api.pumps import DoseCancelled, open_gpio_driver, run_dose


PUMP_COUNT = 5
MACHINE_STATES = (
    "ready",
    "idle",
    "dispensing",
    "locked",
    "cleaning",
    "maintenance_pumps_forward",
    "maintenance_pumps_reverse",
    "error",
    "printing",
)


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        severity: str = "blocking",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.severity = severity

    def body(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.details is not None:
            error["details"] = self.details
        return {"error": error}


@dataclass
class Pump:
    pump_id: int
    bag_id: int
    remaining_shots: int
    locked: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "pumpId": self.pump_id,
            "bagId": self.bag_id,
            "locked": self.locked,
            "error": self.error,
            "remainingShots": self.remaining_shots,
        }


@dataclass
class Job:
    job_id: str
    kind: str
    status: str
    progress_percent: int = 0
    current_pump_id: int | None = None
    requested_shots: list[int] = field(default_factory=lambda: [0, 0, 0, 0, 0])
    completed_shots: list[int] = field(default_factory=lambda: [0, 0, 0, 0, 0])
    errors: list[dict[str, str]] = field(default_factory=list)
    request_id: str | None = None
    cancel: asyncio.Event = field(default_factory=asyncio.Event)

    def as_status(self) -> dict[str, Any]:
        return {
            "jobId": self.job_id,
            "status": self.status,
            "progressPercent": self.progress_percent,
            "currentPumpId": self.current_pump_id,
            "requestedShots": list(self.requested_shots),
            "completedShots": list(self.completed_shots),
            "errors": list(self.errors),
        }


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


class Machine:
    def __init__(self) -> None:
        self.state_dir = Path(os.environ.get("TBW_STATE_DIR", "/var/lib/tbw-root-api"))
        self.config_path = Path(
            os.environ.get("TBW_CONFIG", "/etc/tbw-root-api/config_dosatore.json")
        )
        self.log_path = self.state_dir / "logs" / "tbw-root-api.log"
        self.shot_seconds = _env_float("TBW_SHOT_SECONDS", 0.8)
        self.initial_shots = _env_int("TBW_INITIAL_SHOTS", 100)
        self.port = _env_int("TBW_PORT", 8765)
        raw_mode = os.environ.get("TBW_HARDWARE_MODE", "sim").strip().lower()
        self.hardware_mode = raw_mode if raw_mode in {"gpio", "sim"} else "sim"
        self.pump_driver = None
        self.gpio_error: str | None = None
        self.config = self._load_config()
        self.pumps = [
            Pump(pump_id=i, bag_id=i, remaining_shots=self.initial_shots)
            for i in range(1, PUMP_COUNT + 1)
        ]
        self.machine_state = "ready"
        self.active_job: Job | None = None
        self.jobs: dict[str, Job] = {}
        self.by_request: dict[str, str] = {}
        self.logs: list[dict[str, Any]] = []
        self._seq = 0
        self.lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self.last_print_job_id: str | None = None
        self._load_state()
        self.log(
            "tbw_root_api",
            "logging.started",
            f"Verbose logging started at {self.log_path}",
            details={
                "logPath": str(self.log_path),
                "level": "INFO",
                "hardwareMode": self.hardware_mode,
            },
        )
        if self.hardware_mode == "gpio":
            try:
                self.pump_driver = open_gpio_driver()
            except Exception as exc:
                self.gpio_error = str(exc)
                self.log(
                    "tbw_root_api.hardware.pins",
                    "hardware.gpio_failed",
                    "GPIO pump driver failed",
                    level="error",
                    details={"error": self.gpio_error},
                )
        self.log(
            "tbw_root_api",
            "hardware.mode",
            f"Hardware mode is {self.hardware_mode}",
            details={
                "mode": self.hardware_mode,
                "gpioAvailable": self.pump_driver is not None,
            },
        )

    def _load_config(self) -> dict[str, int]:
        defaults = {
            "stepsPerMl": 1200,
            "retentionSteps": 1000,
            "dosingFrequencyHz": 6400,
        }
        for path in (self.state_dir / "config_dosatore.json", self.config_path):
            if path.is_file():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                for key in defaults:
                    if key in loaded:
                        defaults[key] = int(loaded[key])
                break
        return defaults

    def _load_state(self) -> None:
        path = self.state_dir / "state.json"
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        pumps = data.get("pumps") or []
        for item in pumps:
            pump_id = int(item.get("pumpId", 0))
            if 1 <= pump_id <= PUMP_COUNT:
                pump = self.pumps[pump_id - 1]
                pump.remaining_shots = int(item.get("remainingShots", pump.remaining_shots))
                pump.locked = bool(item.get("locked", False))
                pump.error = item.get("error")

    def _save_state(self) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            payload = {"pumps": [pump.as_dict() for pump in self.pumps]}
            (self.state_dir / "state.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
        except OSError:
            return

    def log(
        self,
        logger: str,
        event: str,
        message: str,
        *,
        level: str = "info",
        details: dict[str, Any] | None = None,
    ) -> None:
        self._seq += 1
        entry = {
            "seq": self._seq,
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": level,
            "logger": logger,
            "event": event,
            "message": message,
        }
        if details is not None:
            entry["details"] = details
        self.logs.append(entry)
        if len(self.logs) > 2000:
            self.logs = self.logs[-2000:]
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except OSError:
            return

    def reset(self) -> None:
        if self.active_job is not None:
            self.active_job.cancel.set()
        if self.pump_driver is not None:
            self.pump_driver.all_off()
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        self.pumps = [
            Pump(pump_id=i, bag_id=i, remaining_shots=self.initial_shots)
            for i in range(1, PUMP_COUNT + 1)
        ]
        self.machine_state = "ready"
        self.active_job = None
        self.jobs.clear()
        self.by_request.clear()
        self.last_print_job_id = None

    def status(self) -> dict[str, Any]:
        active = None
        if self.active_job is not None and self.active_job.status in {"queued", "running"}:
            active = {
                "jobId": self.active_job.job_id,
                "type": self.active_job.kind,
                "status": self.active_job.status,
                "progressPercent": self.active_job.progress_percent,
            }
        return {
            "machineState": self.machine_state,
            "apiVersion": "1.0.0",
            "timestamp": _utcnow(),
            "wifi": _wifi_status(),
            "pumps": [pump.as_dict() for pump in self.pumps],
            "activeJob": active,
            "errors": [],
            "printer": {
                "connected": True,
                "model": "SimulatedLabelPrinter",
                "mediaLoaded": True,
                "error": None,
                "lastPrintJobId": self.last_print_job_id,
            },
        }

    def _require_ready(self) -> None:
        if self.machine_state != "ready":
            raise ApiError(
                409,
                "MACHINE_BUSY",
                f"Machine is {self.machine_state}",
                details={"machineState": self.machine_state},
            )

    def _normalize_shots(self, pump_shots: list[int]) -> list[int]:
        if len(pump_shots) != PUMP_COUNT:
            raise ApiError(
                422,
                "FORMULA_INVALID",
                "Request payload is invalid",
                details={"issues": [{"msg": "pumpShots must contain 5 items"}]},
            )
        shots = [int(value) for value in pump_shots]
        if any(value < 0 for value in shots):
            raise ApiError(422, "FORMULA_INVALID", "Shot counts must be zero or positive")
        if sum(shots) <= 0:
            raise ApiError(422, "FORMULA_INVALID", "At least one shot is required")
        if sum(shots) > 50:
            raise ApiError(
                422,
                "FORMULA_TOTAL_EXCEEDED",
                "Formula exceeds the maximum number of shots",
            )
        short = []
        for pump, count in zip(self.pumps, shots):
            if count <= 0:
                continue
            if pump.locked or pump.error or pump.remaining_shots < count:
                short.append(pump.pump_id)
        if short:
            raise ApiError(
                409,
                "INSUFFICIENT_INGREDIENT",
                "One or more pumps cannot deliver the requested shots",
                details={"pumpIds": short},
            )
        return shots

    async def start_dispense(self, request_id: str, pump_shots: list[int]) -> dict[str, Any]:
        async with self.lock:
            existing = self.by_request.get(request_id)
            if existing and existing in self.jobs:
                job = self.jobs[existing]
                return {
                    "jobId": job.job_id,
                    "status": "accepted",
                    "machineState": self.machine_state,
                    "message": "Dispense already accepted",
                }
            self._require_ready()
            shots = self._normalize_shots(pump_shots)
            if self.hardware_mode == "gpio" and self.pump_driver is None:
                raise ApiError(
                    503,
                    "HARDWARE_OFFLINE",
                    "Pump GPIO is not available",
                    details={"error": self.gpio_error or "driver not open"},
                )
            job = Job(
                job_id=str(uuid.uuid4()),
                kind="dispense",
                status="running",
                requested_shots=shots,
                request_id=request_id,
            )
            self.jobs[job.job_id] = job
            self.by_request[request_id] = job.job_id
            if self.pump_driver is None and self.shot_seconds <= 0:
                self._apply_shots(job)
                self.active_job = None
                self.machine_state = "ready"
            else:
                self.active_job = job
                self.machine_state = "dispensing"
                self._track(asyncio.create_task(self._run_dispense(job)))
            self.log(
                "tbw_root_api.hardware.pumps",
                "dispense.accepted",
                "Dispense accepted",
                details={"jobId": job.job_id, "pumpShots": shots},
            )
            return {
                "jobId": job.job_id,
                "status": "accepted",
                "machineState": self.machine_state,
                "message": "Dispense accepted",
            }

    def _track(self, task: asyncio.Task[None]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _apply_shots(self, job: Job) -> None:
        total = sum(job.requested_shots) or 1
        done = 0
        for index, count in enumerate(job.requested_shots):
            if count <= 0:
                continue
            job.current_pump_id = index + 1
            for _ in range(count):
                if job.cancel.is_set():
                    job.status = "cancelled"
                    job.errors.append(
                        {
                            "code": "MACHINE_BUSY",
                            "severity": "blocking",
                            "message": "Dispense cancelled",
                        }
                    )
                    return
                job.completed_shots[index] += 1
                self.pumps[index].remaining_shots -= 1
                done += 1
                job.progress_percent = int(done * 100 / total)
        job.progress_percent = 100
        job.current_pump_id = None
        job.status = "completed"
        self._save_state()
        self.log(
            "tbw_root_api.hardware.pumps",
            "dispense.completed",
            "Dispense completed",
            details={"jobId": job.job_id},
        )

    async def _run_dispense(self, job: Job) -> None:
        try:
            if self.pump_driver is not None:
                await self._run_gpio_dispense(job)
            else:
                await self._run_simulated_dispense(job)
        finally:
            async with self.lock:
                if self.active_job is job:
                    self.active_job = None
                    self.machine_state = "ready"

    async def _run_simulated_dispense(self, job: Job) -> None:
        total = sum(job.requested_shots) or 1
        done = 0
        for index, count in enumerate(job.requested_shots):
            if count <= 0:
                continue
            job.current_pump_id = index + 1
            for _ in range(count):
                if job.cancel.is_set():
                    job.status = "cancelled"
                    job.errors.append(
                        {
                            "code": "MACHINE_BUSY",
                            "severity": "blocking",
                            "message": "Dispense cancelled",
                        }
                    )
                    return
                await asyncio.sleep(self.shot_seconds)
                if job.cancel.is_set():
                    job.status = "cancelled"
                    return
                async with self.lock:
                    job.completed_shots[index] += 1
                    self.pumps[index].remaining_shots -= 1
                    self._save_state()
                    done += 1
                    job.progress_percent = int(done * 100 / total)
        async with self.lock:
            job.progress_percent = 100
            job.current_pump_id = None
            job.status = "completed"
        self.log(
            "tbw_root_api.hardware.pumps",
            "dispense.completed",
            "Dispense completed",
            details={"jobId": job.job_id, "simulated": True},
        )

    async def _run_gpio_dispense(self, job: Job) -> None:
        driver = self.pump_driver
        if driver is None:
            return

        def on_progress(done: int, total: int) -> None:
            job.progress_percent = int(done * 100 / total) if total else 100

        try:
            await asyncio.to_thread(
                run_dose,
                driver,
                job.requested_shots,
                steps_per_ml=self.config["stepsPerMl"],
                retention_steps=self.config["retentionSteps"],
                dosing_frequency_hz=self.config["dosingFrequencyHz"],
                should_stop=job.cancel.is_set,
                on_progress=on_progress,
            )
        except DoseCancelled:
            async with self.lock:
                if job.status == "running":
                    job.status = "cancelled"
                    job.errors.append(
                        {
                            "code": "MACHINE_BUSY",
                            "severity": "blocking",
                            "message": "Dispense cancelled",
                        }
                    )
            return
        except Exception as exc:
            driver.all_off()
            async with self.lock:
                job.status = "failed"
                job.current_pump_id = None
                job.errors.append(
                    {
                        "code": "HARDWARE_OFFLINE",
                        "severity": "blocking",
                        "message": str(exc) or "Pump GPIO failed during dispense",
                    }
                )
            self.log(
                "tbw_root_api.hardware.pumps",
                "dispense.failed",
                "GPIO dispense failed",
                level="error",
                details={"jobId": job.job_id, "error": str(exc)},
            )
            return

        async with self.lock:
            for index, count in enumerate(job.requested_shots):
                job.completed_shots[index] = count
                if count > 0:
                    self.pumps[index].remaining_shots -= count
            job.progress_percent = 100
            job.current_pump_id = None
            job.status = "completed"
            self._save_state()
        self.log(
            "tbw_root_api.hardware.pumps",
            "dispense.completed",
            "Dispense completed",
            details={"jobId": job.job_id, "simulated": False},
        )

    def job_status(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise ApiError(404, "FORMULA_INVALID", "Unknown dispense job")
        return job.as_status()

    async def emergency_stop(self) -> dict[str, Any]:
        async with self.lock:
            job = self.active_job
            if job is not None:
                job.cancel.set()
                if job.status in {"queued", "running"}:
                    job.status = "cancelled"
                    job.errors.append(
                        {
                            "code": "MACHINE_BUSY",
                            "severity": "blocking",
                            "message": "Emergency stop",
                        }
                    )
                self.active_job = None
            self.machine_state = "ready"
            if self.pump_driver is not None:
                self.pump_driver.all_off()
            self.log("tbw_root_api.hardware", "emergency_stop", "Emergency stop")
            return {"machineState": self.machine_state, "message": "Emergency stop"}

    async def lock_pumps(self, pump_ids: list[int], reason: str) -> dict[str, Any]:
        async with self.lock:
            self._require_ready()
            self._check_pump_ids(pump_ids)
            for pump_id in pump_ids:
                self.pumps[pump_id - 1].locked = True
            self._save_state()
            self.log(
                "tbw_root_api.hardware.pumps",
                "pumps.locked",
                reason or "Pumps locked",
                details={"pumpIds": pump_ids},
            )
            locked = [pump.pump_id for pump in self.pumps if pump.locked]
            if len(locked) == PUMP_COUNT:
                self.machine_state = "locked"
            return {"machineState": self.machine_state, "lockedPumpIds": locked}

    async def unlock_pumps(self, pump_ids: list[int]) -> dict[str, Any]:
        async with self.lock:
            self._check_pump_ids(pump_ids)
            if self.machine_state not in {"ready", "locked"}:
                raise ApiError(409, "MACHINE_BUSY", f"Machine is {self.machine_state}")
            for pump_id in pump_ids:
                self.pumps[pump_id - 1].locked = False
            self._save_state()
            if self.machine_state == "locked":
                self.machine_state = "ready"
            locked = [pump.pump_id for pump in self.pumps if pump.locked]
            return {"machineState": self.machine_state, "lockedPumpIds": locked}

    async def clean(self, request_id: str, pump_ids: list[int]) -> dict[str, Any]:
        async with self.lock:
            self._require_ready()
            self._check_pump_ids(pump_ids)
            job = Job(job_id=str(uuid.uuid4()), kind="clean", status="running", request_id=request_id)
            self.jobs[job.job_id] = job
            self.active_job = job
            self.machine_state = "cleaning"
            self._track(asyncio.create_task(self._finish_soon(job, "ready")))
            return {
                "jobId": job.job_id,
                "status": "accepted",
                "machineState": self.machine_state,
            }

    async def start_maintenance(self, direction: str) -> dict[str, Any]:
        async with self.lock:
            self._require_ready()
            self.machine_state = (
                "maintenance_pumps_forward"
                if direction == "forward"
                else "maintenance_pumps_reverse"
            )
            return {"machineState": self.machine_state, "direction": direction}

    async def stop_maintenance(self) -> dict[str, Any]:
        async with self.lock:
            if self.machine_state not in {
                "maintenance_pumps_forward",
                "maintenance_pumps_reverse",
                "ready",
            }:
                raise ApiError(409, "MACHINE_BUSY", f"Machine is {self.machine_state}")
            self.machine_state = "ready"
            return {"machineState": self.machine_state, "direction": None}

    async def print_label(
        self,
        *,
        request_id: str,
        dispense_id: str,
        person_name: str,
        components: list[dict[str, Any]],
    ) -> dict[str, Any]:
        async with self.lock:
            self._require_ready()
            print_job_id = str(uuid.uuid4())
            self.last_print_job_id = print_job_id
            lines = [
                f"{item.get('name', '')}:{item.get('shots', 0)}"
                for item in components
            ]
            payload = f"tbw:{dispense_id}:{person_name}:{'|'.join(lines)}"
            self.log(
                "tbw_root_api.hardware",
                "label.printed",
                "Label accepted",
                details={"requestId": request_id, "printJobId": print_job_id},
            )
            return {
                "printJobId": print_job_id,
                "dispenseId": dispense_id,
                "status": "completed",
                "qrPayload": payload,
            }

    def update_config(self, payload: dict[str, int]) -> dict[str, int]:
        ranges = {
            "stepsPerMl": (500, 5000),
            "retentionSteps": (0, 1000),
            "dosingFrequencyHz": (100, 10000),
        }
        updated = dict(self.config)
        for key, (low, high) in ranges.items():
            value = int(payload[key])
            if value < low or value > high:
                raise ApiError(422, "FORMULA_INVALID", f"{key} is out of range")
            updated[key] = value
        self.config = updated
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            (self.state_dir / "config_dosatore.json").write_text(
                json.dumps(updated), encoding="utf-8"
            )
        except OSError:
            pass
        return dict(updated)

    def get_logs(self, after_seq: int, limit: int, min_level: str | None) -> dict[str, Any]:
        order = {"debug": 10, "info": 20, "warning": 30, "error": 40}
        minimum = order.get((min_level or "debug").lower(), 10)
        items = []
        for entry in self.logs:
            if entry["seq"] <= after_seq:
                continue
            if order.get(entry["level"], 20) < minimum:
                continue
            items.append(entry)
            if len(items) >= limit:
                break
        return {
            "items": items,
            "lastSeq": self._seq,
            "filePath": str(self.log_path),
            "hardwareMode": self.hardware_mode,
        }

    async def _finish_soon(self, job: Job, next_state: str) -> None:
        if self.shot_seconds > 0:
            await asyncio.sleep(min(self.shot_seconds, 0.5))
        async with self.lock:
            job.progress_percent = 100
            job.status = "completed"
            if self.active_job is job:
                self.active_job = None
                self.machine_state = next_state

    def _check_pump_ids(self, pump_ids: list[int]) -> None:
        if not pump_ids:
            raise ApiError(422, "FORMULA_INVALID", "pumpIds is required")
        for pump_id in pump_ids:
            if pump_id < 1 or pump_id > PUMP_COUNT:
                raise ApiError(422, "FORMULA_INVALID", f"Unknown pump {pump_id}")


def _wifi_status() -> dict[str, Any]:
    # The kiosk UI does not read this block. Report no station association
    # rather than guessing an address from the routing tables.
    return {"connected": False, "ssid": None, "ip": None, "rssi": None}
