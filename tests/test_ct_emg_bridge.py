"""EMG-on-CT geometry with known answers: synthetic forearm made of cylinders."""

import numpy as np
import pytest
import trimesh

from emgmirror.ct import emg_bridge as b


def cyl(radius, z0, z1, x=0.0, y=0.0, sections=64):
    m = trimesh.creation.cylinder(radius=radius, segment=[[x, y, z0], [x, y, z1]], sections=sections)
    return m


def left_forearm_anatomical():
    """LPS frame (x = patient left, y = posterior, z = head). Left arm hanging, palm forward:
    hand below the elbow (distal = -z), radius lateral (+x) of the ulna, anterior (palm) = -y."""
    return {
        "radius": cyl(6, -250, -10, x=+12),
        "ulna": cyl(6, -250, -10, x=-12),
        "humerus_distal": cyl(10, 0, 80),
        "carpals_hand": cyl(15, -320, -260),
        "muscle": cyl(30, -240, -20),
        "skin": cyl(36, -260, -5),
    }


def test_ray_hits_matches_a_known_box():
    box = trimesh.creation.box(extents=[10, 10, 10])
    d = b.ray_hits(box, np.array([0.0, 0.0, -20.0]), np.array([0.0, 0.0, 1.0]))
    assert d == pytest.approx([15.0, 25.0])


def test_palm_side_and_depths_for_a_left_forearm():
    m = left_forearm_anatomical()
    fr = b.forearm_frame(m, "left")
    assert fr["axis"] == pytest.approx([0, 0, -1], abs=1e-3)  # toward the hand
    assert fr["volar"] == pytest.approx([0, -1, 0], abs=1e-3)  # anterior = palm side
    assert fr["length_mm"] == pytest.approx(240, abs=1)
    sites = b.place_sites(m, fr, [{"ch": 1, "name": "a", "muscle_group": "flexors", "fraction_from_elbow": 1 / 3},
                                  {"ch": 2, "name": "b", "muscle_group": "extensors", "fraction_from_elbow": 1 / 3}])
    flex, ext = sites
    assert flex["skin_point_mm"][1] < -30 and ext["skin_point_mm"][1] > 30  # front vs back
    assert flex["skin_point_mm"][2] == pytest.approx(-10 - 240 / 3, abs=1)  # a third of the way from the elbow
    assert flex["fat_mm"] == pytest.approx(6.0, abs=0.3)  # 36 mm skin - 30 mm muscle
    # the axis runs between the bones; straight in from the front the ray passes between them to the back skin
    assert ext["fat_mm"] == pytest.approx(6.0, abs=0.3)


def test_right_arm_rule_and_mirror():
    m = left_forearm_anatomical()
    mirrored = {k: b.mirror(v) for k, v in m.items()}  # a right forearm, anatomical position
    fr = b.forearm_frame(mirrored, "right")
    assert fr["volar"] == pytest.approx([0, -1, 0], abs=1e-3)
    assert b.mirror(m["skin"]).is_winding_consistent and b.mirror(m["skin"]).volume > 0  # faces flipped with x


def test_compartments_split_front_and_back():
    m = left_forearm_anatomical()
    fr = b.forearm_frame(m, "left")
    muscle = m["muscle"].subdivide_to_size(max_edge=5.0)  # vertices along the length, like a CT mesh
    comp = b.compartments(muscle, fr)
    assert len(comp["volar"].faces) > 100 and len(comp["dorsal"].faces) > 100
    empty = b.compartments(m["muscle"], fr)  # caps only: nothing inside the band, but no crash
    assert len(empty["volar"].faces) == 0
    assert comp["volar"].vertices[:, 1].mean() < 0 < comp["dorsal"].vertices[:, 1].mean()
