"""Local settings and bounded, monotonic event history."""

import json
import os
import re
import tempfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .protocol import integer

DEFAULTS = {"socket_server": "192.168.0.100", "socket_port": 9999, "auto_reconnect": True}


def validate_settings(values):
    if type(values) is not dict or set(values) != set(DEFAULTS):
        raise ValueError("設定必須包含 socket_server、socket_port、auto_reconnect")
    host = values["socket_server"]
    if not isinstance(host, str) or not host.strip() or len(host) > 253:
        raise ValueError("socket_server 必須是有效的主機名稱或 IP")
    host = host.strip()
    if any(ch.isspace() or ch in "/\\\x00" for ch in host) or "://" in host:
        raise ValueError("socket_server 請只填主機名稱或 IP，不要包含網址與路徑")
    # IPv6 literals are supported by socket.create_connection; a hostname:port is not.
    if ":" in host:
        import ipaddress

        try:
            ipaddress.IPv6Address(host)
        except ValueError as error:
            raise ValueError("連接埠請填在獨立欄位") from error
    else:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise ValueError("主機名稱編碼或 DNS 標籤長度不合法") from error
        labels = host.removesuffix(".").split(".")
        if len(host) > 253 or any(
            not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in labels
        ):
            raise ValueError("主機名稱每段需為 1–63 個英數字或連字號")
    integer(values["socket_port"], "連接埠", 1, 65535)
    if type(values["auto_reconnect"]) is not bool:
        raise ValueError("auto_reconnect 必須是布林值")
    return {**values, "socket_server": host}


class SettingsStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "settings.json"

    def load(self):
        if not self.path.exists():
            return DEFAULTS.copy()
        try:
            return validate_settings(json.loads(self.path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as error:
            raise ValueError(
                f"無法讀取設定 {self.path}；請先備份並修正，不會覆寫原檔：{error}"
            ) from error

    def save(self, settings):
        settings = validate_settings(settings)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix="settings-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(settings, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


class EventLog:
    """All callers use the controller's lock, including snapshots and exports."""

    def __init__(self, capacity=1000):
        self.items = deque(maxlen=capacity)
        self.cursor = 0
        self.epoch = 0

    def add(self, direction, detail, **attributes):
        self.cursor += 1
        event = {
            "id": self.cursor,
            "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "direction": direction,
            "detail": detail,
            "level": "info",
            **attributes,
        }
        self.items.append(event)
        return event

    def read(self, after=0, epoch=None):
        oldest = self.items[0]["id"] if self.items else self.cursor + 1
        reset = (
            (epoch is not None and epoch != self.epoch) or after < oldest - 1 or after > self.cursor
        )
        events = list(self.items) if reset else [item for item in self.items if item["id"] > after]
        return {"events": events, "cursor": self.cursor, "epoch": self.epoch, "reset": reset}

    def clear(self):
        self.items.clear()
        self.epoch += 1
