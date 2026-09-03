import socket
import threading
import time

import pytest
from conftest import receive, wait_until

from rehab_simulator.protocol import Frame
from rehab_simulator.transport import ConflictError


class FakeSocket:
    def __init__(self):
        self.frames = []
        self.closed = False

    def sendall(self, data):
        self.frames.append(data)

    def close(self):
        self.closed = True

    def settimeout(self, value):
        pass

    def recv(self, size):
        raise socket.timeout()


def attach(service):
    service.desired = True
    service.active_generation = service.generation
    service.connection["status"] = "connected"
    service.sock = FakeSocket()
    return service.sock


def test_retry_exact_schedule_and_no_wait_for_telemetry(service, monkeypatch):
    fake = attach(service)
    clock = [0.0]
    monkeypatch.setattr("rehab_simulator.transport.time.monotonic", lambda: clock[0])
    service.ack_timeout = 0.5
    service.enqueue({"type": "rfid", "uid": "01234567"})
    for now in [0, 0.49, 0.5, 0.99, 1.0, 1.49, 1.5]:
        clock[0] = now
        service._tick(now)
    assert len(fake.frames) == 3 and service.last_delivery["status"] == "timeout"
    assert service.last_delivery["attempt"] == 3
    service.enqueue({"type": "telemetry", "field": "rpm", "value": 60})
    service._tick(2)
    assert len(fake.frames) == 4 and service.pending is None


@pytest.mark.parametrize(
    "reply,status",
    [
        (Frame(0x21, b"\xc8"), "confirmed"),
        (Frame(0x21, b"\xc9"), "rejected"),
        (Frame(0x25, b"\xc8"), "received"),
    ],
)
def test_ack_matching_and_control_during_wait(service, reply, status):
    fake = attach(service)
    service.enqueue({"type": "rfid", "uid": "01234567"})
    service._tick(time.monotonic())
    service._received(reply, time.monotonic())
    assert service.pending is None and service.last_delivery["status"] == status
    if reply.command == 0x25:
        assert (
            service.model.target_level == 200 and fake.frames[-1] == Frame(0x25, b"\xc8").encode()
        )


def test_cancelled_values_not_replayed_or_marked_sent(service):
    attach(service)
    service.model.tracking = True
    service.enqueue({"type": "telemetry", "field": "rpm", "value": 60})
    service.disconnect()
    assert not service.queue and service.model.last_sent["rpm"] is None
    fake = attach(service)
    service._tick(time.monotonic())
    assert not fake.frames
    service.model.rpm = 61
    service._tick(time.monotonic())
    assert fake.frames == [Frame(0x23, b"\x00\x3d").encode()]


def test_disconnect_idempotent_and_stale_attempt_never_connects(service, monkeypatch):
    service.connect()
    old = service.generation
    service.disconnect()
    service.disconnect()
    assert service.connection["status"] == "disconnecting"

    def fail(*args, **kwargs):
        raise AssertionError("stale attempt must not open socket")

    monkeypatch.setattr(socket, "create_connection", fail)
    service._attempt_connect(old, service.settings.copy())
    assert service.connection["attempts"] == 0


