"""Simulated-hardware tests: Esp32Source against the EMGM/2 emulator.

Deterministic: the emulator runs on a fake clock and bytes are fed to the source directly, so no
thread timing is involved. These results say nothing about the physical board (see tests/physical).
"""

import time

import numpy as np
import pytest

from emgmirror.acquisition import emgm2
from emgmirror.acquisition.emulator import EmulatorConfig, Esp32Emulator
from emgmirror.acquisition.esp32 import Esp32Source, HandshakeError
from emgmirror.acquisition.pipeline import Acquisition
from emgmirror.storage import Channel

CH3 = [Channel("flex", "right", "flexors"), Channel("ext", "right", "extensors"),
       Channel("brach", "right", "brachioradialis")]
ACQ = {"fs": 2000, "amp_gain": 2420.0, "serial_baud": 500000, "mains_hz": 50}
HW = {"channels": [{"ch": 1, "gpio": 35}, {"ch": 2, "gpio": 32}, {"ch": 3, "gpio": 33}]}


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def rig(seconds=1.0, channels=CH3, hardware=HW, **ecfg):
    """Emulator + source wired synchronously. Returns (source, emulator, clock, chunks)."""
    clock = Clock()
    emu = Esp32Emulator(EmulatorConfig(**ecfg), clock=clock, realtime=False)
    src = Esp32Source("SIM", channels, ACQ, hardware=hardware, transport_factory=lambda: emu)
    src.ser = emu
    src.process_bytes(emu.read(10**8))  # the HELLO the device sent on boot
    chunks = []
    if seconds:
        clock.t += seconds
        src.process_bytes(emu.read(10**8))
        chunks = src.read_chunks()
    return src, emu, clock, chunks


def cat(chunks, attr):
    return np.concatenate([getattr(c, attr) for c in chunks])


def test_three_independent_channels_and_units():
    src, _, _, chunks = rig(1.0, modes=("emg", "flat_mid", "saturated"))
    assert src.hello["proto"] == "EMGM/2" and src.info.fs_nominal == 2000
    pin = cat(chunks, "pin_mv")
    assert pin.shape == (2000, 3)
    assert 1500 < pin[:, 0].mean() < 1800 and pin[:, 0].std() > 5  # varying around mid-rail
    assert pin[:, 1] == pytest.approx(1650, abs=1)  # constant
    assert pin[:, 2].mean() > 3200  # pinned at full scale
    mv = cat(chunks, "mv")
    assert mv[:, 1] == pytest.approx(0.0, abs=0.001)  # (1650 - bias 1650) / gain
    raw = cat(chunks, "raw")
    assert raw.dtype == np.uint16 and raw[:, 2].max() == 4095 * 4  # raw sums kept unconverted
    assert "NOMINAL" in src.info.calibration["mv"]


def test_index_is_contiguous_and_device_rate_is_measured():
    src, _, _, chunks = rig(2.0)
    idx = cat(chunks, "index")
    assert np.array_equal(idx, np.arange(len(idx)))
    t = np.array([c.t_device_us for c in chunks])
    assert (np.diff(t) > 0).all()
    rate = (idx[-1] + 1 - (chunks[0].index[-1] + 1)) / ((t[-1] - t[0]) / 1e6)
    assert rate == pytest.approx(2000, rel=1e-6)
    assert all(c.gap_before == 0 for c in chunks)


def test_device_side_drop_is_exact_and_attributed():
    src, _, _, chunks = rig(1.0, drop_device_frames={20, 21})
    gaps = [(c.index[0], c.gap_before, c.gap_cause) for c in chunks if c.gap_before]
    assert gaps == [(220, 20, "device")]
    assert src.device_dropped == 20 and src.link_lost == 0


def test_link_loss_and_corruption_are_attributed_to_the_link():
    src, _, _, chunks = rig(1.0, drop_link_frames={30}, corrupt_frames={60})
    gaps = [(int(c.index[0]), c.gap_before, c.gap_cause) for c in chunks if c.gap_before]
    assert gaps == [(310, 10, "link"), (610, 10, "link")]
    assert src.link_lost == 20 and src.link_lost_frames == 2 and src.device_dropped == 0
    assert src.decoder.crc_errors == 1


