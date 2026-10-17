# Troubleshooting

Something not working? Find your symptom below. Most problems are one of a handful of small things — and nearly all of them happened while building this project, so the fixes are tested.

**Golden rule:** change **one** thing at a time, then check again. If you change three things and it starts working, you won't know which one fixed it.

- [The PC doesn't see the board](#the-pc-doesnt-see-the-board)
- [The board shows no banner, or freezes at start-up](#the-board-shows-no-banner-or-freezes-at-start-up)
- [The board won't join the WiFi](#the-board-wont-join-the-wifi)
- ["Access denied" when opening a COM port](#access-denied-when-opening-a-com-port)
- [No data arrives at the PC](#no-data-arrives-at-the-pc)
- [A board stops sending after a while](#a-board-stops-sending-after-a-while)
- [I ran `idf.py set-target` by accident](#i-ran-idfpy-set-target-by-accident)
- [The console won't start](#the-console-wont-start)
- [False alarms, or it misses movement](#false-alarms-or-it-misses-movement)
- [Messages on the console screen](#messages-on-the-console-screen)
- [The zone appears in the wrong place](#the-zone-appears-in-the-wrong-place)
- [Using a different address range](#using-a-different-address-range)

---

## The PC doesn't see the board

1. **Try another USB cable.** Charge-only cables are the number-one cause. The board lights up, but no port appears.
2. Try another USB socket on the PC.
3. Some boards need a **USB driver** for their USB chip (often CP210x, CH340 or CH343). The board's seller or the chip maker's website provides it.
4. Remember: COM numbers belong to the **socket**. The same board in a different socket can get a different number.

## The board shows no banner, or freezes at start-up

- Press **RESET** once while the monitor is open — you may simply have missed the banner.
- If the board connects to WiFi but never prints the banner, check `firmware/sdkconfig` still contains this line:
  ```
  # CONFIG_EXAMPLE_CONNECT_IPV6 is not set
  ```
  With that setting turned on, boards sometimes wait forever at start-up, silently. This happens if the settings file was regenerated — see [set-target](#i-ran-idfpy-set-target-by-accident).

## The board won't join the WiFi

Check, in this order:

1. The network name and password in `firmware/sdkconfig` exactly match the router — including capital letters — and you **rebuilt and re-flashed** after changing them.
2. The router's security is **WPA2-Personal (AES)** — not WPA3, and not a "WPA2/WPA3 mixed" mode.
3. The router is broadcasting on **2.4 GHz**. These boards can't use 5 GHz.
4. The network name uses only plain letters and numbers.

## "Access denied" when opening a COM port

Only **one program at a time** can use a COM port. Close the other one first. To leave the ESP-IDF monitor, press **Ctrl + ]** — not Ctrl + C.

## No data arrives at the PC

The boards are running, but `gv_listen.py` says `...nothing yet`, or the console shows nodes as offline. Check, in this order:

1. **Windows Firewall is blocking Python.** This is the most likely cause.
   Open **Windows Security → Firewall & network protection → Allow an app through firewall**, find **Python**, and tick **Private**. If Python isn't in the list, click **Allow another app** and add the `python.exe` inside the project's `console\.venv\Scripts` folder.
2. **Another program is already listening.** Only one of `gv_listen.py` or the console can run at a time. Close one.
3. **The PC's Ethernet address.** It must be `10.8.0.50`, mask `255.255.255.0`, with **no gateway** (Part 3 of the guide). Check with `ipconfig` in a command prompt.
4. **Two networks using the same addresses.** In `ipconfig`, make sure no other adapter (WiFi, hotspot, VPN) also has a `10.8.0.x` address.
5. **What the board says.** Plug a board into USB and open the monitor. Its status line starts with `GV N1 -> 10.8.0.50`. If it shows a different address, the board found the wrong console.

## A board stops sending after a while

- **On a powerbank?** Many powerbanks switch off when the current drawn is small, and a board draws only 100–150 mA. Use a wall charger.
- **On a USB hub?** Four boards streaming draw about 600 mA; unpowered hubs often supply only 500 mA. Use a powered hub or separate chargers.

## I ran `idf.py set-target` by accident

`set-target` rebuilds `firmware/sdkconfig` from scratch, which wipes your WiFi details and the start-up fix. ESP-IDF saves the old file as **`sdkconfig.old`** in the same folder:

1. Delete `firmware/sdkconfig`.
2. Rename `firmware/sdkconfig.old` to `sdkconfig`.
3. Run `idf.py build` again.

If that doesn't work, download a fresh copy of `firmware/sdkconfig` from GitHub and put your WiFi details back in.

## The console won't start

- **Use the launchers** (`run_simulation.bat` / `run_ghost_vision.bat` on Windows, the `.sh` files on macOS / Linux). They install everything the console needs into a private folder.
- **"Python was not found":** install Python from [python.org](https://www.python.org/downloads/) and tick **"Add python.exe to PATH"** in the installer.
- **On Linux, "Could not create the private Python folder":** run `sudo apt install python3-venv` (Debian / Ubuntu).
- **Everything looks tiny or huge:** start it with a size multiplier, e.g. `python ghost_vision_ops.py --scale 1.3`.

## False alarms, or it misses movement

**First, always recalibrate** with the room empty — after any change to the room, the furniture, the node positions or the router. Then use the sliders on **SETUP / CONTROL**:

| What you see | What to change |
|---|---|
| Movement barely registers | Raise **Gain** |
| False alarms when the room is empty | Recalibrate, then raise **Hits to trigger** to 5–6 |
| The level line is too jittery | **Smoothing** 8, **Window** 30 |
| It reacts too slowly | **Window** 15, **Smoothing** 4 |
| A fake event every hour, on the hour | The router's **group key rotation** is on. Set it to 0 / off (guide, Part 2) |
| Alarms with nobody there, in a room with a fan or pet | Expected — they move too. Calibrate with them present, or keep them out |

## Messages on the console screen

| Message | What it means |
|---|---|
| **NOT CALIBRATED** | That node has no empty-room measurement yet. Calibrate. |
| **WARMING UP** | The node is collecting its first measurements. Wait a few seconds. |
| **SCALE MAXED** | The signal is too strong for the current gain. Lower **Gain**, then recalibrate. |
| **HELD 5+ MIN — RECALIBRATE** | It has reported motion for over five minutes. Usually the trigger is too low — recalibrate with the room empty. |
| **TIMING STRETCHED** | Packets slowed down, so that moment was skipped to avoid a false alarm. Occasional is fine. |
| **RATE COLLAPSED** | A node's data rate fell sharply, so it was ignored to avoid false alarms. Occasional is fine; constant means a weak link — move the node or check its power. |
| **Calibration refused — room was not quiet** | Something moved during calibration. Leave the room and try again. |

## The zone appears in the wrong place

- The **room map doesn't match reality.** Measure again and correct node and router positions in **ROOM PLANNER**.
- **Two boards are swapped.** Check each board's banner matches its label and its position on the map.
- Remember the zone is **approximate** — roughly metre-scale. The ring around it shows how uncertain it is. See [limitations.md](limitations.md).

---

## Using a different address range

The project assumes the router is `10.8.0.1` and the PC is `10.8.0.50`. To use another range, for example a router at `192.168.50.1` and the PC at `192.168.50.10`, change four things:

1. **PC address** (guide, Part 3): use an address in your range, e.g. `192.168.50.10`, still with no gateway.
2. **Console:** start the console once, click **Save settings** on **SETUP / CONTROL**, and close it. Open the new file `console/ghost_vision_settings.json` in a text editor and change
   ```
   "udp_router_ip": "10.8.0.1",
   ```
   to your router's address. Save, and start the console again.
3. **`gv_listen.py`:** near the top of `console/gv_listen.py`, change `ROUTER_IP = "10.8.0.1"` to your router's address.
4. **Firmware:** in `firmware/main/app_main.c`, set `GV_FALLBACK_CONSOLE_IP` to your PC's address, then rebuild and re-flash all four boards. (The boards normally find the console automatically; this is only the backup address.)

---

**Still stuck?** [Open an issue](https://github.com/mkalantary/ghost-vision/issues/new/choose) using the **Question** form. Include what you tried, what you expected, and the last 15 lines of any error message — that's usually enough to find it quickly.
