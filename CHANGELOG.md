# Changelog

What changed in each version, newest first.

## 4.0 — October 2026 — first public release

The first version published on GitHub.

**System**
- Four ESP32-S3 boards and one router, giving 10 radio paths and 16 directed measurements
- Every board listens to the router and to the other three boards
- Per-path frame fingerprinting and per-path rate limiting in the firmware
- Wireless streaming to the console, with automatic discovery of the console's address
- Motion detection that requires at least two paths to agree
- Experimental presence detection and approximate zone estimation with an uncertainty ring
- Room planner with a placement score, calibration that refuses a non-empty room, recording, and install reports
- Simulation mode for trying everything without hardware

**For newcomers**
- One-click launchers for Windows, macOS and Linux
- A complete step-by-step build guide, troubleshooting, FAQ and glossary
- A guide to measuring detection rate and false alarms in your own room
- Lessons learned, limitations and responsible-use pages

**Not yet measured:** detection rate and false alarms in a real room. See the README.