def test_irregular_timestamps_do_not_disturb_indexing():
    src, _, _, chunks = rig(2.0, t_jitter_us=300.0, clock_ppm=50.0)
    idx = cat(chunks, "index")
    assert np.array_equal(idx, np.arange(len(idx)))
    t = np.array([c.t_device_us for c in chunks], dtype=float)
    n_end = np.array([c.index[-1] + 1 for c in chunks], dtype=float)
    slope = np.polyfit(t / 1e6, n_end, 1)[0]  # least squares is robust to the jitter
    assert slope == pytest.approx(2000 / (1 + 50e-6), rel=2e-4)


def test_invalid_samples_are_marked_and_zero_filled_for_live_use():
    src, emu, clock, _ = rig(0, modes=("emg", "invalid", "emg"))
    acq = Acquisition(src, {"acquisition": ACQ})
    clock.t += 0.5
    src.process_bytes(emu.read(10**8))
    out = acq.read()
    assert out.shape == (1000, 3)
    assert (out[:, 1] == 0).all()
    assert acq.monitor.invalid == 1000
    assert acq.monitor.channel_report()[1]["state"] == "fail"


def test_missing_channel_in_firmware_is_refused_without_a_configured_pin_map():
    src, _, _, chunks = rig(0.5, hardware={}, modes=("emg", "emg"), pins=(35, 32))
    assert chunks == [] and src.pre_hello_frames > 0
    assert src.error.startswith("config mismatch") and "2 channels" in src.error


def test_pin_map_mismatch_fails_the_handshake_when_not_applied():
    clock = Clock()
    emu = Esp32Emulator(EmulatorConfig(pins=(36, 39, 34)), clock=clock, realtime=False)
    src = Esp32Source("SIM", CH3, ACQ, hardware=HW, transport_factory=lambda: emu, handshake_timeout_s=1,
                      apply_pins=False)
    with pytest.raises(HandshakeError, match="pins"):
        src.start()


def test_configured_pin_map_is_applied_to_the_device():
    clock = Clock()
    emu = Esp32Emulator(EmulatorConfig(pins=(36, 39), modes=("emg", "emg"),
                                       pin_modes={35: "dc240", 32: "dc2680", 33: "dc2860"}), clock=clock, realtime=False)
    src = Esp32Source("SIM", CH3, ACQ, hardware=HW, transport_factory=lambda: emu)
    src.ser = emu
    src.process_bytes(emu.read(10**8))  # boot HELLO (2 channels, wrong pins) -> host sends PINS
    assert "PINS 35,32,33" in emu.commands
    clock.t += 0.2
    src.process_bytes(emu.read(10**8))
    assert src.error is None and src.hello["pins"] == "35,32,33"
    pin = cat(src.read_chunks(), "pin_mv")
    assert pin.shape[1] == 3 and pin.mean(axis=0) == pytest.approx([240, 2680, 2860], abs=5)


def test_pin_scan_tells_driven_from_floating():
    from emgmirror.acquisition import pinscan

    wiring = {32: "floating", 33: "floating", 34: "dc150", 35: "floating", 36: "dc2150", 39: "dc750"}
    pins = [32, 33, 34, 35, 36, 39]
    res = pinscan.scan("SIM", ACQ, pins, seconds=0.4, rate=1000,
                       factory=lambda: Esp32Emulator(EmulatorConfig(pins=(35, 32, 33), modes=("floating",) * 3,
                                                                    pin_modes=wiring)))
    verdicts = {r["gpio"]: r["verdict"] for r in res["pins"]}
    assert verdicts == {32: "floating", 33: "floating", 34: "driven", 35: "floating", 36: "driven", 39: "driven"}
    assert {r["gpio"]: r["mean_mv"] for r in res["pins"] if r["verdict"] == "driven"} == pytest.approx(
        {34: 150, 36: 2150, 39: 750}, abs=5)


