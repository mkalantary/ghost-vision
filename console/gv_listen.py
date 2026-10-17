#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 M. Kalantary
# SPDX-License-Identifier: Apache-2.0
"""
GHOST-VISION  --  gv_listen.py
Bring-up tool

IN PLAIN WORDS
    Before you trust the big console, use this little tool to check one
    thing: are your boards' measurements actually arriving at your PC,
    complete and on time? It prints one simple line per second per board.

WHAT THIS IS
    A deliberately tiny listener. It does two jobs:
      1. Announces the console on the network so nodes can discover it.
      2. Receives CSI datagrams and prints a one-line-per-second summary.

    It does NOT detect motion, draw anything, or touch ghost_vision_ops.py.
    Its only purpose is to answer one question: "is the wireless data
    arriving, intact, at the right rate?"  Prove that first.

RUN
    python gv_listen.py

WHAT GOOD LOOKS LIKE
    A line per second per node, ~50 pkt/s, loss 0.0%, and the amplitude
    number rising when you walk between that node and the router.

REQUIRES
    numpy   (pip install numpy)
"""

import time
import socket
import struct
import threading
import collections

import numpy as np

# The sensing router's address. Change this one line if your router is not
# 10.8.0.1 (see docs/TROUBLESHOOTING.md, "Using a different address range").
ROUTER_IP = "10.8.0.1"
SENSING_PREFIX = ROUTER_IP.rsplit(".", 1)[0] + "."     # e.g. "10.8.0."

DATA_PORT = 8266
DISCOVERY_PORT = 8267
HELLO = b"GHOSTVISION"
HDR = 12
CSI_BYTES = 128

# Subcarriers that are always null on this hardware (DC + guard band).
# Measured, not assumed: index 0, and 27..37 inclusive.
DEAD = set([0] + list(range(27, 38)))
LIVE = [i for i in range(64) if i not in DEAD]


