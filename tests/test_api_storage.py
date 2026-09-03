import json

import pytest

from rehab_simulator.app import create_app
from rehab_simulator.storage import DEFAULTS, EventLog, SettingsStore, validate_settings


@pytest.fixture
def client(service):
    return create_app({"TESTING": True}, service).test_client()


def test_index_and_default_state(client):
    response = client.get("/")
    assert response.status_code == 200 and "機台測試台" in response.text
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    state = client.get("/api/state").json
    assert state["connection"]["status"] == "disconnected"
    assert not state["connection"]["desired"]
    assert state["simulation"]["last_sent"]["rpm"] is None


@pytest.mark.parametrize("value", [True, 1.5, "40", None, -1, 256, float("nan"), float("inf")])
def test_api_invalid_number_no_partial_mutation(client, service, value):
    assert (
        client.put("/api/simulation", json={"reader_ready": True, "rpm": value}).status_code == 400
    )
    assert not service.model.reader_ready


def test_settings_save_revision_lock_and_reload(client, service):
    values = {**DEFAULTS, "socket_server": "localhost", "socket_port": 10001}
    result = client.put("/api/settings", json={**values, "revision": 0})
    assert result.status_code == 200 and result.json["settings_revision"] == 1
    assert service.store.load() == values
    assert client.put("/api/settings", json={**values, "revision": 0}).status_code == 409
    service.connect()
    assert client.put("/api/settings", json={**values, "revision": 1}).status_code == 409
    assert service.store.load() == values


def test_offline_send_no_side_effect(client, service):
    assert client.post("/api/send", json={"type": "rfid", "uid": "01234567"}).status_code == 409
    assert (
        client.post(
            "/api/send", json={"type": "telemetry", "field": "rpm", "value": 60}
        ).status_code
        == 409
    )
    assert not service.queue and service.model.rpm == 0 and service.model.last_uid is None


@pytest.mark.parametrize(
    "path,method,payload",
    [
        ("/api/simulation", "put", []),
        ("/api/simulation", "put", {"missing": 1}),
        ("/api/send", "post", {"type": "bad"}),
        ("/api/connection", "post", {"action": "reboot"}),
        ("/api/settings", "put", DEFAULTS),
    ],
)
def test_api_invalid_payload(client, path, method, payload):
    assert getattr(client, method)(path, json=payload).status_code == 400


def test_bad_json_mime_size_and_cursor(client):
    assert client.post("/api/send", data="bad", content_type="application/json").status_code == 400
    assert client.post("/api/send", data="{}").status_code == 415
    assert (
        client.post("/api/send", data="x" * 17000, content_type="application/json").status_code
        == 413
    )
    for suffix in ("after=nope", "after=-1", "epoch=-1"):
        assert client.get("/api/events?" + suffix).status_code == 400


def test_event_clear_export_and_monotonic_snapshots(client, service):
    first = client.get("/api/events").json
    cleared = client.delete("/api/events", json={}).json
    assert cleared["epoch"] == first["epoch"] + 1
    assert cleared["cursor"] == first["cursor"]
    after = client.get(f"/api/events?after={first['cursor']}&epoch={first['epoch']}").json
    assert after["reset"] and not after["events"]
    service.update_simulation({"rpm": 12})
    assert client.get("/api/events").json["cursor"] > first["cursor"]
    export = client.get("/api/events/export")
    assert "attachment" in export.headers["Content-Disposition"]
    assert json.loads(export.data)["events"]
    a, b = client.get("/api/state").json, client.get("/api/state").json
    assert a["instance_id"] == b["instance_id"] and b["snapshot_sequence"] > a["snapshot_sequence"]


def test_event_overflow():
    log = EventLog(capacity=3)
    for number in range(6):
        log.add("SYS", str(number))
    result = log.read(1, 0)
    assert result["reset"] and [x["id"] for x in result["events"]] == [4, 5, 6]
    log.clear()
    log.add("SYS", "next")
    assert log.cursor == 7 and log.epoch == 1


@pytest.mark.parametrize(
    "host",
    [
        "",
        "http://localhost",
        "a/b",
        "localhost:9999",
        "a" * 64,
        "x..com",
        "bad_host",
        "\ud800",
        "x\n.com",
    ],
)
def test_bad_hosts(host):
    with pytest.raises(ValueError):
        validate_settings({**DEFAULTS, "socket_server": host})


@pytest.mark.parametrize(
    "host", ["localhost", "127.0.0.1", "::1", "host-name.example", "example.com."]
)
def test_valid_hosts(host):
    assert validate_settings({**DEFAULTS, "socket_server": host})["socket_server"] == host


def test_corrupt_settings_are_not_overwritten(tmp_path):
    store = SettingsStore(tmp_path)
    store.path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        store.load()
    assert store.path.read_text(encoding="utf-8") == "{broken"


def test_atomic_store_failure_preserves_previous(tmp_path, monkeypatch):
    store = SettingsStore(tmp_path)
    store.save(DEFAULTS)

    def fail(*args):
        raise OSError("disk failed")

    monkeypatch.setattr("rehab_simulator.storage.os.replace", fail)
    with pytest.raises(OSError):
        store.save({**DEFAULTS, "socket_port": 1234})
    assert store.load() == DEFAULTS
    assert not list(tmp_path.glob("settings-*.tmp"))
