from fastapi.testclient import TestClient

from emgmirror.server.app import create_app


def test_api_flow(tmp_path, cfg, mirror_session):
    mirror_session.save(tmp_path)
    client = TestClient(create_app(data_dir=tmp_path, raw_dir=tmp_path / "raw"))
    assert client.get("/").status_code == 200
    sessions = client.get("/api/sessions").json()
    assert sessions[0]["session_id"] == "test-mirror"

    res = client.get("/api/sessions/test-mirror/analysis").json()
    assert "summary" in res and res["is_synthetic"]

    anat = client.get("/api/sessions/test-mirror/anatomy").json()
    assert len(anat["figure"]["data"]) == len(anat["traces"])

    assert client.get("/api/sessions/nope/analysis").status_code == 404
    assert client.get("/api/sessions/..%2F..%2Fetc/analysis").status_code == 404


def test_live_websocket(tmp_path, mirror_session):
    mirror_session.save(tmp_path)
    client = TestClient(create_app(data_dir=tmp_path, raw_dir=tmp_path / "raw"))
    with client.websocket_connect("/ws/live?session=test-mirror") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and len(hello["channels"]) == 4
        data = ws.receive_json()
        assert data["type"] == "data" and len(data["envelope"]) == 4
