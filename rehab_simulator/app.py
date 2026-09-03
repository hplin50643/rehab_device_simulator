"""HTTP boundary: strict JSON, snapshot reads, and serialized controller operations."""

import json
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from .transport import ConflictError, Controller


def create_app(config=None, controller=None):
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=16 * 1024,
        JSON_SORT_KEYS=False,
        INSTANCE_DIR=os.environ.get("REHAB_INSTANCE_DIR", str(Path.cwd() / "instance")),
    )
    if config:
        app.config.update(config)
    app.json.ensure_ascii = False
    app.json.sort_keys = False
    service = controller or Controller(app.config["INSTANCE_DIR"])
    app.extensions["simulator"] = service

    def body():
        data = request.get_json()
        if type(data) is not dict:
            raise ValueError("請提供 JSON 物件")
        return data

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(ConflictError)
    def conflict(error):
        return jsonify(error=str(error)), 409

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(
            error={400: "JSON 格式錯誤", 413: "請求內容過大", 415: "請使用 application/json"}.get(
                error.code, error.description
            )
        ), error.code

    @app.errorhandler(Exception)
    def unexpected(error):
        app.logger.exception("Unexpected request error")
        return jsonify(error="內部處理失敗；請檢查終端日誌"), 500

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/state")
    def state():
        return jsonify(service.snapshot())

    @app.put("/api/settings")
    def settings():
        data = body()
        if "revision" not in data:
            raise ValueError("缺少設定 revision；請重新載入目前設定")
        revision = data.pop("revision")
        service.save_settings(data, revision)
        return jsonify(service.snapshot())

    @app.put("/api/simulation")
    def simulation():
        service.update_simulation(body())
        return jsonify(service.snapshot())

    @app.post("/api/connection")
    def connection():
        data = body()
        if set(data) != {"action"} or data["action"] not in ("connect", "disconnect"):
            raise ValueError("action 必須是 connect 或 disconnect")
        if data["action"] == "connect":
            service.connect()
        else:
            service.disconnect()
        return jsonify(service.snapshot()), 202

    @app.post("/api/send")
    def send():
        service.enqueue(body())
        return jsonify(service.snapshot()), 202

    @app.get("/api/events")
    def events():
        after = int(request.args.get("after", "0"))
        epoch = request.args.get("epoch")
        if after < 0 or (epoch is not None and int(epoch) < 0):
            raise ValueError("事件游標不可為負數")
        return jsonify(service.read_events(after, None if epoch is None else int(epoch)))

    @app.delete("/api/events")
    def clear_events():
        data = body()
        if data:
            raise ValueError("清除紀錄請提供空 JSON 物件")
        return jsonify(service.clear_events())

    @app.get("/api/events/export")
    def export():
        payload = service.read_events()
        payload["notice"] = "僅含記憶體中最近最多 1000 筆；可能含卡號及主機資訊，請妥善保管"
        return app.response_class(
            json.dumps(payload, ensure_ascii=False, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": 'attachment; filename="rehab-events.json"'},
        )

    return app
