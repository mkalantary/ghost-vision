# Changelog

What changed in each version, newest first.

## 4.0 — October 2026 — first public release

The first version published on GitHub.

**System**
- Four ESP32-S3 boards and one router, giving 10 radio paths and 16 directed measurements
- Every board listens to the router and to the other three boards
- Per-path frame fingerprinting and per-path rate limiting in the firmware
- Wireless streaming to the console, with automatic discovery of the console's address
- Motion detection with a persistence check on every path; the board-to-board paths only report motion when at least two agree
- Experimental presence detection and approximate zone estimation with an uncertainty ring
- Room planner with a placement score, calibration that refuses a non-empty room, recording, and install reports
- Simulation mode for trying everything without hardware

**For newcomers**
- One-click launchers for Windows, macOS and Linux
- A complete step-by-step build guide, troubleshooting, FAQ and glossary
- A guide to measuring detection rate and false alarms in your own room
- Lessons learned, limitations and responsible-use pages
- Photos of the real hardware
- A Persian (فارسی) README

**Status:** a working prototype, tested informally in a real room. Detection rate and false-alarm rate are not yet formally measured — see the README.
