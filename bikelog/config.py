"""Loading of config/capture.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "capture.toml"


@dataclass(frozen=True)
class CanConfig:
    interface: str
    bitrate: int


@dataclass(frozen=True)
class SerialConfig:
    port: str
    baudrate: int
    timeout: float


@dataclass(frozen=True)
class Config:
    can: CanConfig
    serial: SerialConfig
    session_dir: Path
    source: Path


def load(path: Path | None = None) -> Config:
    path = (path or DEFAULT_CONFIG_PATH).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"no capture config at {path}")

    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    can = raw.get("can", {})
    serial = raw.get("serial", {})
    session = raw.get("session", {})

    return Config(
        can=CanConfig(
            interface=can.get("interface", "can0"),
            bitrate=int(can.get("bitrate", 500000)),
        ),
        serial=SerialConfig(
            port=serial.get("port", "/dev/ttyUSB0"),
            baudrate=int(serial.get("baudrate", 115200)),
            timeout=float(serial.get("timeout", 1.0)),
        ),
        session_dir=Path(session.get("dir", "~/bikelog-sessions")).expanduser(),
        source=path,
    )