def test_old_text_firmware_fails_the_handshake_with_a_clear_message():
    emu = Esp32Emulator(EmulatorConfig(text_firmware=True))
    src = Esp32Source("SIM", CH3, ACQ, transport_factory=lambda: emu, handshake_timeout_s=0.5)
    with pytest.raises(HandshakeError, match="different firmware"):
        src.start()
    assert not src._run_thread and emu.closed  # the failed start released the port


def test_index_unwraps_across_32_bits():
    src, _, _, _ = rig(0)
    dec_bytes = b""
    raw = np.full((10, 3), 6600, dtype=np.uint16)
    for k, first in enumerate([2**32 - 20, 2**32 - 10, 0, 10]):
        dec_bytes += emgm2.encode_data(k + 1000, first, 1000 * (k + 1), 0, raw, 4)
    src.process_bytes(dec_bytes)
    idx = cat(src.read_chunks(), "index")
    assert np.array_equal(idx, np.arange(2**32 - 20, 2**32 + 20))


def test_rate_command_starts_a_new_run():
    src, emu, clock, _ = rig(0.2)
    src.send_command("RATE 1000")
    clock.t += 0.5
    src.process_bytes(emu.read(10**8))
    chunks = src.read_chunks()
    assert src.info.fs_nominal == 1000 and src.run == 2
    run2 = [c for c in chunks if c.run == 2]
    assert run2 and run2[0].index[0] == 0 and run2[0].gap_before == 0
    assert any(a.get("cmd") == "RATE" and a.get("ok") == "1" for a in src.acks)


def test_disconnect_is_reported_and_recording_marked_interrupted(tmp_path):
    from emgmirror.recording import RawRecorder, RawRecording

    # The emulator's clock starts when the port is opened. The disconnect comes late enough that a
    # heavily loaded machine still completes the handshake first.
    emus = []
    src = Esp32Source("SIM", CH3, ACQ, hardware=HW, auto_reconnect=False,
                      transport_factory=lambda: emus.append(Esp32Emulator(EmulatorConfig(disconnect_after_s=1.5)))
                      or emus[-1])
    acq = Acquisition(src, {"acquisition": ACQ})
    acq.start()
    rec = RawRecorder(tmp_path, src.info, label="dc")
    acq.start_recording(rec)
    t0 = time.time()
    while src.status()["connected"] and time.time() - t0 < 20:
        acq.read()
        time.sleep(0.02)
    acq.read()
    assert not src.status()["connected"] and "transport lost" in src.status()["error"]
    report = acq.report()
    assert report["stages"]["connection"]["state"] == "fail"
    acq.stop_recording()
    acq.stop()
    r = RawRecording.open(rec.dir)
    assert r.manifest["status"] == "interrupted" and r.interrupted
    assert 0 < len(r.records) <= 1.5 * 2000 + 10


def test_reconnect_after_device_reset_starts_a_new_run():
    clock = Clock()
    emus = [Esp32Emulator(EmulatorConfig(), clock=clock, realtime=False)]
    src = Esp32Source("SIM", CH3, ACQ, hardware=HW, transport_factory=lambda: emus[-1])
    src.ser = emus[0]
    clock.t += 0.3
    src.process_bytes(emus[0].read(10**8))
    first = src.read_chunks()
    # USB replug resets the ESP32: same run id "1", index back to 0.
    emus.append(Esp32Emulator(EmulatorConfig(), clock=clock, realtime=False))
    src.ser = emus[-1]
    src.decoder = emgm2.StreamDecoder()
    clock.t += 0.3
    src.process_bytes(emus[-1].read(10**8))
    second = src.read_chunks()
    assert first[-1].run == 1 and second[0].run == 2
    assert second[0].index[0] == 0 and all(c.gap_before == 0 for c in second)


def test_acquisition_read_fills_gaps_to_keep_time():
    src, emu, clock, _ = rig(0, drop_device_frames={5})
    acq = Acquisition(src, {"acquisition": ACQ})
    clock.t += 0.1
    src.process_bytes(emu.read(10**8))
    out = acq.read()
    assert out.shape[0] == 200  # 0.1 s at 2 kHz, including 10 filled samples
    assert acq.fills == [{"at_delivered_sample": 50, "device_index": 60, "missing": 10, "filled": 10,
                          "cause": "device"}]
    assert (out[50:60] == 0).all()
