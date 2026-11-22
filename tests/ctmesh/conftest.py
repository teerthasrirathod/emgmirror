import numpy as np
import pytest

pytest.importorskip("vtk")
pytest.importorskip("meshoptimizer")
pytest.importorskip("manifold3d")

from emgmirror.ctmesh import fixtures  # noqa: E402
from emgmirror.ctmesh.contract import save_labelmap  # noqa: E402

# a gantry-tilt-like oblique grid, and an improper (det -1) one as NIfTI flips can produce
DIRECTIONS = {
    "oblique": fixtures.rotation((0.3, 1.0, 0.2), 25.0) @ fixtures.rotation((1, 0, 0), 10.0),
    "improper": fixtures.rotation((1, 0.5, 0), 40.0) @ np.diag([1.0, -1.0, 1.0]),
}


@pytest.fixture(scope="session", params=list(DIRECTIONS))
def phantom(request):
    return fixtures.oblique_phantom(shape=(40, 56, 56), direction=DIRECTIONS[request.param])


@pytest.fixture(scope="session")
def oblique():
    return fixtures.oblique_phantom(shape=(40, 56, 56), direction=DIRECTIONS["oblique"])


@pytest.fixture(scope="session")
def pipeline_out(tmp_path_factory, oblique):
    from emgmirror.ctmesh.pipeline import run
    d = tmp_path_factory.mktemp("ctmesh")
    save_labelmap(oblique.labelmap, d / "in")
    manifest = run(d / "in" / "labels.nii.gz", d / "out", log=lambda *a: None)
    return d / "out", manifest
