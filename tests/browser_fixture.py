"""TEST ONLY: loopback peer for reproducible browser QA and README screenshots.

No HTTP/UI, no app imports/monkeypatching. Exits after --duration seconds.
Synthetic UID suffixes drive predetermined commands; never connect this to hardware.
"""

import argparse
import socket
import time

from rehab_simulator.protocol import Frame, FrameStream


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, default=900)
    args = parser.parse_args()
    deadline = time.monotonic() + args.duration
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        listener.settimeout(0.5)
        print(f"TEST_PEER_PORT={listener.getsockname()[1]}", flush=True)
        while time.monotonic() < deadline:
            try:
                peer, _ = listener.accept()
            except socket.timeout:
                continue
            with peer:
                peer.settimeout(0.2)
                stream = FrameStream()
                peer.sendall(
                    b"".join(Frame(0x20, bytes((selector,))).encode() for selector in range(3))
                )
                while time.monotonic() < deadline:
                    try:
                        chunk = peer.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not chunk:
                        break
                    frames, errors = stream.feed(chunk, time.monotonic())
                    for frame in frames:
                        print(f"RX {frame.hex}", flush=True)
                        if frame.command == 0x21:
                            uid = frame.data.hex().upper()
                            commands = {
                                "00000000": [Frame(0x22, b"\x02")],
                                "00000001": [Frame(0x22, b"\x00")],
                                "00000002": [Frame(0x25, b"\x00")],
                                "00000003": [Frame(0x25, b"\xff\xff")],
                            }.get(uid, [Frame(0x22, b"\x01"), Frame(0x25, b"\x0a")])
                            try:
                                peer.sendall(
                                    Frame(0x21, b"\xc8").encode()
                                    + b"".join(x.encode() for x in commands)
                                )
                            except OSError:
                                break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
