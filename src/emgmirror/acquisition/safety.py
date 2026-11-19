"""Host-side safety checks that software can actually make.

Software cannot make a body-connected rig safe (see docs/ELECTRICAL_SAFETY.md). It can refuse to
start a person recording when the laptop is visibly connected to mains power, which is the most
common way the isolation that the gate relies on gets lost.
"""

from __future__ import annotations

import sys
import threading
import time

SUPPORTED_PLATFORMS = ("Windows (GetSystemPowerStatus)", "macOS (pmset -g batt)",
                       "Linux with /sys/class/power_supply/*/online (most laptops)")


def _macos_ac_power() -> bool | None:
    import subprocess

    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    # First line: "Now drawing from 'AC Power'" / "'Battery Power'" / "'UPS Power'" (a UPS is mains too).
    if "'Battery Power'" in out:
        return False
    if "'AC Power'" in out or "'UPS Power'" in out:
        return True
    return None


def ac_power_connected() -> bool | None:
    """True if the computer reports mains (AC) power, False on battery, None if unknown."""
    if sys.platform == "darwin":
        return _macos_ac_power()
    if sys.platform == "win32":
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

        s = Status()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            return None
        return {0: False, 1: True}.get(s.ACLineStatus)
    try:  # Linux
        from pathlib import Path

        supplies = list(Path("/sys/class/power_supply").glob("*/online"))
        if supplies:
            return any(p.read_text().strip() == "1" for p in supplies)
    except OSError:
        pass
    return None


def check_before_person_recording() -> list[str]:
    """Problems that must block a recording with electrodes on a person. Empty list = none found."""
    problems = []
    ac = ac_power_connected()
    if ac is True:
        problems.append("the computer reports mains (AC) power: unplug the charger. The safety gate requires "
                        "battery-only operation with every other mains-connected cable unplugged.")
    elif ac is None:
        # Deliberately no override: battery-only operation is an electrical-safety condition for a
        # person wearing electrodes, and software that cannot see the power state cannot vouch for it.
        problems.append("could not read the power status on this computer, so battery-only operation cannot be "
                        "confirmed and the recording stays blocked (an electrical-safety condition for electrodes "
                        f"on a person; there is no override). Supported: {'; '.join(SUPPORTED_PLATFORMS)}. "
                        "See docs/ACQUISITION_SETUP.md.")
    return problems


ABORT_REASON = "mains (AC) power connected during the recording"


def start_power_watch(acq, interval_s: float = 1.0) -> threading.Thread:
    """Watch the power state while `acq` runs. When mains power appears, set `acq.abort_reason` (the
    guided recorder and the dataset session stop on it) and mark it in the raw recording.

    Mains appearing mid-recording breaks the isolation the safety gate relies on. Call after
    `acq.start()`; the thread ends when the acquisition stops or an abort is set.
    """

    def watch():
        while acq.started and not getattr(acq, "abort_reason", None):
            if ac_power_connected():
                acq.abort_reason = ABORT_REASON
                acq.mark("abort", reason=acq.abort_reason)
                break
            time.sleep(interval_s)

    t = threading.Thread(target=watch, name="power-watch", daemon=True)
    t.start()
    return t