def test_cancel_during_connect_drops_candidate(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    candidate = FakeSocket()

    def delayed(*args, **kwargs):
        entered.set()
        release.wait(2)
        return candidate

    monkeypatch.setattr(socket, "create_connection", delayed)
    service.start()
    service.connect()
    assert entered.wait(1)
    service.disconnect()
    service.disconnect()
    with pytest.raises(ConflictError):
        service.save_settings(service.settings, 0)
    release.set()
    wait_until(lambda: service.snapshot()["connection"]["status"] == "disconnected")
    assert candidate.closed and service.sock is None


@pytest.mark.parametrize(
    "failure",
    [
        UnicodeError("invalid hostname"),
        ConnectionRefusedError("refused"),
        socket.timeout("timeout"),
    ],
)
def test_connection_failure_does_not_kill_worker(service, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(socket, "create_connection", fail)
    service.settings["auto_reconnect"] = False
    service.start()
    service.connect()
    wait_until(lambda: service.snapshot()["connection"]["status"] == "error")
    assert service.thread.is_alive()
    assert str(failure) in service.connection["error"]
    assert not service.desired and service.connection["attempts"] == 1


def test_start_does_not_autoconnect_and_manual_disconnect_stops_retry(service, monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise ConnectionRefusedError("test refusal")

    monkeypatch.setattr(socket, "create_connection", fail)
    service.start()
    time.sleep(0.12)
    assert not calls
    service.connect()
    wait_until(lambda: len(calls) >= 2)
    service.disconnect()
    wait_until(lambda: service.connection["status"] == "disconnected")
    count = len(calls)
    time.sleep(0.15)
    assert len(calls) == count


def test_real_switch_target_only_after_disconnect(connected):
    service, old_peer, _ = connected
    with socket.socket() as new_listener:
        new_listener.bind(("127.0.0.1", 0))
        new_listener.listen(1)
        new_listener.settimeout(2)
        settings = {**service.settings, "socket_port": new_listener.getsockname()[1]}
        with pytest.raises(ConflictError):
            service.save_settings(settings, service.settings_revision)
        service.disconnect()
        wait_until(lambda: service.connection["status"] == "disconnected")
        old_peer.settimeout(1)
        assert old_peer.recv(1024) == b""
        service.save_settings(settings, service.settings_revision)
        service.connect()
        peer, _ = new_listener.accept()
        with peer:
            wait_until(lambda: service.connection["status"] == "connected")
            service.enqueue({"type": "rfid", "uid": "0001"})
            assert receive(peer) == [Frame(0x21, b"\x00\x01")]
            peer.sendall(Frame(0x21, b"\xc8").encode())
            wait_until(
                lambda: (service.snapshot()["last_delivery"] or {}).get("status") == "confirmed"
            )
            service.disconnect()
            wait_until(lambda: service.connection["status"] == "disconnected")


def test_real_loopback_card_and_ack(connected):
    service, peer, _ = connected
    service.enqueue({"type": "rfid", "uid": "00 01 02 03"})
    assert receive(peer)[0] == Frame(0x21, b"\0\x01\x02\x03")
    packet = Frame(0x21, b"\xc8").encode()
    peer.sendall(packet[:2])
    peer.sendall(packet[2:])
    wait_until(lambda: (service.snapshot()["last_delivery"] or {}).get("status") == "confirmed")
    assert service.model.last_uid == "00010203"


def test_real_stream_and_nonblocking_control(connected):
    service, peer, _ = connected
    peer.sendall(Frame(0x22, b"\x01").encode() + Frame(0x20, b"\x01").encode())
    assert receive(peer, timeout=0.4) == [Frame(0x20, b"\x01\xc9")]
    assert receive(peer, timeout=1) == [Frame(0x22, b"\x01\xc8")]
    assert service.model.tracking
    peer.sendall(Frame(0x25, b"\x0a").encode() + Frame(0x22, b"\x02").encode())
    frames = receive(peer, count=3)
    assert frames[:2] == [Frame(0x25, b"\xc8"), Frame(0x22, b"\x02\xc8")]
    assert Frame(0x23, b"\x01\x0a") in frames and service.model.tracking


def test_real_eof_reconnect_no_card_replay(connected):
    service, peer, listener = connected
    service.enqueue({"type": "rfid", "uid": "01234567"})
    receive(peer)
    peer.close()
    next_peer, _ = listener.accept()
    try:
        wait_until(
            lambda: (
                service.connection["status"] == "connected" and service.connection["attempts"] >= 2
            )
        )
        next_peer.settimeout(0.25)
        with pytest.raises(socket.timeout):
            next_peer.recv(1024)
        service.disconnect()
        wait_until(lambda: service.connection["status"] == "disconnected")
        assert not service.desired
    finally:
        next_peer.close()


def test_real_bad_checksum_rejected(connected):
    service, peer, _ = connected
    bad = bytearray(Frame(0x25, b"\x0a").encode())
    bad[-2] ^= 1
    peer.sendall(bad + Frame(0x20, b"\x00").encode())
    assert receive(peer) == [Frame(0x20, b"\x00\xc9")]
    assert service.model.target_level is None
    assert any(e.get("checksum") == "invalid" for e in service.read_events()["events"])


def test_unknown_echo_once_and_reply_never_echoed(service):
    fake = attach(service)
    packet = Frame(0x66, b"\x01")
    service._received(packet, 0)
    service._received(packet, 1)
    service._received(Frame(0x21, b"\xc8"), 2)
    assert fake.frames == [packet.encode()]
