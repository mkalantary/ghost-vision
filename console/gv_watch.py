#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 M. Kalantary
# SPDX-License-Identifier: Apache-2.0
"""
GHOST-VISION  --  gv_watch.py
Bring-up tool

IN PLAIN WORDS
    Plug one board into your PC with a USB cable and run this. It shows you
    what the board is saying -- which node it thinks it is, whether it joined
    your WiFi, and any errors -- without drowning you in measurement data.

WHAT THIS IS
    A serial monitor that throws away the CSI flood and shows you only the
    lines that mean something: boot banner, warnings, errors, and the GV
    status lines.  Everything is also saved to gv_watch_log.txt so you can
    scroll back at your leisure.

WHY IT EXISTS
    The board prints ~50 CSI lines per second.  Any normal terminal loses
    the important lines in that torrent within seconds.

RUN
    python gv_watch.py COM5

    (Add --all if you ever want the CSI lines on screen too.
     The log file always contains everything.)

STOP
    Ctrl + C

IMPORTANT
    Only one program can own a COM port.  Close the ESP-IDF Monitor first
    (Ctrl + ]) or this will fail with "access denied".

REQUIRES
    pip install pyserial
"""

import io
import sys
import time
import argparse

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("pyserial is not installed.  Run:  pip install pyserial")
    sys.exit(1)

LOGFILE = "gv_watch_log.txt"


def show_ports():
    """List the serial ports Windows can actually see right now."""
    ports = list(list_ports.comports())
    if not ports:
        print("  (no serial ports found at all -- is the board plugged in?)")
        return
    for p in ports:
        print("  %-8s %s" % (p.device, p.description))


def is_noise(line):
    """True for the high-volume CSI data lines we want off the screen."""
    s = line.lstrip()
    return s.startswith("CSI_DATA") or s.startswith("type,")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", help="e.g. COM5")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--all", action="store_true",
                    help="also show the CSI data lines")
    ap.add_argument("--list", action="store_true",
                    help="just list the serial ports and exit")
    args = ap.parse_args()

    if args.list or not args.port:
        print("Serial ports currently present:")
        show_ports()
        if not args.port:
            print("\nRun again with one of them, e.g.:  python gv_watch.py COM5")
        return

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.2)
    except Exception as e:
        print("Could not open %s: %s" % (args.port, e))
        print("\nIf this says 'access denied', the ESP-IDF Monitor still has "
              "the port.\nStop it with Ctrl + ] and try again.")
        print("\nPorts that do exist right now:")
        show_ports()
        sys.exit(1)

    print("Watching %s at %d baud.  Full log -> %s" % (args.port, args.baud, LOGFILE))
    print("Showing status lines only (use --all for everything).  Ctrl+C to stop.\n")

    shown = 0
    hidden = 0
    started = time.time()

    with io.open(LOGFILE, "w", encoding="utf-8", errors="replace") as log:
        try:
            while True:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line.strip():
                    continue

                log.write(line + "\n")
                log.flush()

                if is_noise(line) and not args.all:
                    hidden += 1
                    continue

                shown += 1
                print("[%6.1fs] %s" % (time.time() - started, line))
        except KeyboardInterrupt:
            pass
        finally:
            try:
                ser.close()
            except Exception:
                pass

    print("\nStopped after %.1f s.  %d lines shown, %d CSI lines hidden."
          % (time.time() - started, shown, hidden))

    if shown == 0 and hidden == 0:
        print("\n!! NOTHING arrived on %s in %.0f seconds." % (args.port, time.time() - started))
        print("!! The port opened fine, so it exists -- it is just silent.")
        print("!! In order of likelihood:")
        print("   1. Wrong port. A board takes the COM number of the SOCKET it")
        print("      is plugged into, not its own identity. Ports present now:")
        show_ports()
        print("   2. Board sitting in download mode after flashing.")
        print("      Press the RESET (RST/EN) button and try again.")
        print("   3. Board not powered, or a charge-only USB cable.")
    else:
        print("Everything (including any CSI lines) is in %s" % LOGFILE)


if __name__ == "__main__":
    main()