def local_ip_for(target=ROUTER_IP):
    """
    Ask Windows which of our addresses it would use to reach the router.
    No packet is actually sent -- connect() on UDP only sets a destination.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target, 9))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def announce(bind_ip=None, router_ip=ROUTER_IP):
    """
    Broadcast 'I am the console' twice a second, forever.

    IMPORTANT: we must NOT let the OS choose the interface. The sensing
    Ethernet adapter has no default gateway (deliberately), so Windows sends
    255.255.255.255 out the internet-facing adapter instead -- where no node
    is listening. So: bind explicitly to the sensing address, and send to the
    subnet broadcast as well as the limited broadcast.
    """
    if bind_ip is None:
        bind_ip = local_ip_for(router_ip)

    if not bind_ip:
        print("!! Could not work out which address reaches %s." % router_ip)
        print("!! Is the Ethernet cable plugged in, with this PC on the %sx network?" % SENSING_PREFIX)
        return

    octets = bind_ip.split(".")
    subnet_bcast = ".".join(octets[:3] + ["255"])

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((bind_ip, 0))
    except OSError as e:
        print("!! Could not bind announcements to %s: %s" % (bind_ip, e))
        return

    print("Announcing from %s to %s and 255.255.255.255 on port %d"
          % (bind_ip, subnet_bcast, DISCOVERY_PORT))
    if not bind_ip.startswith(SENSING_PREFIX):
        print("!! WARNING: %s is not on the %sx sensing network." % (bind_ip, SENSING_PREFIX))
        print("!! Announcements are probably going out the wrong adapter.")

    while True:
        for dst in (subnet_bcast, "255.255.255.255"):
            try:
                s.sendto(HELLO, (dst, DISCOVERY_PORT))
            except OSError:
                pass
        # Deliberately slow. These announcements are transmitted BY the router,
        # so they carry its BSSID, pass each node's filter, and land in the CSI
        # stream as junk records. At 2 Hz they were 17% of a node's sig_len
        # learning window. Every 2 s a node still finds the console long before
        # its 10 s fallback, at a fraction of the contamination.
        time.sleep(2.0)


class PathStat:
    """One sensing path: a specific transmitter heard by a specific node."""

    def __init__(self):
        self.n = 0
        self.rssi = 0
        self.amps = collections.deque(maxlen=200)

    def add(self, rssi, amp):
        self.n += 1
        self.rssi = rssi
        self.amps.append(amp)

    def new_window(self):
        self.n = 0

    def wobble(self):
        if len(self.amps) < 10:
            return 0.0
        a = np.array(self.amps, dtype=float)
        return float(np.std(a) / max(np.mean(a), 1e-6) * 100.0)


class NodeStat:
    """
    Loss accounting by sequence continuity.

    The earlier version compared the first and last sequence number seen in
    each one-second window. That was wrong twice over: it carried the previous
    window's last sequence in as the new window's first (inventing one missing
    packet per second), and a datagram straddling a window boundary could make
    the count go negative. Negative loss is impossible, so the ruler was bad.

    This version walks the sequence numbers. Every gap is counted once, when
    it appears. Nothing is inferred from window edges.
    """

    def __init__(self):
        self.expect = None        # next sequence number we think is due
        self.win_recv = 0         # records received this window
        self.win_missing = 0      # records confirmed missing this window
        self.win_late = 0         # arrived after we'd moved past them
        self.tot_recv = 0
        self.tot_missing = 0
        self.reboots = 0
        self.rssi = 0
        self.amps = collections.deque(maxlen=200)

    def add(self, seq, rssi, amp):
        if self.expect is None:
            self.expect = seq

        # A node that restarts begins its counter at zero again. Without this
        # the jump backwards would register as a huge negative gap.
        if seq < self.expect - 100:
            self.reboots += 1
            self.expect = seq

        gap = seq - self.expect
        if gap > 0:
            self.win_missing += gap
            self.tot_missing += gap
        elif gap < 0:
            # It showed up after we'd moved past it, so we counted it missing
            # earlier. It wasn't. Take it back.
            self.win_late += 1
            self.win_missing = max(0, self.win_missing - 1)
            self.tot_missing = max(0, self.tot_missing - 1)

        if seq >= self.expect:
            self.expect = seq + 1

        self.win_recv += 1
        self.tot_recv += 1
        self.rssi = rssi
        self.amps.append(amp)

    def loss_pct(self):
        total = self.win_recv + self.win_missing
        if total <= 0:
            return 0.0
        return 100.0 * self.win_missing / total

    def total_loss_pct(self):
        total = self.tot_recv + self.tot_missing
        if total <= 0:
            return 0.0
        return 100.0 * self.tot_missing / total

    def new_window(self):
        self.win_recv = 0
        self.win_missing = 0
        self.win_late = 0

    def wobble(self):
        """Std-dev of the mean-normalised amplitude. Rough motion proxy."""
        if len(self.amps) < 10:
            return 0.0
        a = np.array(self.amps, dtype=float)
        return float(np.std(a) / max(np.mean(a), 1e-6) * 100.0)


LINK_NAME = {0: "router", 1: "N1", 2: "N2", 3: "N3", 4: "N4"}


def decode(data):
    """
    Yield (node, link, seq, rssi, mean_amplitude) per record.

    Protocol 1 had a pad byte in every record. Protocol 2 puts the LINK id
    there -- which transmitter this CSI came from -- so the packet is the same
    size. Version 1 packets still decode, reported as link 0 (the router).
    """
    if len(data) < HDR or data[0:2] != b"GV":
        return
    ver, node, n_recs, csi_len = data[2], data[3], data[4], data[5]
    if ver not in (1, 2) or csi_len != CSI_BYTES:
        return
    if node < 1 or node > 4:
        return
    (seq0,) = struct.unpack_from("<I", data, 8)
    rec_bytes = 2 + csi_len
    for k in range(n_recs):
        off = HDR + k * rec_bytes
        if off + rec_bytes > len(data):
            return
        rssi = struct.unpack_from("<b", data, off)[0]
        link = data[off + 1] if ver >= 2 else 0
        if link > 4:
            continue
        raw = np.frombuffer(data, dtype=np.int8, count=csi_len, offset=off + 2)
        iq = raw.reshape(-1, 2).astype(np.float32)
        amp = np.sqrt(iq[:, 0] ** 2 + iq[:, 1] ** 2)
        yield node, link, seq0 + k, rssi, float(np.mean(amp[LIVE]))


def main():
    threading.Thread(target=announce, daemon=True).start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", DATA_PORT))
    sock.settimeout(1.0)

    print("Listening on UDP %d, announcing on %d." % (DATA_PORT, DISCOVERY_PORT))
    print("Waiting for nodes... (Ctrl+C to stop)\n")

    stats = {}
    paths = {}
    window_start = time.time()
    seen_any = False

    try:
        while True:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                data = None
            except OSError:
                data = None

            if data:
                for node, link, seq, rssi, amp in decode(data):
                    if node not in stats:
                        stats[node] = NodeStat()
                        print(">>> Node N%d appeared, from %s" % (node, addr[0]))
                    stats[node].add(seq, rssi, amp)
                    key = (node, link)
                    if key not in paths:
                        paths[key] = PathStat()
                        print("    + path  %s -> N%d" % (LINK_NAME.get(link, "?"), node))
                    paths[key].add(rssi, amp)
                    seen_any = True

            now = time.time()
            if now - window_start >= 1.0:
                elapsed = now - window_start
                if not stats:
                    print("  ...nothing yet")
                for node in sorted(stats):
                    s = stats[node]
                    rate = s.win_recv / elapsed
                    print("N%d  %5.1f pkt/s | rssi %4d | loss %4.1f%% | "
                          "amp %6.2f | wobble %5.2f"
                          % (node, rate, s.rssi, s.loss_pct(),
                             s.amps[-1] if s.amps else 0.0, s.wobble()))
                    s.new_window()
                if paths:
                    row = []
                    for (node, link) in sorted(paths):
                        p = paths[(node, link)]
                        row.append("%s>N%d %4.0f/s w%4.1f"
                                   % (LINK_NAME.get(link, "?"), node,
                                      p.n / elapsed, p.wobble()))
                        p.new_window()
                    for i in range(0, len(row), 3):
                        print("    " + " | ".join(row[i:i + 3]))
                window_start = now
    except KeyboardInterrupt:
        print("\nStopped.")
        if not seen_any:
            print("No data was ever received. Check, in this order:")
            print("  1. Windows Defender is blocking python.exe (most likely)")
            print("  2. The node's serial log -- which IP is it sending to?")
            print("  3. This PC has a fixed %sx address on its Ethernet adapter" % SENSING_PREFIX)
        else:
            print("\nSession totals (these are the numbers that matter):")
            for node in sorted(stats):
                s = stats[node]
                line = ("  N%d: %d records, %d missing -> %.2f%% loss"
                        % (node, s.tot_recv, s.tot_missing, s.total_loss_pct()))
                if s.reboots:
                    line += "   (%d node restart(s) seen)" % s.reboots
                print(line)

            print("\nSensing paths seen — this is the ten-tripwire check:")
            for (node, link) in sorted(paths):
                print("    %-7s -> N%d" % (LINK_NAME.get(link, "?"), node))
            n_router = sum(1 for (_, l) in paths if l == 0)
            n_peer = sum(1 for (_, l) in paths if l != 0)
            print("\n    %d router paths + %d sibling paths = %d total"
                  % (n_router, n_peer, len(paths)))
            if n_peer == 0:
                print("    NO sibling paths. Node-to-node listening is not working.")


if __name__ == "__main__":
    main()
