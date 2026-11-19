import copy

import pytest

from emgmirror.config import load_config
from emgmirror.simulate.synthetic import synthetic_session


@pytest.fixture(scope="session")
def cfg():
    """Default config with a shorter protocol and smaller pools so tests run fast."""
    c = copy.deepcopy(load_config())
    c["simulation"]["motor_units_per_muscle"] = 80
    c["protocol_rest_between_s"] = 1.5
    short = {"rest": 3, "grip_ramp": 6, "grip_low_hold": 8, "open_close": 4}
    for step in c["protocol"]:
        step["duration_s"] = short.get(step["task"], 3)
        if step["task"] == "open_close":
            step["reps"] = 2
    return c


@pytest.fixture(scope="session")
def mirror_session(cfg):
    return synthetic_session(cfg, mode="mirror", session_id="test-mirror")


@pytest.fixture(scope="session")
def sequential_session(cfg):
    return synthetic_session(cfg, mode="sequential", session_id="test-sequential")
