"""HTTP API for the TBW Root dispenser.

The kiosk UI calls these routes on the same origin under /api/v1.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tbw_root_api.machine import MACHINE_STATES, ApiError, Machine

app = FastAPI(
    title="TBW Root API",
    description="Local hardware API for the TBW Root dispenser (GPIO on Pi, simulated elsewhere)",
    version="1.0.0",
)
machine = Machine()


class DispenseRequest(BaseModel):
    requestId: str
    pumpShots: list[int] = Field(min_length=5, max_length=5)


class PumpLockRequest(BaseModel):
    reason: str
    pumpIds: list[int]


class PumpUnlockRequest(BaseModel):
    pumpIds: list[int]


class CleanRequest(BaseModel):
    requestId: str
    pumpIds: list[int]
    mode: str = "standard"


class PumpMaintenanceStartRequest(BaseModel):
    direction: str


class LabelComponent(BaseModel):
    pumpId: int
    name: str
    shots: int


class LabelPrintRequest(BaseModel):
    requestId: str
    dispenseId: str
    personName: str
    components: list[LabelComponent]


class DispenserConfigPayload(BaseModel):
    stepsPerMl: int = Field(ge=500, le=5000)
    retentionSteps: int = Field(ge=0, le=1000)
    dosingFrequencyHz: int = Field(ge=100, le=10000)


def _api_error(status: int, code: str, message: str, details: dict[str, Any] | None = None) -> JSONResponse:
    error: dict[str, Any] = {
        "code": code,
        "severity": "blocking",
        "message": message,
    }
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status, content={"error": error})


@app.exception_handler(ApiError)
async def handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.body())


@app.exception_handler(RequestValidationError)
async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
    issues = []
    for item in exc.errors():
        issues.append(
            {
                "type": item.get("type"),
                "loc": list(item.get("loc", [])),
                "msg": item.get("msg"),
                "input": item.get("input"),
            }
        )
    machine.log(
        "tbw_root_api.http",
        "http.validation",
        f"{request.method} {request.url.path} -> 422",
        level="warning",
    )
    return _api_error(
        422,
        "FORMULA_INVALID",
        "Request payload is invalid",
        {"issues": issues},
    )


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    machine.log(
        "tbw_root_api.http",
        "http.request",
        f"{request.method} {request.url.path} -> {response.status_code} ({duration_ms}ms)",
        details={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "durationMs": duration_ms,
        },
    )
    return response


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "port": machine.port}


@app.get("/api/v1/machine/status")
def machine_status() -> dict[str, Any]:
    return machine.status()


@app.post("/api/v1/machine/emergency-stop")
async def emergency_stop() -> dict[str, Any]:
    return await machine.emergency_stop()


@app.post("/api/v1/dispense", status_code=202)
async def start_dispense(body: DispenseRequest) -> dict[str, Any]:
    return await machine.start_dispense(body.requestId, body.pumpShots)


@app.get("/api/v1/dispense/{job_id}/status")
def dispense_status(job_id: str) -> dict[str, Any]:
    return machine.job_status(job_id)


@app.post("/api/v1/pumps/lock")
async def lock_pumps(body: PumpLockRequest) -> dict[str, Any]:
    return await machine.lock_pumps(body.pumpIds, body.reason)


@app.post("/api/v1/pumps/unlock")
async def unlock_pumps(body: PumpUnlockRequest) -> dict[str, Any]:
    return await machine.unlock_pumps(body.pumpIds)


@app.post("/api/v1/maintenance/tubes/clean", status_code=202)
async def clean_tubes(body: CleanRequest) -> dict[str, Any]:
    if body.mode != "standard":
        raise ApiError(422, "FORMULA_INVALID", "Unsupported clean mode")
    return await machine.clean(body.requestId, body.pumpIds)


@app.post("/api/v1/maintenance/pumps/start", status_code=202)
async def start_pumps(body: PumpMaintenanceStartRequest) -> dict[str, Any]:
    if body.direction not in {"forward", "reverse"}:
        raise ApiError(422, "FORMULA_INVALID", "direction must be forward or reverse")
    return await machine.start_maintenance(body.direction)


@app.post("/api/v1/maintenance/pumps/stop")
async def stop_pumps() -> dict[str, Any]:
    return await machine.stop_maintenance()


@app.post("/api/v1/labels/print", status_code=202)
async def print_label(body: LabelPrintRequest) -> dict[str, Any]:
    return await machine.print_label(
        request_id=body.requestId,
        dispense_id=body.dispenseId,
        person_name=body.personName,
        components=[item.model_dump() for item in body.components],
    )


@app.get("/api/v1/config")
def get_config() -> dict[str, int]:
    return dict(machine.config)


@app.put("/api/v1/config")
def put_config(body: DispenserConfigPayload) -> dict[str, int]:
    return machine.update_config(body.model_dump())


@app.get("/api/v1/logs")
def get_logs(
    afterSeq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    minLevel: str | None = None,
) -> dict[str, Any]:
    return machine.get_logs(afterSeq, limit, minLevel)


# Keep the state enum referenced so a future OpenAPI export stays aligned.
_ = MACHINE_STATES
