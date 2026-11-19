"""Physical hardware tests: run ONLY against a connected ESP32 with firmware/emg_esp32_v2.

    set EMGMIRROR_PORT=COM9            (PowerShell: $env:EMGMIRROR_PORT = "COM9")
    pytest tests/physical -m hardware -v

Skipped otherwise, so a green run without a board never counts as a hardware pass. No electrodes are
needed or wanted: these tests check the device, the link and the pins, not EMG. Each test writes
its measured numbers to docs/test_results/physical/ so the record survives the terminal.
"""

import datetime
import json
import os
import time
from pathlib import Path

import pytest

from emgmirror.acquisition.esp32 import Esp32Source
from emgmirror.acquisition.pipeline import Acquisition
from emgmirror.config import REPO_ROOT, load_config
from emgmirror.acquisition.registry import channels_from_config

PORT = (os.environ.get("EMGMIRROR_PORT") or "").strip() or None
pytestmark = [pytest.mark.hardware,
              pytest.mark.skipif(not PORT, reason="no board: set EMGMIRROR_PORT to run physical tests")]
OUT = REPO_ROOT / "docs" / "test_results" / "physical"


def save(name: str, data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.date.today().isoformat()
    data = {"date": datetime.datetime.now().isoformat(timespec="seconds"), "port": PORT, **data}
    (OUT / f"{stamp}_pytest_{name}.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


@pytest.fixture(scope="module")
def cfg():
    return load_config(REPO_ROOT / "configs" / "esp32_candy.yaml")


@pytest.fixture()
def acq(cfg):
    src = Esp32Source(PORT, channels_from_config(cfg), cfg["acquisition"], hardware=cfg.get("hardware"))
    a = Acquisition(src, cfg)
    a.start()
    yield a
    a.stop()


def run_for(a: Acquisition, seconds: float) -> None:
    t0 = time.time()
    while time.time() - t0 < seconds:
        a.read()
        time.sleep(0.02)


def test_handshake_identifies_the_device(acq):
    d = acq.src.hello
    save("handshake", {"hello": d})
    assert d["proto"] == "EMGM/2"
    assert d["chip"].startswith("ESP32")
    want = ",".join(str(c["gpio"]) for c in acq.src.hardware["channels"])
    assert d["pins"] == want and d["nch"] == str(len(acq.channels))


def test_rate_and_integrity_over_20_s(acq):
    run_for(acq, 20.0)
    r = acq.report()
    st = acq.src.status()
    save("integrity_20s", {"rates": r["rates"], "counts": r["counts"], "decoder": st["decoder"],
                           "device_status": st["device_status"], "stages": r["stages"]})
    assert r["counts"]["missing"] == 0, r["counts"]
    assert r["counts"]["invalid"] == 0
    assert st["decoder"]["crc_errors"] == 0 and st["decoder"]["frame_errors"] == 0
    assert r["rates"]["device_clock_hz"] == pytest.approx(acq.fs, rel=0.005)
    assert r["rates"]["host_window_hz"] == pytest.approx(acq.fs, rel=0.01)


def test_rate_command_changes_the_measured_rate(acq):
    acq.src.send_command("RATE 1000")
    time.sleep(0.5)
    acq.read()
    acq.monitor.__init__(acq.src, mains_hz=50)  # fresh statistics for the new run
    run_for(acq, 5.0)
    r1000 = acq.report()["rates"]
    acq.src.send_command("RATE 2000")
    time.sleep(0.5)
    save("rate_command", {"rates_at_1000": r1000, "hello_after": acq.src.hello})
    assert acq.src.hello["fs"] == "2000"
    assert r1000["device_clock_hz"] == pytest.approx(1000, rel=0.005)


def test_pin_levels_without_electrodes(acq):
    """Records what each pin reads with nothing on the body. No pass/fail on the levels themselves."""
    run_for(acq, 3.0)
    chans = acq.monitor.channel_report()
    save("pin_levels", {"channels": chans, "note": "no electrodes connected"})
    assert len(chans) == 3
