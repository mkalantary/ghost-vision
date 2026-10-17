# Firmware — the program for the four boards

This folder is the program that runs on each ESP32-S3 sensor board. Each board joins your sensing WiFi, listens to the router **and** to the other three boards, measures how every packet arrived, and sends those measurements to the console on your PC.

**New to this? Follow [the guide](../docs/GUIDE.md), Parts 4–6.** It walks through every step with checks along the way. This page is the short reference version.

It's an ESP-IDF project built on Espressif's [esp-csi](https://github.com/espressif/esp-csi) `csi_recv_router` example.

## Requirements

- **ESP-IDF v5.4.4.** v6.x is untested.
- ESP32-S3 boards. Developed on ESP32-S3-WROOM-1 (N16R8).
- Flash over UART.

## Before you build — three edits

**1. WiFi credentials** — in `sdkconfig`:

```
CONFIG_EXAMPLE_WIFI_SSID="YOUR_SSID"
CONFIG_EXAMPLE_WIFI_PASSWORD="YOUR_PASSWORD"
```

Or set them with `idf.py menuconfig` → Example Connection Configuration.

**2. Board MACs** — in `main/app_main.c`, two tables (`GV_KNOWN` and `GV_NODE_MAC`) hold placeholder MACs. Flash each board once, press RESET, and copy the `sta` MAC from its boot banner into the matching row.

> Don't identify a board by MAC prefix. Four boards bought together showed three different Espressif prefixes.

**3. Node identity** — in `main/app_main.c`, change this line for each board, **save**, then build and flash:

```c
#define GV_NODE_ID   1    /* 1, 2, 3 or 4 */
```

## Build and flash

In the **ESP-IDF Command Prompt**, inside this `firmware` folder:

```
idf.py build
idf.py -p COM5 flash monitor
```

Use your board's COM port. Press **Ctrl + ]** to leave the monitor.

> ⚠️ **Don't run `idf.py set-target`.** `sdkconfig` is already set for ESP32-S3, and `set-target` regenerates it from scratch — wiping your WiFi details and the start-up fix described below. If it happens, see [Troubleshooting](../docs/TROUBLESHOOTING.md#i-ran-idfpy-set-target-by-accident).

> 🔒 Once you've put your WiFi password into `sdkconfig`, don't upload that file anywhere.

✅ **Check it worked:** the boot banner shows the right node number, and in the stats line the node's **own** link reads `0` under `heard/5s` — a node cannot hear itself.

## Router settings

These are not optional. Each one fixed a real fault.

| Setting | Value | Why |
|---|---|---|
| Channel | **Fixed** (e.g. 6) | Auto-channel hops and destroys every baseline |
| Channel width | 20 MHz | 40 MHz gives inconsistent CSI sizes |
| Mode | 802.11n only | |
| Security | WPA2-Personal (AES) | WPA3 / mixed mode breaks ESP32 joins |
| Group key rotation | **0 / off** | The default hourly rotation causes a phantom event on the hour |
| AP isolation | Off | Nodes must hear each other |
| Packet aggregation | Off | |
| SSID | ASCII only, and **not** your organisation's name | A sensor network named after its owner is a signpost |

## Key settings in `app_main.c`

| Define | Default | Meaning |
|---|---|---|
| `GV_NODE_ID` | 1–4 | This board's identity |
| `GV_MULTILINK` | 1 | Capture node-to-node links, not just the router |
| `GV_MAX_PER_SEC` | 80 | Rate cap per link — trims loud links, leaves quiet ones alone |
| `GV_DATA_PORT` | 8266 | Node → console data |
| `GV_DISCOVERY_PORT` | 8267 | Console → node "I am here" announcements |
| `GV_FALLBACK_CONSOLE_IP` | 10.8.0.50 | Used if no announcement arrives within 10 s |
| `CONFIG_SEND_FREQUENCY` | 50 | Pings per second. Tick-aligned — don't raise to 100 |
| `GV_SERIAL_ECHO` | 0 | 1 = also print CSI to serial |

## Design notes

- **The CSI callback runs inside the WiFi driver task and must never block.** It only copies into a queue; a separate task does the sending.
- **Promiscuous filter is `WIFI_PROMIS_FILTER_MASK_ALL`.** `MASK_DATA` looked tidier and silently killed the router link.
- **Frames are fingerprinted by PHY format, not length or rate.** CSI comes from the preamble, so frame length is irrelevant, and for HT frames the MCS must be excluded or ordinary rate adaptation looks like a new transmitter.
- **IPv6 is disabled for the connect step** (`CONFIG_EXAMPLE_CONNECT_IPV6` off). With it on, boot intermittently hung forever waiting for an IPv6 address — no error, no banner.
- **Power save is off** (`WIFI_PS_NONE`).

Wire format: [../docs/data-format.md](../docs/data-format.md)
