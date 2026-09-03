"""Binary framing only. No sockets, Flask, or hardware imports."""

from dataclasses import dataclass

HEAD = 0x7A
TAIL = 0x7F
TELEMETRY = {"rpm": 0, "reported_level": 1, "heart_rate": 2}
COMMANDS = {
    0x20: "裝置狀態",
    0x21: "卡片 UID",
    0x22: "追蹤控制",
    0x23: "數值回報",
    0x24: "異常停止",
    0x25: "阻力設定",
}


def integer(value, name, minimum=0, maximum=255):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} 必須是 {minimum}–{maximum} 的整數")
    return value


def parse_uid(value):
    if not isinstance(value, str):
        raise ValueError("UID 必須是 HEX 字串")
    value = "".join(value.split())
    if not value or len(value) % 2 or len(value) > 510:
        raise ValueError("UID 需為 1–255 bytes，HEX 位數必須為偶數")
    if any(ch not in "0123456789abcdefABCDEF" for ch in value):
        raise ValueError("UID 只能包含 HEX 字元與空白")
    return bytes.fromhex(value)


@dataclass(frozen=True)
class Frame:
    command: int
    data: bytes = b""

    def encode(self):
        integer(self.command, "命令")
        if not isinstance(self.data, bytes) or len(self.data) > 255:
            raise ValueError("封包資料必須是最多 255 bytes")
        body = bytes((self.command, len(self.data))) + self.data
        return bytes((HEAD,)) + body + bytes((255 - sum(body) % 256, TAIL))

    @property
    def hex(self):
        return self.encode().hex(" ").upper()


def decode(packet):
    if len(packet) < 5 or packet[0] != HEAD or packet[-1] != TAIL:
        raise ValueError("封包起始／結束碼錯誤")
    if len(packet) != packet[2] + 5:
        raise ValueError("封包長度錯誤")
    if sum(packet[1:-1]) % 256 != 255:
        raise ValueError("校驗碼錯誤")
    return Frame(packet[1], bytes(packet[3:-2]))


def is_reply(frame):
    """0x25 C8 is an inbound level=200 command, NOT an ACK in this role."""
    data = frame.data
    if frame.command == 0x21:
        return len(data) == 1 and data[0] in (0xC8, 0xC9)
    if frame.command in (0x20, 0x22, 0x23):
        return len(data) == 2 and data[0] <= 2 and data[1] in (0xC8, 0xC9)
    return False


def describe(frame, direction):
    cmd, data = frame.command, frame.data
    if cmd == 0x21:
        if direction == "RX" and is_reply(frame):
            return "卡號已確認（C8）" if data[0] == 0xC8 else "卡號被拒絕（C9）"
        return f"刷卡 UID {data.hex().upper()}"
    if cmd == 0x23 and len(data) == 2:
        name = {0: "RPM", 1: "Level", 2: "BPM"}.get(data[0], f"類型 {data[0]}")
        return f"{name} = {data[1]}"
    if cmd == 0x20 and data:
        name = {0: "讀卡器", 1: "機台", 2: "心率裝置"}.get(data[0], "未知裝置")
        if len(data) == 1:
            return f"查詢{name}狀態"
        if len(data) == 2:
            return f"{name}：{'正常 C8' if data[1] == 0xC8 else '異常 C9' if data[1] == 0xC9 else data[1]}"
    if cmd == 0x22 and data:
        name = {0: "停止", 1: "開始", 2: "暫停（原版僅確認）"}.get(data[0], "未知控制")
        return name + ("／回覆 C8" if len(data) == 2 and data[1] == 0xC8 else "")
    if cmd == 0x25:
        if direction == "TX" and data == b"\xc8":
            return "阻力設定已接收 C8"
        return f"目標 Level = {int.from_bytes(data, 'big')}"
    if cmd == 0x24:
        return "異常停止通知（00）" if data == b"\x00" else "未知異常通知"
    return COMMANDS.get(cmd, f"未知命令 0x{cmd:02X}")


class FrameStream:
    """Length-aware stream decoder; never split on a delimiter within a payload."""

    def __init__(self, partial_timeout=2.0):
        self.buffer = bytearray()
        self.since = None
        self.partial_timeout = partial_timeout

    def feed(self, chunk, now):
        frames, errors = [], []
        self.buffer.extend(chunk)
        # Maximum valid frame is 260 bytes. Keep buffering bounded under hostile input.
        if len(self.buffer) > 16384:
            self.buffer = self.buffer[-16384:]
            self.since = None
            errors.append("接收緩衝超限，已捨棄過量資料")
        while self.buffer:
            if self.buffer[0] != HEAD:
                position = self.buffer.find(bytes((HEAD,)))
                count = position if position >= 0 else len(self.buffer)
                del self.buffer[:count]
                self.since = None
                errors.append(f"捨棄 {count} bytes 非封包資料")
                continue
            if self.since is None:
                self.since = now
            total = self.buffer[2] + 5 if len(self.buffer) >= 3 else 5
            if len(self.buffer) < total:
                if now - self.since >= self.partial_timeout:
                    del self.buffer[0]
                    self.since = None
                    errors.append("不完整封包逾時，重新同步")
                    continue
                break
            candidate = bytes(self.buffer[:total])
            try:
                frame = decode(candidate)
            except ValueError as error:
                errors.append(f"{error}：{candidate.hex(' ').upper()}")
                del self.buffer[0]
            else:
                frames.append(frame)
                del self.buffer[:total]
            self.since = None
        return frames, errors
