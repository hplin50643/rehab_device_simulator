"""Single-process entry point. Importing the app never connects to a TCP host."""

import argparse
import logging
import signal
from pathlib import Path

from waitress import create_server

from .app import create_app


def main():
    parser = argparse.ArgumentParser(description="復健機 TCP 模擬儀表板")
    parser.add_argument("--web-host", default="0.0.0.0")
    parser.add_argument("--web-port", type=int, default=5000)
    parser.add_argument("--instance-dir", type=Path, default=None)
    args = parser.parse_args()
    if not 1 <= args.web_port <= 65535:
        parser.error("web-port 必須在 1–65535")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = {"INSTANCE_DIR": str(args.instance_dir.resolve())} if args.instance_dir else None
    try:
        app = create_app(config)
        server = create_server(app, host=args.web_host, port=args.web_port, threads=6)
    except (OSError, ValueError) as error:
        parser.exit(1, f"無法啟動：{error}\n")
    controller = app.extensions["simulator"]
    controller.start()

    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    print(f"Rehab Device Simulator: http://127.0.0.1:{args.web_port}", flush=True)
    print(
        f"Listening on {args.web_host}:{args.web_port}; TCP starts disconnected. Ctrl+C to stop.",
        flush=True,
    )
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        controller.close()
        server.close()


if __name__ == "__main__":
    main()
