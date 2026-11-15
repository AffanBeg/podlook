"""Verify PodLook packets on Windows without OpenTrack. Python 3.10+, no dependencies."""
import argparse
import math
import socket
import struct
import time

PACKET = struct.Struct('<6d')


def decode(data):
    if len(data) != PACKET.size:
        raise ValueError(f'Expected 48 bytes, received {len(data)}')
    values = PACKET.unpack(data)
    if not all(math.isfinite(value) for value in values):
        raise ValueError('Packet contains NaN or infinity')
    if any(value != 0 for value in values[:3]):
        raise ValueError('PodLook should send zero translation')
    if any(abs(value) > 180.001 for value in values[3:]):
        raise ValueError('Angle outside expected range')
    return values[3:]


def listen(bind, port, count, timeout):
    received = rejected = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind((bind, port))
        listener.settimeout(0.25)
        print(f'Listening on {bind}:{listener.getsockname()[1]}. Stop OpenTrack first.', flush=True)
        started = time.monotonic()
        while count == 0 or received < count:
            if timeout and time.monotonic() - started > timeout:
                print(f'Timed out: {received} valid, {rejected} rejected.', flush=True)
                return 1
            try:
                data, sender = listener.recvfrom(65535)
            except socket.timeout:
                continue
            try:
                yaw, pitch, roll = decode(data)
            except ValueError as error:
                rejected += 1
                print(f'Rejected packet: {error}', flush=True)
                continue
            received += 1
            if received == 1 or received % 25 == 0 or received == count:
                print(f'{sender[0]}: yaw={yaw:+.1f}, pitch={pitch:+.1f}, roll={roll:+.1f}; valid={received}', flush=True)
        print(f'PASS: received {received} valid packets; rejected {rejected}.', flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=4242)
    parser.add_argument('--count', type=int, default=100, help='Valid packets required; 0 listens continuously')
    parser.add_argument('--timeout', type=float, default=30, help='Seconds; 0 disables timeout')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535 or args.count < 0 or args.timeout < 0 or not math.isfinite(args.timeout):
        parser.error('Invalid port, count or timeout')
    try:
        return listen(args.bind, args.port, args.count, args.timeout)
    except OSError as error:
        print(f'Network error: {error}. Close OpenTrack if the port is in use.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
