"""One place that turns a source spec string into a DataSource.

Specs:
    virtual[:mirror|:sequential]   simulator, generated in memory
    replay:<session_id>            a saved session from the sessions folder
    esp32:<PORT>                   ESP32 running firmware/emg_esp32_v2 (EMGM/2)
    text:<PORT>                    legacy text firmware (emg_stream, emg_candy_esp32)
    emulator[:mode,mode,mode]      software ESP32 speaking EMGM/2 (modes: see acquisition.emulator)
    <PORT>                         bare port: protocol from config `acquisition.protocol`
"""

from __future__ import annotations

from pathlib import Path

from ..storage import Channel, Session
from .base import DataSource


def channels_from_config(cfg: dict) -> list[Channel]:
    return [Channel(**{k: c[k] for k in ("name", "arm", "muscle")}) for c in cfg["channels"]]


def open_source(spec: str, cfg: dict, data_dir: str | Path | None = None, channels: list[Channel] | None = None,
                **kwargs) -> DataSource:
    spec = spec.strip()
    kind, _, arg = spec.partition(":")
    if len(kind) == 1 and arg.startswith("\\"):  # not a scheme: a Windows path was passed
        kind, arg = "replay", spec
    chans = channels or channels_from_config(cfg)
    acq = cfg["acquisition"]
    hw = cfg.get("hardware") or {}

    if kind == "virtual":
        from .sources import VirtualSource

        return VirtualSource(cfg, mode=arg or "mirror")
    if kind == "replay":
        from ..config import DATA_DIR
        from .sources import ReplaySource

        folder = Path(arg)
        if not folder.exists():
            folder = Path(data_dir or DATA_DIR) / arg
        return ReplaySource(Session.load(folder))
    if kind == "emulator":
        from .emulator import EmulatorConfig, Esp32Emulator
        from .esp32 import Esp32Source

        modes = tuple(arg.split(",")) if arg else ("emg",) * len(chans)
        pins = tuple(c.get("gpio", 0) for c in hw.get("channels", [])) or (35, 32, 33)[: len(modes)]
        ecfg = EmulatorConfig(fs=int(acq.get("fs", 2000)), modes=modes, pins=pins, **kwargs.pop("emulator", {}))
        src = Esp32Source("emulator", chans, acq, hardware=hw, transport_factory=lambda: Esp32Emulator(ecfg), **kwargs)
        src.info.kind = "esp32-emulator"
        src.info.is_real_hardware = False
        src.info.label = f"emulated ESP32 ({','.join(modes)})"
        return src
    if kind == "esp32":
        from .esp32 import Esp32Source

        return Esp32Source(arg, chans, acq, hardware=hw, **kwargs)
    if kind == "text":
        from .sources import SerialSource

        return SerialSource(arg, chans, acq)
    # bare port name
    proto = acq.get("protocol", "text")
    return open_source(f"{'esp32' if proto == 'emgm2' else 'text'}:{spec}", cfg, data_dir, channels, **kwargs)
