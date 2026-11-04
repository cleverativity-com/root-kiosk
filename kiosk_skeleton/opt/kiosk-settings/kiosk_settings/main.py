"""HTTP API for Wi-Fi, IPv4, and dispenser identity.

The kiosk UI calls these routes on the same origin. Swagger is at /settings/docs.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from kiosk_settings.network import (
    NetworkError,
    apply_saved,
    live_status,
    scan_wifi,
    validate_static,
)
from kiosk_settings.store import StateError, delete_json, read_json, write_json

app = FastAPI(
    title="Kiosk settings",
    description=(
        "Wi-Fi, IPv4 addressing, and dispenser identity. "
        "Settings are stored on the boot partition. The root filesystem stays read-only."
    ),
    version="1.0.0",
    docs_url="/settings/docs",
    redoc_url="/settings/redoc",
    openapi_url="/settings/openapi.json",
)

_DISPENSER_ID = re.compile(r"^[\S]{1,64}$")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


class Ipv4Dhcp(BaseModel):
    mode: Literal["dhcp"]


class Ipv4Static(BaseModel):
    mode: Literal["static"]
    address: str
    netmask: str
    gateway: str
    dns: str


class NetworkPut(BaseModel):
    ssid: str = Field(min_length=1, max_length=32)
    password: str | None = Field(
        default=None,
        max_length=63,
        description="Omit to keep the saved password. An empty string joins an open network.",
    )
    ipv4: Ipv4Dhcp | Ipv4Static = Field(discriminator="mode")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "ssid": "Office",
                    "password": "correct horse",
                    "ipv4": {"mode": "dhcp"},
                },
                {
                    "ssid": "Office",
                    "password": "correct horse",
                    "ipv4": {
                        "mode": "static",
                        "address": "192.168.1.50",
                        "netmask": "255.255.255.0",
                        "gateway": "192.168.1.1",
                        "dns": "8.8.8.8",
                    },
                },
            ]
        }
    )


class Ipv4Public(BaseModel):
    mode: Literal["dhcp", "static"]
    address: str | None = None
    netmask: str | None = None
    gateway: str | None = None
    dns: str | None = None


class StatusBody(BaseModel):
    connected: bool
    ssid: str | None = None
    address: str | None = None
    netmask: str | None = None
    gateway: str | None = None
    dns: list[str] = Field(default_factory=list)


class NetworkBody(BaseModel):
    ssid: str | None
    hasPassword: bool
    ipv4: Ipv4Public
    status: StatusBody


class NetworkUpdateBody(NetworkBody):
    saved: bool
    associated: bool
    addressing: bool
    detail: str | None = None


class WifiNetwork(BaseModel):
    ssid: str
    bssid: str
    signal: int = Field(description="Signal strength in dBm. Closer to zero is stronger.")
    frequency: int
    security: str = Field(description="open, wpa-psk, wpa3, or wep")


class WifiScanBody(BaseModel):
    networks: list[WifiNetwork]


class DispenserPut(BaseModel):
    dispenserId: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=128)

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"dispenserId": "D-1042", "name": "Roma Termini"}]}
    )


class DispenserBody(BaseModel):
    configured: bool
    dispenserId: str | None = None
    name: str | None = None
    updatedAt: str | None = None


def _error(status: int, code: str, message: str, details: dict[str, Any] | None = None) -> JSONResponse:
    error: dict[str, Any] = {
        "code": code,
        "severity": "blocking",
        "message": message,
    }
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status, content={"error": error})


def _validation_code(path: str) -> str:
    if path.startswith("/api/v1/dispenser"):
        return "DISPENSER_INVALID"
    return "NETWORK_INVALID"


@app.exception_handler(ApiError)
async def handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
    return _error(exc.status, exc.code, exc.message, exc.details)


@app.exception_handler(StateError)
async def handle_state_error(_request: Request, exc: StateError) -> JSONResponse:
    return _error(500, "STATE_WRITE_FAILED", str(exc) or "Could not write the boot partition")


@app.exception_handler(NetworkError)
async def handle_network_error(_request: Request, exc: NetworkError) -> JSONResponse:
    status = 422 if exc.code == "NETWORK_INVALID" else 503
    return _error(status, exc.code, exc.message)


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
    return _error(
        422,
        _validation_code(request.url.path),
        "Request payload is invalid",
        {"issues": issues},
    )


def _public_ipv4(saved: dict | None) -> dict[str, Any]:
    ipv4 = (saved or {}).get("ipv4") if saved else None
    if not isinstance(ipv4, dict) or ipv4.get("mode") != "static":
        return {"mode": "dhcp"}
    return {
        "mode": "static",
        "address": ipv4.get("address"),
        "netmask": ipv4.get("netmask"),
        "gateway": ipv4.get("gateway"),
        "dns": ipv4.get("dns"),
    }


def _network_view(saved: dict | None, status: dict[str, Any]) -> dict[str, Any]:
    return {
        "ssid": saved.get("ssid") if saved else None,
        "hasPassword": bool(saved and saved.get("password")),
        "ipv4": _public_ipv4(saved),
        "status": status,
    }


def _stored_network(body: NetworkPut, previous: dict | None) -> dict[str, Any]:
    if any(ord(ch) < 32 for ch in body.ssid):
        raise NetworkError("NETWORK_INVALID", "ssid contains unsupported characters")
    if body.password is None:
        previous_password = previous.get("password") if previous else None
        password = previous_password if isinstance(previous_password, str) else ""
    else:
        password = body.password
    if password and not 8 <= len(password) <= 63:
        raise NetworkError("NETWORK_INVALID", "password must be 8 to 63 characters")
    if isinstance(body.ipv4, Ipv4Static):
        ipv4 = {
            "mode": "static",
            "address": body.ipv4.address.strip(),
            "netmask": body.ipv4.netmask.strip(),
            "gateway": body.ipv4.gateway.strip(),
            "dns": body.ipv4.dns.strip(),
        }
        try:
            validate_static(ipv4)
        except ValueError as exc:
            raise NetworkError("NETWORK_INVALID", str(exc)) from exc
    else:
        ipv4 = {"mode": "dhcp"}
    return {"ssid": body.ssid, "password": password, "ipv4": ipv4}


def _dispenser_view(saved: dict | None) -> dict[str, Any]:
    if not saved or not isinstance(saved.get("dispenserId"), str) or not saved["dispenserId"]:
        return {"configured": False}
    return {
        "configured": True,
        "dispenserId": saved["dispenserId"],
        "name": saved.get("name"),
        "updatedAt": saved.get("updatedAt"),
    }


def _check_dispenser_id(value: str) -> str:
    if value.strip() != value or not _DISPENSER_ID.fullmatch(value):
        raise ApiError(422, "DISPENSER_INVALID", "dispenserId must be 1 to 64 characters without spaces")
    if "/" in value or "\\" in value:
        raise ApiError(422, "DISPENSER_INVALID", "dispenserId must not contain a slash")
    return value


@app.get("/api/v1/network/wifi", tags=["Network"], response_model=WifiScanBody)
def wifi_scan() -> dict[str, Any]:
    return {"networks": scan_wifi()}


@app.get("/api/v1/network/status", tags=["Network"], response_model=StatusBody)
def network_status() -> dict[str, Any]:
    return live_status()


@app.get("/api/v1/network", tags=["Network"], response_model=NetworkBody)
def get_network() -> dict[str, Any]:
    return _network_view(read_json("network.json"), live_status())


@app.put("/api/v1/network", tags=["Network"], response_model=NetworkUpdateBody)
def put_network(body: NetworkPut) -> dict[str, Any]:
    previous = read_json("network.json")
    document = _stored_network(body, previous)
    write_json("network.json", document)
    try:
        result = apply_saved(document, mode="now")
    except OSError as exc:
        result = {
            "associated": False,
            "addressing": False,
            "status": live_status(),
            "detail": str(exc),
        }
    view = _network_view(document, result["status"])
    view.update(
        {
            "saved": True,
            "associated": result["associated"],
            "addressing": result["addressing"],
            "detail": result["detail"],
        }
    )
    return view


@app.get(
    "/api/v1/dispenser",
    tags=["Dispenser"],
    response_model=DispenserBody,
    response_model_exclude_none=True,
)
def get_dispenser() -> dict[str, Any]:
    return _dispenser_view(read_json("dispenser.json"))


@app.put(
    "/api/v1/dispenser",
    tags=["Dispenser"],
    response_model=DispenserBody,
    response_model_exclude_none=True,
)
def put_dispenser(body: DispenserPut) -> dict[str, Any]:
    dispenser_id = _check_dispenser_id(body.dispenserId)
    name = body.name.strip() if isinstance(body.name, str) else None
    if name == "":
        name = None
    if name is not None and any(ord(ch) < 32 for ch in name):
        raise ApiError(422, "DISPENSER_INVALID", "name contains unsupported characters")
    saved = {
        "dispenserId": dispenser_id,
        "name": name,
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_json("dispenser.json", saved)
    return _dispenser_view(saved)


@app.delete(
    "/api/v1/dispenser",
    tags=["Dispenser"],
    response_model=DispenserBody,
    response_model_exclude_none=True,
)
def delete_dispenser() -> dict[str, Any]:
    delete_json("dispenser.json")
    return {"configured": False}
