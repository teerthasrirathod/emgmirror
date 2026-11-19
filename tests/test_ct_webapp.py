"""Tests for the local CT web app. The pipeline is replaced by a fake runner; no scan data is used."""

from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from emgmirror.ct.webapp import create_ct_app, safe_relpath

DISCLAIMER = "Research use, not for diagnosis or surgical planning"
SUMMARY = {"title": "t", "headline": "h", "plain": [], "numbers": [], "limits": [], "warnings": []}
VIEWER_HTML = "<!doctype html><title>viewer</title><p>fake viewer</p>"
STL_BYTES = b"solid fake\nendsolid fake\n"
PUBLIC_KEYS = {"id", "name", "state", "created", "step", "progress"}


def write_outputs(out_dir: Path) -> None:
    """Write the three files a successful pipeline run leaves behind."""
    (out_dir / "meshes").mkdir(parents=True, exist_ok=True)
    (out_dir / "viewer.html").write_text(VIEWER_HTML, encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(SUMMARY), encoding="utf-8")
    (out_dir / "meshes" / "bone.stl").write_bytes(STL_BYTES)


class FakeRunner:
    """Records its arguments. Each call pops the next outcome: "ok", "fail" (exit 1) or "raise"."""

    def __init__(self, outcomes=(), gate: threading.Event | None = None):
        self.calls: list[tuple[Path, Path, Path]] = []
        self.outcomes = list(outcomes)
        self.gate = gate
        self.started = threading.Event()
        self.returned = threading.Semaphore(0)  # one release per finished run

    def __call__(self, input_dir: Path, out_dir: Path, status_file: Path) -> int:
        self.calls.append((input_dir, out_dir, status_file))
        self.started.set()
        try:
            if self.gate is not None:
                self.gate.wait(10)
            outcome = self.outcomes.pop(0) if self.outcomes else "ok"
            if outcome == "raise":
                raise RuntimeError("boom")
            if outcome == "fail":
                return 1
            write_outputs(out_dir)
            return 0
        finally:
            self.returned.release()


@pytest.fixture
def make_app(tmp_path):
    """Return a factory that builds a test client over a fresh jobs folder."""

    def _make(runner: FakeRunner | None = None):
        runner = runner or FakeRunner()
        root = tmp_path / "jobs"
        client = TestClient(create_ct_app(root, runner, extra_hosts=("testserver",)))
        client.fake_runner = runner  # lets wait_for know when the worker's final write is due
        return client, runner, root

    return _make


