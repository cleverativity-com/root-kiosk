#!/usr/bin/env python3
"""Contract tests for the bundled TBW hardware API."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "kiosk_skeleton" / "opt" / "tbw-root-api"

os.environ["TBW_STATE_DIR"] = tempfile.mkdtemp(prefix="tbw-api-")
os.environ["TBW_SHOT_SECONDS"] = "0"
os.environ["TBW_INITIAL_SHOTS"] = "10"
os.environ["TBW_PORT"] = "8765"
os.environ["TBW_CONFIG"] = str(ROOT / "kiosk_skeleton" / "etc" / "tbw-root-api" / "config_dosatore.json")
sys.path.insert(0, str(PACKAGE))

from fastapi.testclient import TestClient  # noqa: E402

from tbw_root_api.main import app, machine  # noqa: E402


class TbwRootApiTests(unittest.TestCase):
    def setUp(self) -> None:
        machine.reset()
        self.client = TestClient(app)

    def test_health_and_ready_machine(self) -> None:
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")
        self.assertEqual(health.json()["port"], 8765)

        status = self.client.get("/api/v1/machine/status")
        self.assertEqual(status.status_code, 200)
        body = status.json()
        self.assertEqual(body["machineState"], "ready")
        self.assertEqual(len(body["pumps"]), 5)
        self.assertEqual(body["pumps"][0]["remainingShots"], 10)
        self.assertEqual(body["apiVersion"], "1.0.0")

    def test_invalid_dispense_uses_kiosk_error_envelope(self) -> None:
        response = self.client.post("/api/v1/dispense", json={})
        self.assertEqual(response.status_code, 422)
        error = response.json()["error"]
        self.assertEqual(error["code"], "FORMULA_INVALID")
        self.assertIn("issues", error["details"])

    def test_dispense_completes_and_decrements_shots(self) -> None:
        started = self.client.post(
            "/api/v1/dispense",
            json={"requestId": "dispense-1", "pumpShots": [2, 0, 0, 0, 0]},
        )
        self.assertEqual(started.status_code, 202)
        job_id = started.json()["jobId"]
        self.assertEqual(started.json()["status"], "accepted")

        deadline = time.time() + 5
        body = {}
        while time.time() < deadline:
            body = self.client.get(f"/api/v1/dispense/{job_id}/status").json()
            if body["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["progressPercent"], 100)
        status = self.client.get("/api/v1/machine/status").json()
        self.assertEqual(status["machineState"], "ready")
        self.assertEqual(status["pumps"][0]["remainingShots"], 8)

    def test_insufficient_ingredient(self) -> None:
        response = self.client.post(
            "/api/v1/dispense",
            json={"requestId": "too-many", "pumpShots": [20, 20, 11, 0, 0]},
        )
        # 50 is over the formula cap, which is reported before the bag check.
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "FORMULA_TOTAL_EXCEEDED")

        response = self.client.post(
            "/api/v1/dispense",
            json={"requestId": "empty-bag", "pumpShots": [0, 11, 0, 0, 0]},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "INSUFFICIENT_INGREDIENT")

    def test_label_and_config(self) -> None:
        printed = self.client.post(
            "/api/v1/labels/print",
            json={
                "requestId": "label-1",
                "dispenseId": "dispense-1",
                "personName": "Ada",
                "components": [{"pumpId": 1, "name": "Base", "shots": 2}],
            },
        )
        self.assertEqual(printed.status_code, 202)
        self.assertIn("dispense-1", printed.json()["qrPayload"])
        config = self.client.get("/api/v1/config")
        self.assertEqual(config.status_code, 200)
        self.assertEqual(config.json()["stepsPerMl"], 1200)

    def test_gpio_without_a_driver_does_not_complete(self) -> None:
        previous_mode = machine.hardware_mode
        previous_driver = machine.pump_driver
        previous_error = machine.gpio_error
        machine.hardware_mode = "gpio"
        machine.pump_driver = None
        machine.gpio_error = "gpiozero missing"
        try:
            response = self.client.post(
                "/api/v1/dispense",
                json={"requestId": "no-gpio", "pumpShots": [1, 0, 0, 0, 0]},
            )
        finally:
            machine.hardware_mode = previous_mode
            machine.pump_driver = previous_driver
            machine.gpio_error = previous_error
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "HARDWARE_OFFLINE")
        status = self.client.get("/api/v1/machine/status").json()
        self.assertEqual(status["pumps"][0]["remainingShots"], 10)

    def test_image_enables_gpio_pumps(self) -> None:
        service = (
            ROOT / "kiosk_skeleton" / "etc" / "systemd" / "system" / "tbw-root-api.service"
        ).read_text(encoding="utf-8")
        self.assertIn("TBW_HARDWARE_MODE=gpio", service)
        self.assertNotIn("TBW_HARDWARE_MODE=sim", service)
        self.assertIn("TBW_HARDWARE=gpio", service)
        self.assertIn("EnvironmentFile=/etc/tbw-root-api/.env", service)
        self.assertIn("SupplementaryGroups=gpio", service)
        dotenv = (ROOT / "kiosk_skeleton" / "etc" / "tbw-root-api" / ".env").read_text(
            encoding="utf-8"
        )
        self.assertIn("TBW_HARDWARE=gpio", dotenv)
        self.assertIn("TBW_HARDWARE_MODE=gpio", dotenv)
        assignments = [
            line.split("=", 1)[1]
            for line in dotenv.splitlines()
            if line.startswith("TBW_HARDWARE")
        ]
        self.assertEqual(assignments, ["gpio", "gpio"])
        launcher = (ROOT / "kiosk_skeleton" / "usr" / "bin" / "tbw-root-api").read_text(
            encoding="utf-8"
        )
        self.assertLess(launcher.index("app.env"), launcher.index("/etc/tbw-root-api/.env"))
        install = (ROOT / "kiosk_skeleton" / "usr" / "lib" / "tbw" / "install-tbw.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("gpio_env.py --normalize-tree", install)
        self.assertIn("gpio_env.py --write /etc/tbw-root-api/.env", install)
        build = (ROOT / "kiosk_skeleton" / "build.sh").read_text(encoding="utf-8")
        self.assertIn("python3-gpiozero", build)
        self.assertIn("python3-lgpio", build)
        logs = self.client.get("/api/v1/logs").json()
        self.assertEqual(logs["hardwareMode"], "sim")


class DosePulseTests(unittest.TestCase):
    def test_run_dose_pulses_each_step_then_releases_pins(self) -> None:
        from tbw_root_api.pumps import run_dose

        class FakePumps:
            def __init__(self) -> None:
                self.dirs: list[tuple[int, bool]] = []
                self.high: list[tuple[int, ...]] = []
                self.low: list[tuple[int, ...]] = []
                self.released = 0

            def set_dir(self, pump_id: int, forward: bool) -> None:
                self.dirs.append((pump_id, forward))

            def pulse_on(self, pump_ids: list[int]) -> None:
                self.high.append(tuple(pump_ids))

            def pulse_off(self, pump_ids: list[int]) -> None:
                self.low.append(tuple(pump_ids))

            def all_off(self) -> None:
                self.released += 1

            def close(self) -> None:
                return None

        driver = FakePumps()
        run_dose(
            driver,
            [1, 0, 0, 0, 0],
            steps_per_ml=1,
            retention_steps=1,
            dosing_frequency_hz=1000,
            should_stop=lambda: False,
            ml_per_dose=1,
        )
        # One forward step on pump 1, then one retention step.
        self.assertEqual(driver.high, [(1,), (1,)])
        self.assertEqual(driver.low, [(1,), (1,)])
        self.assertEqual(driver.dirs[0], (1, True))
        self.assertIn((1, False), driver.dirs)
        self.assertEqual(driver.released, 1)

    def test_run_dose_stops_without_finishing_when_cancelled(self) -> None:
        from tbw_root_api.pumps import DoseCancelled, run_dose

        class FakePumps:
            def __init__(self) -> None:
                self.high = 0
                self.released = 0

            def set_dir(self, pump_id: int, forward: bool) -> None:
                return None

            def pulse_on(self, pump_ids: list[int]) -> None:
                self.high += 1

            def pulse_off(self, pump_ids: list[int]) -> None:
                return None

            def all_off(self) -> None:
                self.released += 1

            def close(self) -> None:
                return None

        driver = FakePumps()
        with self.assertRaises(DoseCancelled):
            run_dose(
                driver,
                [2, 0, 0, 0, 0],
                steps_per_ml=1,
                retention_steps=0,
                dosing_frequency_hz=1000,
                should_stop=lambda: True,
                ml_per_dose=1,
            )
        self.assertEqual(driver.high, 0)
        self.assertEqual(driver.released, 1)


class GpioJobTests(unittest.TestCase):
    def setUp(self) -> None:
        machine.reset()
        self._previous = (
            machine.hardware_mode,
            machine.pump_driver,
            machine.gpio_error,
            dict(machine.config),
        )

    def tearDown(self) -> None:
        mode, driver, error, config = self._previous
        machine.hardware_mode = mode
        machine.pump_driver = driver
        machine.gpio_error = error
        machine.config = config
        machine.reset()

    def test_gpio_job_completes_and_counts_shots_after_pulses(self) -> None:
        import tbw_root_api.pumps as pumps_mod

        class FakePumps:
            def __init__(self) -> None:
                self.pulses = 0

            def set_dir(self, pump_id: int, forward: bool) -> None:
                return None

            def pulse_on(self, pump_ids: list[int]) -> None:
                self.pulses += 1

            def pulse_off(self, pump_ids: list[int]) -> None:
                return None

            def all_off(self) -> None:
                return None

            def close(self) -> None:
                return None

        from tbw_root_api.machine import Job

        driver = FakePumps()
        machine.hardware_mode = "gpio"
        machine.pump_driver = driver
        machine.config = {
            "stepsPerMl": 1,
            "retentionSteps": 1,
            "dosingFrequencyHz": 1000,
        }
        job = Job(
            job_id="job-gpio",
            kind="dispense",
            status="running",
            requested_shots=[1, 0, 0, 0, 0],
        )
        machine.jobs[job.job_id] = job
        machine.active_job = job
        original_sleep = pumps_mod.time.sleep
        pumps_mod.time.sleep = lambda _seconds: None
        try:
            asyncio.run(machine._run_gpio_dispense(job))
        finally:
            pumps_mod.time.sleep = original_sleep

        # 20 ml per shot * 1 step/ml, plus one retention step.
        self.assertEqual(driver.pulses, 21)
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.completed_shots[0], 1)
        self.assertEqual(machine.pumps[0].remaining_shots, 9)


class HardwareEnvTests(unittest.TestCase):
    def test_simulation_dotenv_is_rewritten_to_gpio(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "gpio_env",
            ROOT / "kiosk_skeleton" / "usr" / "lib" / "tbw" / "gpio_env.py",
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        updated = module.normalize_text(
            'export TBW_HARDWARE="simulation"\nTBW_PORT=8765\n# keep\n',
            ensure=True,
        )
        self.assertIn("TBW_HARDWARE=gpio\n", updated)
        self.assertIn("TBW_HARDWARE_MODE=gpio\n", updated)
        self.assertIn("TBW_PORT=8765\n", updated)
        self.assertIn("# keep\n", updated)
        self.assertNotIn("simulation", updated)

        root = Path(tempfile.mkdtemp(prefix="tbw-env-"))
        api = root / "services" / "tbw-root-api"
        api.mkdir(parents=True)
        (api / ".env").write_text("TBW_HARDWARE=sim\n", encoding="utf-8")
        (api / ".env.example").write_text("TBW_HARDWARE=sim\n", encoding="utf-8")
        venv_env = api / "venv" / ".env"
        venv_env.parent.mkdir()
        venv_env.write_text("TBW_HARDWARE=sim\n", encoding="utf-8")
        changed = module.normalize_tree(api)
        self.assertEqual((api / ".env").read_text(encoding="utf-8"), "TBW_HARDWARE=gpio\nTBW_HARDWARE_MODE=gpio\n")
        self.assertEqual((api / ".env.example").read_text(encoding="utf-8"), "TBW_HARDWARE=sim\n")
        self.assertEqual(venv_env.read_text(encoding="utf-8"), "TBW_HARDWARE=sim\n")
        self.assertEqual(changed, [api / ".env"])

    def test_tbw_hardware_gpio_and_kiosk_dotenv(self) -> None:
        import tbw_root_api.machine as machine_mod

        previous_files = machine_mod.KIOSK_ENV_FILES
        saved = {
            key: os.environ.get(key)
            for key in ("TBW_HARDWARE", "TBW_HARDWARE_MODE")
        }
        env_path = Path(tempfile.mkdtemp(prefix="tbw-kiosk-env-")) / ".env"
        env_path.write_text("TBW_HARDWARE=gpio\n", encoding="utf-8")
        machine_mod.KIOSK_ENV_FILES = (env_path,)
        for key in saved:
            os.environ.pop(key, None)
        try:
            fresh = machine_mod.Machine()
            self.assertEqual(fresh.hardware_mode, "gpio")
            self.assertIsNone(fresh.pump_driver)
            os.environ["TBW_HARDWARE_MODE"] = "sim"
            simulated = machine_mod.Machine()
            self.assertEqual(simulated.hardware_mode, "sim")
        finally:
            machine_mod.KIOSK_ENV_FILES = previous_files
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
