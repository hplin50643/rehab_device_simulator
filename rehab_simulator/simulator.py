"""Pure deterministic virtual hardware state machine; caller supplies monotonic time."""

from collections import deque
from dataclasses import asdict, dataclass, field

from .protocol import TELEMETRY, Frame, integer

MACHINE_STATES = ("Idle", "Start", "Pause", "End")


@dataclass
class Simulator:
    rpm: int = 0
    heart_rate: int = 0
    reported_level: int = 0
    target_level: int | None = None
    tracking: bool = False
    reader_ready: bool = False
    hr_ready: bool = False
    machine_state: str = "End"
    auto_follow: bool = True
    auto_report: bool = True
    last_uid: str | None = None
    last_command: str | None = None
    controls: deque = field(default_factory=deque)
    last_sent: dict = field(default_factory=lambda: dict.fromkeys(TELEMETRY))
    auto_baseline: dict = field(default_factory=lambda: dict.fromkeys(TELEMETRY, 0))

    def snapshot(self):
        result = asdict(self)
        result.pop("controls")
        result.pop("auto_baseline")
        result["pending_control"] = (
            {"action": "開始" if self.controls[0][1] else "停止", "due_at": self.controls[0][0]}
            if self.controls
            else None
        )
        result["level_warning"] = (
            "此 Level 超出 TCP 單 byte 回報範圍；不會截斷或送出非法封包"
            if self.reported_level > 255
            else None
        )
        return result

    def update(self, changes):
        allowed = {
            *TELEMETRY,
            "reader_ready",
            "hr_ready",
            "machine_state",
            "auto_follow",
            "auto_report",
        }
        if not changes or set(changes) - allowed:
            raise ValueError("模擬設定為空或包含不支援欄位")
        # Validate the complete patch before mutating any field.
        for key, value in changes.items():
            if key in TELEMETRY:
                integer(value, key)
            elif key == "machine_state":
                if not isinstance(value, str) or value not in MACHINE_STATES:
                    raise ValueError("機台狀態必須是 Idle / Start / Pause / End")
            elif type(value) is not bool:
                raise ValueError(f"{key} 必須是布林值")
        for key, value in changes.items():
            setattr(self, key, value)

    def command(self, frame, now):
        """Return immediate replies. Raises ValueError on unsupported command data."""
        cmd, data = frame.command, frame.data
        if cmd == 0x20 and len(data) == 1 and data[0] in (0, 1, 2):
            ready = (self.reader_ready, self.machine_state == "Start", self.hr_ready)[data[0]]
            return [Frame(cmd, data + bytes((0xC8 if ready else 0xC9,)))]
        if cmd == 0x22 and len(data) == 1 and data[0] in (0, 1, 2):
            action = data[0]
            self.last_command = {0: "停止", 1: "開始", 2: "暫停（僅確認）"}[action]
            if action == 2:
                return [Frame(cmd, b"\x02\xc8")]
            previous_due = self.controls[-1][0] if self.controls else now
            due = max(now, previous_due) + (0.5 if action == 1 else 3.5)
            self.controls.append((due, action))
            return []
        if cmd == 0x25 and len(data) in (1, 2):
            self.target_level = int.from_bytes(data, "big")
            self.last_command = f"設定 Level {self.target_level}"
            if self.auto_follow and self.target_level > 0:
                self.reported_level = self.target_level
            return [Frame(cmd, b"\xc8")]
        raise ValueError("不支援的命令或資料長度；未更動機台狀態")

    def tick(self, now):
        replies = []
        while self.controls and self.controls[0][0] <= now:
            _, action = self.controls.popleft()
            self.tracking = action == 1
            if self.auto_follow:
                self.machine_state = "Start" if action else "End"
            replies.append(Frame(0x22, bytes((action, 0xC8))))
        return replies

    def telemetry_due(self):
        if not self.tracking or not self.auto_report:
            return []
        return [
            Frame(0x23, bytes((subtype, getattr(self, name))))
            for name, subtype in TELEMETRY.items()
            if 0 <= getattr(self, name) <= 255 and getattr(self, name) != self.auto_baseline[name]
        ]

    def sent(self, frame):
        if frame.command == 0x23 and len(frame.data) == 2:
            name = next(
                (name for name, subtype in TELEMETRY.items() if subtype == frame.data[0]), None
            )
            if name:
                self.last_sent[name] = frame.data[1]
                self.auto_baseline[name] = frame.data[1]
        elif frame.command == 0x21:
            self.last_uid = frame.data.hex().upper()

    def rebase_after_disconnect(self):
        # Suppress cancelled/offline values, without claiming they were transmitted.
        if self.tracking:
            self.auto_baseline = {name: getattr(self, name) for name in TELEMETRY}

    def fault(self):
        self.controls.clear()
        self.tracking = False
        if self.auto_follow:
            self.machine_state = "End"
        packets = [Frame(0x24, b"\x00")]
        if not self.hr_ready:
            packets.append(Frame(0x20, b"\x02\xc9"))
        return packets
