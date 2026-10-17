# Data formats

*This page is for programmers who want to read Ghost-Vision's data in their own tools. You don't need it to build or use the system.*

## 1. Node → console: UDP wire format (protocol 2)

Sent to UDP port **8266**. Little-endian. Up to 8 records per datagram; a part-full datagram is flushed after 150 ms.

### Header — 12 bytes

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | `'G'` |
| 1 | 1 | `'V'` |
| 2 | 1 | Protocol version (`2`) |
| 3 | 1 | Node ID of the **receiver** (1–4) |
| 4 | 1 | Number of records in this datagram |
| 5 | 1 | CSI bytes per record (`128`) |
| 6–7 | 2 | Reserved (`0`) |
| 8 | 4 | `uint32` sequence number of the **first** record |

Sequence numbers are consecutive, so loss is counted from gaps.

### Each record — 130 bytes

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | `int8` RSSI (dBm) |
| 1 | 1 | **Link ID** — who transmitted: `0` = router, `1`–`4` = node N1–N4 |
| 2 | 128 | CSI: 64 subcarriers × (`int8` imaginary, `int8` real) |

In protocol 1, byte 1 was an always-zero pad. Protocol 2 reused it as the link ID, so the packet did not grow. The console decodes both.

## 2. Console → node: discovery

The console announces itself on UDP port **8267** every 2 seconds, broadcast to the subnet, carrying the magic string `GHOSTVISION`. If a node hears nothing for 10 seconds it falls back to the compiled-in console address.

> On a PC with two network adapters, bind the announcer to the sensing adapter's IP *and* send to that subnet's broadcast address. `255.255.255.255` leaves through whichever adapter the routing table picks — often the wrong one.

## 3. Console recording CSV

Written by the console's **Record** button. One row per update.

| Column(s) | Meaning |
|---|---|
| `time`, `epoch` | Wall-clock time and Unix time |
| `system_state` | Overall decision |
| `armed` | 1 if armed |
| `nK_state`, `nK_level`, `nK_thr`, `nK_rate`, `nK_rssi`, `nK_conf` | Per node K = 1–4: state, current level, threshold, packets/s, RSSI, confidence |
| `zone` | Coarse zone label |
| `track_state`, `track_x`, `track_y`, `track_r` | Zone estimate: state, position (m), uncertainty radius (m) |
| `track_speed`, `track_heading` | Estimated movement |
| `solution_quality`, `ambiguous` | How trustworthy the zone estimate is |

## 4. Raw CSI CSV (esp-csi serial format)

`data/sample/raw_csi_router_link.csv` uses Espressif's standard serial output: one `CSI_DATA` row per packet. Key columns:

| Column | Meaning |
|---|---|
| `mac` | Transmitter address (anonymised in the sample) |
| `rssi` | Received signal strength, dBm |
| `rate`, `sig_mode`, `mcs`, `bandwidth` | PHY format of the frame |
| `noise_floor` | Receiver noise floor, dBm |
| `channel` | WiFi channel |
| `local_timestamp` | Receiver clock, µs |
| `sig_len` | Frame length |
| `len` | CSI byte count (128) |
| `data` | The CSI array, as `[imag, real, imag, real, …]` |
