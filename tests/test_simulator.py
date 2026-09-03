import pytest

from rehab_simulator.protocol import Frame
from rehab_simulator.simulator import Simulator


def test_start_stop_pause_fifo_nonblocking():
    sim = Simulator()
    assert sim.command(Frame(0x22, b"\x01"), 10) == []
    assert sim.command(Frame(0x20, b"\x01"), 10.2) == [Frame(0x20, b"\x01\xc9")]
    assert not sim.tick(10.499) and not sim.tracking
    assert sim.tick(10.5) == [Frame(0x22, b"\x01\xc8")]
    assert sim.tracking and sim.machine_state == "Start"
    sim.command(Frame(0x22, b"\x02"), 11)
    assert sim.tracking and sim.machine_state == "Start"
    sim.command(Frame(0x22, b"\x00"), 11)
    sim.command(Frame(0x22, b"\x01"), 11.1)
    assert not sim.tick(14.499)
    assert sim.tick(14.5) == [Frame(0x22, b"\x00\xc8")]
    assert not sim.tracking
    assert sim.tick(15) == [Frame(0x22, b"\x01\xc8")]
    assert sim.tracking


@pytest.mark.parametrize(
    "state,code", [("Start", 0xC8), ("Idle", 0xC9), ("Pause", 0xC9), ("End", 0xC9)]
)
def test_machine_state_is_not_tracking(state, code):
    sim = Simulator(tracking=True, machine_state=state, auto_follow=False)
    assert sim.command(Frame(0x20, b"\x01"), 0)[0].data == bytes((1, code))
    assert sim.tracking
    sim.command(Frame(0x22, b"\x00"), 0)
    sim.tick(3.5)
    assert sim.machine_state == state and not sim.tracking


@pytest.mark.parametrize("ready,code", [(True, 0xC8), (False, 0xC9)])
@pytest.mark.parametrize("selector,field", [(0, "reader_ready"), (2, "hr_ready")])
def test_reader_hr_status(ready, code, selector, field):
    sim = Simulator()
    sim.update({field: ready})
    assert sim.command(Frame(0x20, bytes((selector,))), 0)[0].data == bytes((selector, code))


def test_level_zero_high_and_manual_override():
    sim = Simulator(reported_level=12, tracking=True)
    assert sim.command(Frame(0x25, b"\x00"), 0) == [Frame(0x25, b"\xc8")]
    assert sim.target_level == 0 and sim.reported_level == 12
    sim.command(Frame(0x25, b"\xff\xff"), 0)
    assert sim.target_level == sim.reported_level == 65535
    assert sim.snapshot()["level_warning"] and not sim.telemetry_due()
    sim.update({"auto_follow": False, "reported_level": 8})
    sim.command(Frame(0x25, b"\x00\x0a"), 0)
    assert sim.target_level == 10 and sim.reported_level == 8


@pytest.mark.parametrize(
    "command,payload",
    [(0x25, b""), (0x25, b"\0\0\0"), (0x22, b"\x04"), (0x20, b"\x03"), (0x20, b"\0\0")],
)
def test_bad_payload_is_atomic(command, payload):
    sim = Simulator()
    before = sim.snapshot()
    with pytest.raises(ValueError):
        sim.command(Frame(command, payload), 0)
    assert sim.snapshot() == before


@pytest.mark.parametrize("value", [-1, 256, True, 1.5, "12", None, float("nan"), float("inf")])
@pytest.mark.parametrize("field", ["rpm", "reported_level", "heart_rate"])
def test_invalid_simulation_values(field, value):
    sim = Simulator()
    with pytest.raises(ValueError):
        sim.update({"reader_ready": True, field: value})
    assert not sim.reader_ready


def test_on_change_and_actual_last_sent():
    sim = Simulator()
    assert sim.last_sent["rpm"] is None
    sim.update({"rpm": 60})
    assert not sim.telemetry_due()
    sim.tracking = True
    assert sim.telemetry_due() == [Frame(0x23, b"\x00\x3c")]
    sim.sent(sim.telemetry_due()[0])
    assert not sim.telemetry_due() and sim.last_sent["rpm"] == 60
    sim.rpm = 61
    sim.rebase_after_disconnect()
    assert not sim.telemetry_due() and sim.last_sent["rpm"] == 60
    sim.rpm = 62
    assert sim.telemetry_due() == [Frame(0x23, b"\x00\x3e")]


def test_initial_offline_values_still_sent_on_first_start():
    sim = Simulator(rpm=60, heart_rate=80)
    sim.rebase_after_disconnect()
    sim.command(Frame(0x22, b"\x01"), 0)
    sim.tick(0.5)
    assert len(sim.telemetry_due()) == 2


def test_fault_does_not_use_stale_reason():
    sim = Simulator(tracking=True, hr_ready=True)
    assert sim.fault() == [Frame(0x24, b"\0")]
    assert not sim.tracking
    sim.hr_ready = False
    assert sim.fault() == [Frame(0x24, b"\0"), Frame(0x20, b"\x02\xc9")]
