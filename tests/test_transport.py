"""Фрейминг Adalight и DDP."""
import socket
import struct

from engine.transport import DDPTransport


def test_ddp_packet():
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    srv.settimeout(1.0)
    host, port = srv.getsockname()

    tx = DDPTransport(host, port)
    rgb = bytes(range(0, 90)) * 12  # 1080 байт = 360 LED
    assert tx.send(rgb)
    pkt, _ = srv.recvfrom(2048)

    flags, seq, dtype, dest, offset, length = struct.unpack("!BBBBIH", pkt[:10])
    assert flags == 0x41 and dest == 0x01 and offset == 0
    assert length == 1080 and pkt[10:] == rgb
    tx.close()
    srv.close()
