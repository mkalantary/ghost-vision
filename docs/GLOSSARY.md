# Glossary

Every technical word used in this project, in plain language.

| Word | What it means |
|---|---|
| **2.4 GHz** | The radio "lane" the WiFi in this project uses. It passes through furniture and thin walls well. |
| **Baseline** | What the empty room looks like to the sensors. Movement is measured as a change from this. |
| **Calibration** | Measuring the baseline: the system watches the empty room for 30 seconds and learns what "normal" looks like for every path. |
| **Channel** | Which exact frequency inside the 2.4 GHz band the router uses. It must stay fixed, or the baseline is lost. |
| **COM port** | The name Windows gives a board plugged in by USB, like `COM5`. |
| **Console** | The program on your PC that shows everything: `ghost_vision_ops.py`. |
| **Corroboration** | Requiring at least two paths to agree before reporting motion, so one glitchy path can't cause a false alarm. |
| **CSI — Channel State Information** | A detailed measurement of how a WiFi signal arrived: how strong it was and how it shifted, at 64 slightly different frequencies. A moving body changes it. This is what the boards measure. |
| **DHCP** | The router feature that hands out addresses to devices automatically. The boards get their addresses this way. |
| **ESP32-S3** | The small, inexpensive computer chip on each sensor board, made by Espressif. It has WiFi built in. |
| **ESP-IDF** | Espressif's free toolkit for building programs and putting them on ESP32 boards. |
| **Ethernet** | A network cable. Your PC uses one to talk to the sensing router. |
| **False alarm** | Reporting motion when nobody is there. |
| **Firmware** | The program that runs on each board (`firmware/main/app_main.c`). |
| **Flashing** | Copying firmware onto a board, over USB. |
| **Fresnel zone** | The football-shaped region between two antennas where a body disturbs the signal most. This is why a path is a wide band, not a thin line. |
| **Gain** | How strongly the console amplifies small changes. Higher gain = more sensitive, and more false alarms. |
| **IP address** | A device's address on a network, like `10.8.0.50`. |
| **LAN port** | The numbered network sockets on a router (not the "WAN" or "Internet" one). |
| **Link / path** | The radio route between two devices — router to a board, or board to board. Four boards and a router make 10 paths. |
| **MAC address** | A board's permanent hardware address, like `12:34:56:78:9a:bc`. Printed in the board's start-up banner. |
| **Monitor** | A window showing what a board prints over USB. Opened with `idf.py monitor`; closed with **Ctrl + ]**. |
| **Node** | One sensor board. They are named N1, N2, N3 and N4. |
| **Presence** | Detecting that someone is in the room even when they are nearly still. Harder than motion, and still experimental. |
| **Python** | The programming language the console is written in. You need it installed to run the console. |
| **Router / access point (AP)** | The WiFi box that every board connects to. Here it is a dedicated sensing router, not your home WiFi. |
| **RSSI** | Received signal strength, in dBm. Closer to 0 is stronger (−40 is strong, −80 is weak). |
| **sdkconfig** | The settings file for the firmware build, including your WiFi name and password. |
| **Simulation mode** | Running the console with virtual boards and a virtual person, so you can try it with no hardware. |
| **SSID** | A WiFi network's name. |
| **Subcarrier** | One of the 64 slightly different frequencies a WiFi signal is split across. CSI measures each one. |
| **Threshold** | The level of change, worked out during calibration, above which a path counts as "disturbed". Each path has its own. |
| **UDP** | A simple, fast way of sending data over a network. The boards use it to send measurements to your PC. |
| **Uncertainty ring** | The circle drawn around the estimated zone. The person is probably somewhere inside it — this is the honest answer, not the dot in the middle. |
| **WPA2** | A WiFi security standard. The router must use WPA2-Personal (AES) for the boards to join. |
| **Zone** | The approximate area of the room where movement is happening, roughly metre-scale. |
