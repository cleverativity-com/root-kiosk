"""Stepper pump driver for the bundled kiosk API.

Pin numbers and the two-phase dose match the dispenser desktop app
(ToBeWare10L.py POMPE_PIN / esegui_dosaggio_motori) and
services/tbw-root-api in accleverate-v26. One shot is 20 ml.
"""

from __future__ import annotations

import time
from typing import Callable, Protocol, Sequence


# (PUL, DIR) BCM numbers, pumps 1–5.
PUMP_PINS: dict[int, tuple[int, int]] = {
    1: (12, 16),
    2: (5, 6),
    3: (19, 26),
    4: (24, 25),
    5: (20, 21),
}
DIR_FORWARD = 1
DIR_REVERSE = 0
ML_PER_DOSE = 20


class DoseCancelled(Exception):
    """The dispense was stopped before the pumps finished."""


class PumpDriver(Protocol):
    def set_dir(self, pump_id: int, forward: bool) -> None: ...
    def pulse_on(self, pump_ids: Sequence[int]) -> None: ...
    def pulse_off(self, pump_ids: Sequence[int]) -> None: ...
    def all_off(self) -> None: ...
    def close(self) -> None: ...


class GpioPumpDriver:
    """gpiozero outputs. active_high and initial low match the desktop app."""

    def __init__(self) -> None:
        from gpiozero import OutputDevice

        self._pul: dict[int, OutputDevice] = {}
        self._dir: dict[int, OutputDevice] = {}
        try:
            for pump_id, (step_pin, dir_pin) in PUMP_PINS.items():
                self._pul[pump_id] = OutputDevice(
                    step_pin, active_high=True, initial_value=False
                )
                self._dir[pump_id] = OutputDevice(
                    dir_pin, active_high=True, initial_value=False
                )
        except Exception:
            self.close()
            raise

    def set_dir(self, pump_id: int, forward: bool) -> None:
        self._dir[pump_id].value = DIR_FORWARD if forward else DIR_REVERSE

    def pulse_on(self, pump_ids: Sequence[int]) -> None:
        for pump_id in pump_ids:
            self._pul[pump_id].on()

    def pulse_off(self, pump_ids: Sequence[int]) -> None:
        for pump_id in pump_ids:
            self._pul[pump_id].off()

    def all_off(self) -> None:
        for pump_id in PUMP_PINS:
            pul = self._pul.get(pump_id)
            direction = self._dir.get(pump_id)
            if pul is not None:
                pul.off()
            if direction is not None:
                direction.off()

    def close(self) -> None:
        self.all_off()
        for device in (*self._pul.values(), *self._dir.values()):
            try:
                device.close()
            except Exception:
                continue
        self._pul.clear()
        self._dir.clear()


def open_gpio_driver() -> GpioPumpDriver:
    return GpioPumpDriver()


def run_dose(
    driver: PumpDriver,
    pump_shots: Sequence[int],
    *,
    steps_per_ml: int,
    retention_steps: int,
    dosing_frequency_hz: int,
    should_stop: Callable[[], bool],
    on_progress: Callable[[int, int], None] | None = None,
    ml_per_dose: int = ML_PER_DOSE,
) -> None:
    """Pulse the active pumps forward for the dose, then reverse for retention.

    Pumps with a shorter dose stop early while the others keep stepping.
    Pins are turned off when the dose finishes or is cancelled.
    """
    if dosing_frequency_hz < 1:
        raise RuntimeError("Dosing frequency must be positive")

    steps_by_pump = [
        int(shots) * ml_per_dose * int(steps_per_ml) for shots in pump_shots
    ]
    active = [
        (index + 1, steps) for index, steps in enumerate(steps_by_pump) if steps > 0
    ]
    if not active:
        return

    max_steps = max(steps for _, steps in active)
    retention = max(int(retention_steps), 0)
    total_work = max_steps + retention
    delay = 1.0 / (2.0 * dosing_frequency_hz)
    done = 0

    def report() -> None:
        if on_progress is not None and (done == total_work or done % 256 == 0):
            on_progress(done, total_work)

    try:
        for pump_id, _ in active:
            driver.set_dir(pump_id, forward=True)

        for step in range(max_steps):
            if should_stop():
                raise DoseCancelled()
            pulsing = [pump_id for pump_id, steps in active if step < steps]
            driver.pulse_on(pulsing)
            time.sleep(delay)
            driver.pulse_off(pulsing)
            time.sleep(delay)
            done += 1
            report()

        # Pause before reversing so the mechanism is not slammed into retention.
        time.sleep(0.1)

        for pump_id, _ in active:
            driver.set_dir(pump_id, forward=False)

        active_ids = [pump_id for pump_id, _ in active]
        for _ in range(retention):
            if should_stop():
                raise DoseCancelled()
            driver.pulse_on(active_ids)
            time.sleep(delay)
            driver.pulse_off(active_ids)
            time.sleep(delay)
            done += 1
            report()

        for pump_id, _ in active:
            driver.set_dir(pump_id, forward=True)
        if on_progress is not None:
            on_progress(total_work, total_work)
    finally:
        driver.all_off()
