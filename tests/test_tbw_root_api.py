#!/usr/bin/env python3
"""Contract tests for the bundled TBW hardware API."""

from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
