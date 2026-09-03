import socket
import time

import pytest

from rehab_simulator.protocol import FrameStream
from rehab_simulator.transport import Controller


def wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "Condition did not become true before deadline"


def receive(peer, count=1, timeout=2):
    stream, frames = FrameStream(), []
    deadline = time.monotonic() + timeout
    while len(frames) < count and time.monotonic() < deadline:
        peer.settimeout(max(0.01, deadline - time.monotonic()))
        try:
            chunk = peer.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        parsed, errors = stream.feed(chunk, time.monotonic())
        assert not errors
        frames.extend(parsed)
    assert len(frames) >= count, f"Expected {count} frames, received {frames}"
    return frames


@pytest.fixture
def service(tmp_path):
    controller = Controller(tmp_path, ack_timeout=0.06, retry_interval=0.05, connect_timeout=0.3)
    yield controller
    controller.close()
    if controller.sock:
        controller.sock.close()


@pytest.fixture
def connected(service):
    # Use the production ACK interval for real threaded I/O. Deterministic retry
    # tests drive their own clock; an accelerated 60ms interval is flaky on CI.
    service.ack_timeout = 0.5
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(3)
        listener.settimeout(2)
        service.save_settings(
            {
                "socket_server": "127.0.0.1",
                "socket_port": listener.getsockname()[1],
                "auto_reconnect": True,
            },
            0,
        )
        service.start()
        service.connect()
        peer, _ = listener.accept()
        wait_until(lambda: service.snapshot()["connection"]["status"] == "connected")
        try:
            yield service, peer, listener
        finally:
            service.disconnect()
            peer.close()