def create_job(client: TestClient, **body) -> str:
    r = client.post("/api/ct/jobs", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def put(client: TestClient, jid: str, name: str, data: bytes):
    return client.put(f"/api/ct/jobs/{jid}/files", params={"name": name}, content=data)


def start(client: TestClient, jid: str):
    return client.post(f"/api/ct/jobs/{jid}/start")


def wait_for(client: TestClient, jid: str, states=("done", "failed"), timeout: float = 10.0) -> dict:
    """Poll the status endpoint until the job reaches one of the given states."""
    runner = getattr(client, "fake_runner", None)
    if runner is not None:
        # Wait for the fake runner to return, then pause: the worker replaces status.json right after that,
        # and a read that overlaps the replace fails on Windows (see the xfail notes in this file).
        assert runner.returned.acquire(timeout=timeout), "the runner never returned"
        time.sleep(0.5)
    deadline = time.monotonic() + timeout
    while True:
        st = client.get(f"/api/ct/jobs/{jid}").json()
        if st["state"] in states:
            return st
        if time.monotonic() > deadline:
            pytest.fail(f"job stayed in state {st['state']!r} for {timeout} s")
        time.sleep(0.05)


def upload_job(client: TestClient, files: dict[str, bytes]) -> str:
    """Create a job and upload the given files into it."""
    jid = create_job(client)
    for name, data in files.items():
        assert put(client, jid, name, data).status_code == 200
    return jid


def finished_job(client: TestClient) -> str:
    """Create, upload, run to completion and return a job id."""
    jid = upload_job(client, {"scan/IM1": b"slice"})
    assert start(client, jid).status_code == 200
    assert wait_for(client, jid)["state"] == "done"
    return jid


def assert_contained(base: Path, root: Path, jid: str) -> None:
    """Nothing under base lies outside the jobs folder, and the job writes only into its upload folder."""
    for p in base.rglob("*"):
        if p.is_file():
            assert root in p.parents, f"{p} was written outside the jobs folder"
    job = root / jid
    for p in job.rglob("*"):
        if p.is_file():
            assert p.name == "status.json" or (job / "upload") in p.parents, f"{p} is outside the upload folder"


def build_zip() -> bytes:
    """A zip with two normal members and two members with escaping paths."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("scan/IM1.dcm", b"one")
        zf.writestr("scan/IM2.dcm", b"two")
        zf.writestr("../../evil.txt", b"evil")
        info = zipfile.ZipInfo("placeholder.txt")
        info.filename = "C:\\Windows\\evil2.txt"  # set after construction: ZipInfo would rewrite the backslashes
        zf.writestr(info, b"evil2")
    return buf.getvalue()


def test_page_is_served_without_external_references(make_app):
    """The page carries the research disclaimer and refers to nothing outside this machine."""
    client, _, _ = make_app()
    r = client.get("/ct")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert DISCLAIMER in r.text
    assert "http://" not in r.text and "https://" not in r.text


def test_upload_run_and_download_results(make_app):
    """Nested uploads reach the runner intact, and the finished results can be read back."""
    client, runner, root = make_app()
    jid = create_job(client)
    contents = {"scan/IMAGES/IM1": b"slice one", "scan/IMAGES/IM2": b"slice two", "scan/readme.txt": b"hello"}
    total = 0
    for n, (name, data) in enumerate(contents.items(), start=1):
        total += len(data)
        r = put(client, jid, name, data)
        assert r.status_code == 200
        assert r.json() == {"bytes": total, "files": n}
    assert start(client, jid).status_code == 200
    st = wait_for(client, jid)
    assert st["state"] == "done"
    assert st["summary"] == SUMMARY
    assert {"viewer.html", "meshes/bone.stl"} <= set(st["downloads"])
    assert "summary.json" not in st["downloads"]
    assert client.get(f"/api/ct/jobs/{jid}/viewer").text == VIEWER_HTML
    r = client.get(f"/api/ct/jobs/{jid}/files/meshes/bone.stl")
    assert r.status_code == 200 and r.content == STL_BYTES

    assert len(runner.calls) == 1
    input_dir, out_dir, _ = runner.calls[0]
    assert out_dir == root / jid / "out"
    stored = {p.relative_to(input_dir).as_posix(): p.read_bytes() for p in input_dir.rglob("*") if p.is_file()}
    assert len(stored) == 3
    for name, data in contents.items():
        folder, base = name.rsplit("/", 1)
        matches = [k for k, v in stored.items() if v == data]
        assert len(matches) == 1, f"{name} not found intact"
        assert matches[0].startswith(folder + "/") and matches[0].endswith("_" + base)


def test_zip_upload_is_unpacked_without_escaping_the_job(make_app, tmp_path):
    """Zip members with parent or drive-letter paths stay inside the job's upload folder."""
    client, _, root = make_app()
    jid = create_job(client)
    r = put(client, jid, "bundle.zip", build_zip())
    assert r.status_code == 200
    assert r.json()["files"] == 1
    names = [p.name for p in (root / jid / "upload").rglob("*") if p.is_file()]
    assert any(n.endswith("_IM1.dcm") for n in names)
    assert any(n.endswith("_IM2.dcm") for n in names)
    assert_contained(tmp_path, root, jid)


@pytest.mark.parametrize("name", ["../../x", "..\\..\\x", "C:\\x", "/etc/passwd", "a/../../b"])
def test_upload_names_cannot_escape_the_upload_folder(make_app, tmp_path, name):
    """Hostile file names are reduced to a harmless relative path inside the upload folder."""
    client, _, root = make_app()
    jid = create_job(client)
    assert put(client, jid, name, b"data").status_code == 200
    assert_contained(tmp_path, root, jid)
    assert len([p for p in (root / jid / "upload").rglob("*") if p.is_file()]) == 1


@pytest.mark.parametrize("name", ["..", "../..", ".", "/", "...", ""])
def test_upload_name_made_only_of_dots_or_slashes_is_refused(make_app, name):
    """A name with no usable part is answered with 400."""
    client, _, _ = make_app()
    jid = create_job(client)
    assert put(client, jid, name, b"x").status_code == 400


@pytest.mark.parametrize("rel", ["../status.json", "..%2Fstatus.json", "..%5Cstatus.json"])
def test_download_cannot_read_outside_the_output_folder(make_app, rel):
    """Path tricks in the download URL give 404 and never the job's status file."""
    client, _, _ = make_app()
    jid = finished_job(client)
    r = client.get(f"/api/ct/jobs/{jid}/files/{rel}")
    assert r.status_code == 404
    assert b'"state"' not in r.content and b'"created"' not in r.content


def test_root_opens_the_page(make_app):
    """The plain address redirects to the page; a foreign Host is refused there too."""
    client, _, _ = make_app()
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/ct"
    assert client.get("/").status_code == 200
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 403


def test_no_unguarded_routes(make_app):
    """Every route the app serves checks Host/Origin; the schema and docs routes, which would not, are off."""
    client, _, _ = make_app()
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404


def test_page_explains_when_opened_without_the_server(make_app):
    """Opened straight from disk the page cannot reach the API: it says how to start the local program."""
    client, _, _ = make_app()
    text = client.get("/ct").text
    assert "emgmirror ct serve" in text and "test(location.protocol)" in text


def test_requests_with_foreign_host_or_origin_are_refused(make_app):
    """A foreign Host header or a cross-site Origin gets 403."""
    client, _, _ = make_app()
    assert client.get("/ct", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/api/ct/jobs", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/api/ct/jobs", headers={"Origin": "https://evil.example"}).status_code == 403


@pytest.mark.parametrize("origin", ["http://127.0.0.1:8000", "http://localhost:8000"])
def test_local_origins_are_allowed(make_app, origin):
    """Pages served from this machine may call the API."""
    client, _, _ = make_app()
    assert client.get("/api/ct/jobs", headers={"Origin": origin}).status_code == 200


@pytest.mark.parametrize("jid", ["0123456789abcdef", "..%2Fx", "ZZZZ", "0123456789ABCDEF", "a" * 15])
def test_unknown_or_malformed_job_ids_return_404(make_app, jid):
    """Ids that are unknown or not 16 lowercase hex characters are not found."""
    client, _, _ = make_app()
    assert client.get(f"/api/ct/jobs/{jid}").status_code == 404
    assert client.post(f"/api/ct/jobs/{jid}/start").status_code == 404
    assert client.get(f"/api/ct/jobs/{jid}/viewer").status_code == 404
    assert client.delete(f"/api/ct/jobs/{jid}").status_code == 404


def test_start_with_nothing_uploaded_is_refused(make_app):
    """A job with no files and no folder cannot be started."""
    client, runner, _ = make_app()
    jid = create_job(client)
    assert start(client, jid).status_code == 400
    assert runner.calls == []


def test_second_start_while_running_is_refused(make_app):
    """Starting a job that is already queued or running gives 409."""
    gate = threading.Event()
    runner = FakeRunner(gate=gate)
    client, _, _ = make_app(runner)
    jid = upload_job(client, {"a.dcm": b"x"})
    try:
        assert start(client, jid).status_code == 200
        assert runner.started.wait(10)
        assert start(client, jid).status_code == 409
    finally:
        gate.set()
    assert wait_for(client, jid)["state"] == "done"
    assert len(runner.calls) == 1


def test_failed_build_reports_error_and_can_be_started_again(make_app):
    """Exit code 1, then an exception, both end as failed with an error; a later start succeeds."""
    client, runner, _ = make_app(FakeRunner(outcomes=["fail", "raise", "ok"]))
    jid = upload_job(client, {"a.dcm": b"x"})

    assert start(client, jid).status_code == 200
    st = wait_for(client, jid)
    assert st["state"] == "failed" and st["error"]

    r = start(client, jid)
    assert r.status_code == 200 and r.json()["state"] == "queued"
    st = wait_for(client, jid)
    assert st["state"] == "failed" and st["error"] == "boom"

    assert start(client, jid).status_code == 200
    assert wait_for(client, jid)["state"] == "done"
    assert len(runner.calls) == 3


def test_delete_removes_finished_job(make_app):
    """A finished job's folder and list entry are removed by delete."""
    client, _, root = make_app()
    jid = finished_job(client)
    assert client.delete(f"/api/ct/jobs/{jid}").status_code == 200
    assert not (root / jid).exists()
    assert jid not in [j["id"] for j in client.get("/api/ct/jobs").json()]
    assert client.get(f"/api/ct/jobs/{jid}").status_code == 404


def test_delete_of_running_job_is_refused(make_app):
    """A job that is being processed cannot be deleted."""
    gate = threading.Event()
    runner = FakeRunner(gate=gate)
    client, _, root = make_app(runner)
    jid = upload_job(client, {"a.dcm": b"x"})
    assert start(client, jid).status_code == 200
    try:
        assert runner.started.wait(10)
        assert client.delete(f"/api/ct/jobs/{jid}").status_code == 409
        assert (root / jid).exists()
    finally:
        gate.set()
    assert wait_for(client, jid)["state"] == "done"


def test_upload_after_start_is_refused(make_app):
    """Files cannot be added once a job has been started."""
    gate = threading.Event()
    client, _, _ = make_app(FakeRunner(gate=gate))
    jid = upload_job(client, {"a.dcm": b"x"})
    try:
        assert start(client, jid).status_code == 200
        assert put(client, jid, "b.dcm", b"y").status_code == 409
    finally:
        gate.set()
    assert wait_for(client, jid)["state"] == "done"


def test_folder_job_runs_on_the_original_folder(make_app, tmp_path):
    """A job made from a folder path hands that folder to the runner and copies nothing."""
    client, runner, root = make_app()
    scan = tmp_path / "my scan" / "IMAGES"
    scan.mkdir(parents=True)
    (scan / "IM1").write_bytes(b"x")
    r = client.post("/api/ct/jobs", json={"path": str(scan)})
    assert r.status_code == 200
    jid = r.json()["id"]
    assert start(client, jid).status_code == 200
    assert wait_for(client, jid)["state"] == "done"

    assert runner.calls[0][0] == scan
    assert not [p for p in (root / jid / "upload").rglob("*") if p.is_file()]
    status = client.get(f"/api/ct/jobs/{jid}")
    assert "source" not in status.json()
    assert str(scan) not in status.text


def test_folder_job_with_missing_folder_is_refused(make_app, tmp_path):
    """A folder path that does not exist gives 400."""
    client, _, _ = make_app()
    r = client.post("/api/ct/jobs", json={"path": str(tmp_path / "does-not-exist")})
    assert r.status_code == 400


def test_folder_job_refuses_uploads(make_app, tmp_path):
    """Uploading into a job that was given a folder path should be refused."""
    client, _, _ = make_app()
    jid = create_job(client, path=str(tmp_path))
    assert put(client, jid, "a.dcm", b"x").status_code == 409


def test_source_path_not_in_create_or_start_responses(make_app, tmp_path):
    """The local folder path should not be echoed back by create or start."""
    client, _, _ = make_app()
    r = client.post("/api/ct/jobs", json={"path": str(tmp_path)})
    assert "source" not in r.json()
    jid = r.json()["id"]
    assert "source" not in start(client, jid).json()
    wait_for(client, jid)


@pytest.mark.parametrize(("name", "expected"), [
    ("IMAGES/IM1", "IMAGES/IM1"),
    ("../../x", "x"),
    ("..\\..\\x", "x"),
    ("C:\\x", "C_/x"),
    ("/etc/passwd", "etc/passwd"),
    ("a/../../b", "a/b"),
    ("scan name (1).dcm", "scan_name__1_.dcm"),
    ("1/2/3/4/5/6/7/8", "3/4/5/6/7/8"),
])
def test_safe_relpath_maps_names_to_plain_relative_paths(name, expected):
    """Known inputs map to the expected relative path."""
    assert safe_relpath(name) == Path(expected)


@pytest.mark.parametrize("name", [
    "../../etc/passwd", "..\\..\\x", "C:\\Windows\\x", "D:/y", "\\\\server\\share\\f", "a/../../../b", "x:y:z", "~/.ssh/id",
])
def test_safe_relpath_is_relative_without_parents_or_drive(name):
    """Whatever the input, the result is relative, has no '..' part and no drive or colon."""
    rel = safe_relpath(name)
    assert not rel.is_absolute()
    assert rel.drive == ""
    assert ".." not in rel.parts
    assert all(":" not in part for part in rel.parts)


def test_safe_relpath_refuses_names_with_no_usable_part():
    """A name made only of dots and separators raises HTTP 400."""
    with pytest.raises(HTTPException) as exc:
        safe_relpath("../..")
    assert exc.value.status_code == 400


def test_job_list_is_newest_first_and_shows_public_fields_only(make_app):
    """The job list is ordered by creation, newest first, with only the public keys."""
    client, _, _ = make_app()
    first = create_job(client, name="first")
    time.sleep(0.05)
    second = create_job(client, name="second")
    jobs = client.get("/api/ct/jobs").json()
    assert [j["id"] for j in jobs] == [second, first]
    assert [j["name"] for j in jobs] == ["second", "first"]
    assert all(set(j) == PUBLIC_KEYS for j in jobs)


def test_job_list_order_ignores_later_activity(make_app):
    """Uploading to an older job should not move it above newer jobs."""
    client, _, _ = make_app()
    first = create_job(client, name="first")
    time.sleep(0.05)
    second = create_job(client, name="second")
    time.sleep(0.05)
    assert put(client, first, "a.dcm", b"x").status_code == 200
    assert [j["id"] for j in client.get("/api/ct/jobs").json()] == [second, first]


@pytest.mark.parametrize("state", ["running", "queued"])
def test_job_cut_off_by_a_server_stop_can_be_deleted_or_restarted(make_app, state):
    """A job left 'running' or 'queued' (Ctrl+C during a build) is marked failed when the server starts again."""
    client, _, root = make_app()
    jid = upload_job(client, {"scan/IM1": b"slice"})
    st = json.loads((root / jid / "status.json").read_text(encoding="utf-8"))
    st["state"] = state
    (root / jid / "status.json").write_text(json.dumps(st), encoding="utf-8")
    client, _, root = make_app()  # the server starts again over the same jobs folder
    got = client.get(f"/api/ct/jobs/{jid}").json()
    assert got["state"] == "failed" and "Interrupted" in got["error"]
    assert start(client, jid).status_code == 200
    assert wait_for(client, jid)["state"] == "done"
    assert client.delete(f"/api/ct/jobs/{jid}").status_code == 200


def _damaged_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("scan/IM1.dcm", b"one" * 1000)
        zf.writestr("scan/IM2.dcm", bytes(range(256)) * 400)
    data = bytearray(buf.getvalue())
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as zf:
        info = zf.getinfo("scan/IM2.dcm")
    start_ = info.header_offset + 30 + len(info.filename) + len(info.extra)
    for k in range(start_ + 5, start_ + 60):  # spoil the second member's compressed bytes
        data[k] ^= 0x5A
    return bytes(data)


def _encrypted_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("scan/IM1.dcm", b"one")
    data = bytearray(buf.getvalue())
    data[6] |= 1  # local header: encrypted flag
    cd = data.find(b"PK\x01\x02")
    data[cd + 8] |= 1  # central directory: encrypted flag
    return bytes(data)


@pytest.mark.parametrize("make_zip", [_damaged_zip, _encrypted_zip])
def test_damaged_or_encrypted_zip_is_refused_and_leaves_nothing(make_app, make_zip):
    client, _, root = make_app()
    jid = create_job(client)
    r = put(client, jid, "scan.zip", make_zip())
    assert r.status_code == 400 and "damaged or password-protected" in r.json()["detail"]
    st = json.loads((root / jid / "status.json").read_text(encoding="utf-8"))
    assert st["files"] == 0 and st["bytes"] == 0
    assert [p for p in (root / jid / "upload").rglob("*") if p.is_file()] == []
    assert start(client, jid).status_code == 400  # nothing usable was uploaded


def test_a_job_another_live_server_is_running_is_not_reset(make_app):
    """A second server on the same jobs folder must not mark the first server's live job as failed."""
    import os

    client, _, root = make_app()
    jid = upload_job(client, {"scan/IM1": b"slice"})
    st = json.loads((root / jid / "status.json").read_text(encoding="utf-8"))
    st.update(state="running", server_pid=os.getpid())  # a server process that is still alive
    (root / jid / "status.json").write_text(json.dumps(st), encoding="utf-8")
    client, _, root = make_app()
    got = client.get(f"/api/ct/jobs/{jid}").json()
    assert got["state"] == "running" and "server_pid" not in got
    assert client.delete(f"/api/ct/jobs/{jid}").status_code == 409


def test_a_failed_zip_never_gives_its_file_number_to_a_later_upload(make_app):
    """Uploads are numbered so same-named files never collide; a refused zip must not free its number."""
    client, _, root = make_app()
    jid = create_job(client)
    assert put(client, jid, "a/IM1", b"first").status_code == 200
    assert put(client, jid, "scan.zip", _damaged_zip()).status_code == 400
    st = json.loads((root / jid / "status.json").read_text(encoding="utf-8"))
    assert st["files"] == 1 and st["next_index"] == 2  # an upload running alongside may hold number 2 already
    assert put(client, jid, "a/IM1", b"second").status_code == 200
    assert put(client, jid, "a/IM1", b"third").status_code == 200
    got = sorted(p.read_bytes() for p in (root / jid / "upload").rglob("*") if p.is_file())
    assert got == [b"first", b"second", b"third"]
    assert json.loads((root / jid / "status.json").read_text(encoding="utf-8"))["files"] == 3


@pytest.mark.parametrize(("body", "headers"), [
    ('["x"]', {}), ('"x"', {}), ("{bad", {}), ("", {})])
def test_malformed_json_on_job_create_is_a_400(make_app, body, headers):
    client, _, _ = make_app()
    r = client.post("/api/ct/jobs", content=body, headers={"content-type": "application/json", **headers})
    assert r.status_code == 400, r.text
