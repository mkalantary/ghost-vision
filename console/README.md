# Console — the program on your PC

This is what you look at. The boards send it measurements; it decides whether someone is moving, shows roughly where on a map of your room, and keeps a record.

**Easiest way to start:** double-click `run_simulation.bat` (no hardware) or `run_ghost_vision.bat` (with your boards) in the main project folder. On macOS / Linux, use the `.sh` versions. They install everything needed into a private folder, `console/.venv`, the first time.

| File | What it is |
|---|---|
| `ghost_vision_ops.py` | **The main application.** 16 path engines, corroborated detection, room planner, live tactical map, calibration, arm/disarm, recording, install report. Serial and UDP. |
| `gv_listen.py` | Bring-up tool. Prints rate, RSSI, loss and wobble per node and per path. Not a detector. |
| `gv_watch.py` | Serial monitor that hides the CSI flood so you can read the boot banner and stats. |
| `config/` | Example room layout (4.5 m × 2.5 m, nodes in the corners). Copy it next to `ghost_vision_ops.py` to start from it. |

## Install by hand (optional)

The launchers do this for you. If you prefer to do it yourself:

```
pip install -r requirements.txt
```

Developed on Windows. Sound uses `winsound` on Windows and falls back to Qt elsewhere. The camera panel is optional and only needs `opencv-python` if you use it.

## Run

```
python ghost_vision_ops.py           # live, from the nodes
python ghost_vision_ops.py --sim     # four virtual nodes and a virtual occupant
python ghost_vision_ops.py --noboot  # skip the boot sequence
python gv_listen.py                  # per-node / per-path link health
python gv_watch.py --list            # list serial ports
python gv_watch.py COM5              # watch one board's serial output
```

## Network setup for the PC

- Wire the PC to the router by Ethernet. Don't join the sensing WiFi from the PC.
- Static IP `10.8.0.50`, **no gateway, no DNS.**
- If the PC has a second network (e.g. a mobile hotspot), make sure the two subnets don't overlap. Run `ipconfig` and check.

## Using it

1. **Calibrate first.** Leave the room empty — the exit delay covers your walk out. Calibration refuses to run if the room isn't quiet.
2. **Recalibrate after any change** to node positions, furniture or the data path. Levels only mean something against thresholds measured on the same setup.
3. Keep the room map accurate. Node positions on the map are part of the measurement.

| Symptom | Fix |
|---|---|
| Movement barely registers | Raise **Gain** |
| False alarms when still | Recalibrate, then raise **Hits to trigger** to 5–6 |
| Line too jittery | **Smoothing** 8, **Window** 30 |
| Too slow to react | **Window** 15, **Smoothing** 4 |
| "SCALE MAXED" | Lower **Gain**, recalibrate |
| "HELD 5+ MIN — RECALIBRATE" | Trigger too low — recalibrate with the room empty |
| "TIMING STRETCHED" / "RATE COLLAPSED" | Protective gates working. Occasional is fine; constant means a link problem |

## Positioning default

Zone estimation uses the **4 router paths only** (`solver_paths = "router"`). The all-16-path solver is built but off, because it assumes every path is equally sensitive — and wall-hugging paths are not. See [../docs/lessons-learned.md](../docs/lessons-learned.md).
