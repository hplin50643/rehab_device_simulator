"""One TCP owner thread, serialized writes, non-blocking protocol deadlines."""

import logging
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass
from uuid import uuid4

from .protocol import TELEMETRY, Frame, FrameStream, describe, integer, is_reply, parse_uid
from .simulator import Simulator
from .storage import EventLog, SettingsStore, validate_settings


class ConflictError(Exception):
    """Operation conflicts with the current connection or settings revision."""


@dataclass
class Delivery:
    frame: Frame
    label: str
    attempt: int = 0
    deadline: float = 0


class Controller:
    def __init__(self, instance_dir, *, ack_timeout=0.5, retry_interval=1.0, connect_timeout=3.0):
        self.lock = threading.RLock()
        self.model = Simulator()
        self.events = EventLog()
        self.store = SettingsStore(instance_dir)
        self.settings = self.store.load()
        self.settings_revision = 0
        self.instance_id = uuid4().hex
        self.snapshot_sequence = 0
        self.connection = {
            "status": "disconnected",
            "error": None,
            "attempts": 0,
            "connected_at": None,
            "tx_count": 0,
            "rx_count": 0,
        }
        self.last_delivery = None
        self.desired = False
        self.generation = 0
        self.active_generation = -1
        self.sock = None
        self.queue = deque()
        self.pending = None
        self.echoed = set()
        self.stream = FrameStream()
        self.ack_timeout = ack_timeout
        self.retry_interval = retry_interval
        self.connect_timeout = connect_timeout
        self.next_retry = 0.0
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.events.add("SYS", "模擬器就緒；尚未連線至目標主機")

    def start(self):
        with self.lock:
            if self.thread is not None:
                return
            self.thread = threading.Thread(target=self._run, name="rehab-tcp", daemon=True)
            self.thread.start()

    def close(self):
        self.disconnect()
        self.stop_event.set()
        self.wake.set()
        if self.thread is not None:
            self.thread.join(timeout=self.connect_timeout + 1)

    def snapshot(self):
        with self.lock:
            self.snapshot_sequence += 1
            return {
                "instance_id": self.instance_id,
                "snapshot_sequence": self.snapshot_sequence,
                "settings": self.settings.copy(),
                "settings_revision": self.settings_revision,
                "connection": {**self.connection, "desired": self.desired},
                "simulation": self.model.snapshot(),
                "last_delivery": self.last_delivery,
                "queue_length": len(self.queue),
                "version": "0.1.0",
            }

    def save_settings(self, changes, revision):
        values = validate_settings(changes)
        integer(revision, "設定版本", 0, 2**53 - 1)
        with self.lock:
            if (
                self.desired
                or self.sock is not None
                or self.connection["status"] not in ("disconnected", "error")
            ):
                raise ConflictError("請先斷線，待連線停止後再儲存目標主機")
            if revision != self.settings_revision:
                raise ConflictError("其他分頁已修改設定，請按「還原目前設定」後重新編輯")
            self.store.save(values)
            self.settings = values
            self.settings_revision += 1
            self.events.add(
                "SYS", f"設定已儲存：{values['socket_server']}:{values['socket_port']}；尚未連線"
            )

    def connect(self):
        with self.lock:
            if self.thread is not None and not self.thread.is_alive():
                raise ConflictError("TCP worker 已停止，請重新啟動程式")
            if self.desired:
                return
            if self.connection["status"] == "disconnecting":
                raise ConflictError("正在停止前一條連線，請稍候")
            self.desired = True
            self.generation += 1
            self.connection.update(status="connecting", error=None, attempts=0)
            self.next_retry = 0
            self.events.add("SYS", "已要求連線")
            self.wake.set()

    def disconnect(self):
        with self.lock:
            if self.connection["status"] == "disconnecting":
                return
            active = (
                self.desired or self.sock is not None or self.connection["status"] == "connecting"
            )
            self.desired = False
            self.generation += 1
            self._cancel_work()
            if active:
                self.connection["status"] = "disconnecting"
                self.events.add("SYS", "已要求斷線；取消待送事件及延遲控制，不再自動重連")
            else:
                self.connection["status"] = "disconnected"
            self.wake.set()

    def update_simulation(self, changes):
        with self.lock:
            self.model.update(changes)
            self.events.add(
                "SYS", "模擬值已更新：" + "、".join(f"{k}={v}" for k, v in changes.items())
            )
            self.wake.set()

    def enqueue(self, request):
        if type(request) is not dict:
            raise ValueError("傳送操作必須是 JSON 物件")
        kind = request.get("type")
        if kind == "rfid" and set(request) == {"type", "uid"}:
            packets = [Frame(0x21, parse_uid(request["uid"]))]
            label = "手動刷卡"
        elif kind == "telemetry" and set(request) == {"type", "field", "value"}:
            name = request["field"]
            if not isinstance(name, str) or name not in TELEMETRY:
                raise ValueError("不支援的數值欄位")
            value = integer(request["value"], name)
            packets = [Frame(0x23, bytes((TELEMETRY[name], value)))]
            label = "手動強制送出"
        elif kind == "unexpected_stop" and set(request) == {"type"}:
            packets = None
            label = "手動異常停止"
        else:
            raise ValueError("不支援的傳送操作或欄位")
        with self.lock:
            if self.connection["status"] != "connected" or not self.desired:
                raise ConflictError("尚未連線；沒有排入傳送佇列")
            if len(self.queue) > 250:
                raise ConflictError("傳送佇列已滿，請等待既有事件處理完畢")
            if packets is None:
                packets = self.model.fault()
            if kind == "telemetry":
                setattr(self.model, name, value)
            for frame in packets:
                self.queue.append(Delivery(frame, label))
            self.events.add("SYS", f"{label}已排入佇列（尚未代表主機已確認）")
            self.wake.set()

    def read_events(self, after=0, epoch=None):
        with self.lock:
            return {**self.events.read(after, epoch), "instance_id": self.instance_id}

    def clear_events(self):
        with self.lock:
            self.events.clear()
            return {**self.events.read(self.events.cursor), "instance_id": self.instance_id}

    def _cancel_work(self):
        if self.pending or self.queue:
            self.last_delivery = {
                "status": "cancelled",
                "detail": "連線結束，未重放待確認／待送事件",
            }
        self.queue.clear()
        self.pending = None
        self.model.controls.clear()
        self.model.rebase_after_disconnect()

    def _drop_socket(self, reason=None):
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None
            self._cancel_work()
            self.stream = FrameStream()
            self.echoed.clear()
            self.connection["connected_at"] = None
            if reason:
                self.connection["error"] = reason
                self.events.add("SYS", reason, level="warning")
            if self.desired and self.settings["auto_reconnect"]:
                self.connection["status"] = "reconnecting"
                self.next_retry = time.monotonic() + self.retry_interval
            else:
                self.desired = False
                self.connection["status"] = "error" if reason else "disconnected"

    def _attempt_connect(self, generation, settings):
        with self.lock:
            if generation != self.generation or not self.desired or self.stop_event.is_set():
                return
            self.connection["status"] = "connecting"
            self.connection["attempts"] += 1
            self.events.add("SYS", f"連線至 {settings['socket_server']}:{settings['socket_port']}")
        try:
            candidate = socket.create_connection(
                (settings["socket_server"], settings["socket_port"]), timeout=self.connect_timeout
            )
            candidate.settimeout(0.05)
        except (OSError, UnicodeError, ValueError) as error:
            with self.lock:
                if generation == self.generation and self.desired:
                    self._drop_socket(f"連線失敗：{error}")
            return
        with self.lock:
            if generation != self.generation or not self.desired or self.stop_event.is_set():
                candidate.close()
                return
            self.sock = candidate
            self.model.rebase_after_disconnect()
            self.active_generation = generation
            self.connection.update(status="connected", error=None, connected_at=time.time())
            self.events.add("SYS", "TCP 已連線；等待主機指令或手動傳送")

    def _send(self, frame, label="自動回覆", attempt=1):
        with self.lock:
            if not self.sock or not self.desired or self.active_generation != self.generation:
                return False
            # Small (<=260 byte) sends; the lock prevents disconnect/enqueue races.
            try:
                self.sock.sendall(frame.encode())
            except OSError as error:
                self._drop_socket(f"傳送失敗：{error}；本次是否送達無法確認")
                return False
            self.model.sent(frame)
            self.connection["tx_count"] += 1
            self.events.add(
                "TX",
                describe(frame, "TX"),
                command=f"0x{frame.command:02X}",
                hex=frame.hex,
                checksum="valid",
                attempt=attempt,
                source=label,
            )
            return True

    def _received(self, frame, now):
        with self.lock:
            self.connection["rx_count"] += 1
            self.events.add(
                "RX",
                describe(frame, "RX"),
                command=f"0x{frame.command:02X}",
                hex=frame.hex,
                checksum="valid",
            )
            if self.pending:
                delivery = self.pending
                confirmed = (
                    delivery.frame.command == 0x21 and frame.command == 0x21 and is_reply(frame)
                )
                status = (
                    ("confirmed" if frame.data[0] == 0xC8 else "rejected")
                    if confirmed
                    else "received"
                )
                detail = (
                    "卡號已確認（不代表登入／課程查詢成功）"
                    if status == "confirmed"
                    else "主機拒絕卡號（C9）"
                    if status == "rejected"
                    else "收到有效資料，結束原版等待；不是本次事件的對應 ACK"
                )
                self.last_delivery = {
                    "status": status,
                    "detail": detail,
                    "attempt": delivery.attempt,
                    "command": f"0x{delivery.frame.command:02X}",
                }
                self.events.add("SYS", detail, delivery=status, attempt=delivery.attempt)
                self.pending = None
            if is_reply(frame):
                return
            if frame.command in (0x20, 0x22, 0x25):
                try:
                    if frame.command == 0x22 and len(self.model.controls) >= 100:
                        raise ValueError("延遲控制佇列已滿，忽略過量命令")
                    replies = self.model.command(frame, now)
                except ValueError as error:
                    self.events.add("SYS", str(error), level="warning")
                    return
                for reply in replies:
                    if not self._send(reply):
                        break
            else:
                # Unknown valid packets retain a bounded, once-per-connection echo.
                encoded = frame.encode()
                if encoded not in self.echoed and len(self.echoed) < 128:
                    self.echoed.add(encoded)
                    self._send(frame, "未知命令：本連線僅 echo 一次")
                else:
                    self.events.add("SYS", "忽略重複未知封包，避免 echo 迴圈", level="warning")

    def _tick(self, now):
        with self.lock:
            if not self.sock or not self.desired or self.active_generation != self.generation:
                return
            for reply in self.model.tick(now):
                if not self._send(reply, "延遲控制完成"):
                    return
            if self.pending and now >= self.pending.deadline:
                delivery = self.pending
                if delivery.attempt < 3:
                    delivery.attempt += 1
                    if self._send(delivery.frame, delivery.label, delivery.attempt):
                        delivery.deadline = time.monotonic() + self.ack_timeout
                else:
                    self.last_delivery = {
                        "status": "timeout",
                        "command": f"0x{delivery.frame.command:02X}",
                        "attempt": delivery.attempt,
                        "detail": "三次傳送後未收到回覆；對端不一定提供 ACK，送達狀態未知",
                    }
                    self.events.add(
                        "SYS", self.last_delivery["detail"], level="warning", delivery="timeout"
                    )
                    self.pending = None
            if not self.sock or not self.desired:
                return
            if self.pending is None and self.queue:
                delivery = self.queue.popleft()
                delivery.attempt = 1
                if self._send(delivery.frame, delivery.label):
                    waits = delivery.frame.command != 0x23
                    self.last_delivery = {
                        "status": "waiting" if waits else "sent",
                        "attempt": 1,
                        "command": f"0x{delivery.frame.command:02X}",
                        "detail": "已寫入 TCP，等待回覆"
                        if waits
                        else "已寫入 TCP；數值回報不等待 ACK",
                    }
                    if waits:
                        delivery.deadline = time.monotonic() + self.ack_timeout
                        self.pending = delivery
            if self.sock and self.pending is None and not self.queue:
                for packet in self.model.telemetry_due():
                    if not self._send(packet, "追蹤中變值回報"):
                        break

    def _run(self):
        fatal_error = None
        try:
            while not self.stop_event.is_set():
                with self.lock:
                    if self.sock and (
                        not self.desired or self.active_generation != self.generation
                    ):
                        self._drop_socket()
                    if not self.desired and self.connection["status"] != "error":
                        self.connection["status"] = "disconnected"
                    desired, generation = self.desired, self.generation
                    settings = self.settings.copy()
                    current_socket = self.sock
                    next_retry = self.next_retry
                if not desired:
                    self.wake.wait(0.1)
                    self.wake.clear()
                    continue
                if current_socket is None:
                    if time.monotonic() >= next_retry:
                        self._attempt_connect(generation, settings)
                    else:
                        self.wake.wait(min(0.1, max(0, next_retry - time.monotonic())))
                        self.wake.clear()
                    continue
                try:
                    chunk = current_socket.recv(4096)
                    if not chunk:
                        with self.lock:
                            if generation == self.generation:
                                self._drop_socket("目標主機已關閉連線（EOF）")
                        continue
                except socket.timeout:
                    chunk = b""
                except OSError as error:
                    with self.lock:
                        if generation == self.generation:
                            self._drop_socket(f"接收失敗：{error}")
                    continue
                now = time.monotonic()
                with self.lock:
                    if generation != self.generation or not self.desired:
                        continue
                    frames, errors = self.stream.feed(chunk, now)
                    for error in errors:
                        self.events.add("SYS", error, level="warning", checksum="invalid")
                    for frame in frames:
                        if self.sock is None:
                            break
                        self._received(frame, now)
                    self._tick(now)
        except Exception as error:
            logging.exception("TCP worker stopped unexpectedly")
            fatal_error = str(error)
        finally:
            with self.lock:
                self.desired = False
                self._drop_socket()
                if fatal_error:
                    self.connection.update(
                        status="error", error="TCP worker 已停止，請重新啟動程式"
                    )
                    self.events.add("SYS", f"TCP worker 非預期停止：{fatal_error}", level="warning")
