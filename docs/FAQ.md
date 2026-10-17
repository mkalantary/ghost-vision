# Frequently asked questions

### Can I try it without buying anything?
Yes. Download the project, then double-click `run_simulation.bat` (Windows) or run `bash run_simulation.sh` (macOS / Linux). Four virtual boards and a virtual person appear. See [Part 0 of the guide](GUIDE.md#part-0--try-it-with-no-hardware).

### Does it use a camera?
No. The sensing uses only WiFi measurements. The console has an optional webcam panel for checking what the sensors reacted to, but it is off by default, it is clearly labelled, and it never feeds the detection.

### Does it record images, sound or faces?
No. What it records are numbers: signal strength and timing. No picture, no audio, no face, nothing that identifies a person.

### Can it see through walls?
Not in the way films show. It doesn't produce any image. A body moving between two boards changes the WiFi signal on that path, and furniture, doors and light walls don't block that much. Reinforced concrete does. The boards must be placed around the space you want to sense — it can't look into a room from outside it. Details: [limitations.md](limitations.md).

### Can it count how many people are in a room?
No, and this isn't something more code or AI could fix — the information isn't in the signal. The console reports whether activity is in one place, several separate places, or widespread. To count people, count them through a doorway with a separate sensor. [limitations.md](limitations.md) explains why.

### How accurate is it?
Motion detection works: it has been used in a real office room with people walking through it, and it detected them. But those tests were informal, so **there's no measured detection rate or false-alarm rate yet** — this is stated openly at the top of the README. You can measure your own setup with [measuring-your-setup.md](measuring-your-setup.md), and sharing your numbers helps everyone.

The zone on the map is approximate: roughly metre-scale, shown with an uncertainty ring.

### Will AI make it more accurate?
AI can help with some things, like telling a person's walking rhythm apart from a fan. But it can't create information that isn't in the signal. A model trained in one room also tends to give confident answers in other rooms where it has no real evidence. See [lessons-learned.md](lessons-learned.md).

### Do I need four boards?
Four is what the project is built and tested with — the console expects four, and the room coverage and the two-path agreement rule both come from having four. Running with fewer is untested.

### Can I use my home WiFi router?
It's much better to use a spare router. The router needs settings — a fixed channel, 20 MHz width, no hourly key change — that you might not want on your home network, and keeping the sensing network separate keeps it private and isolated.

### Can I use a different ESP32 (not the S3)?
The project is built and tested only on the ESP32-S3. Other ESP32 chips that support CSI might work with changes, but that's untested. Please share your results if you try.

### Does it work on macOS or Linux?
The console runs on Linux in simulation mode — tested. Live use with real boards was developed and tested on Windows. macOS and live Linux use are untested; reports are welcome.

### Is the network it creates secure?
It uses WPA2, so outsiders can't join it without the password. Inside the network, the measurements travel unencrypted. That's a known limitation, written down in [SECURITY.md](../SECURITY.md).

### Can I use it in a product or for a client?
The licence (Apache 2.0) allows it, including commercially. Please read [responsible-use.md](responsible-use.md) first, and keep the copyright notices.

One catch: the console uses PyQt5 for its window, and PyQt5 is licensed under GPL v3 or a paid commercial licence. Running it for a client, or sharing your changes as open source, is fine. Selling or handing out a **closed-source** product that includes the console needs a commercial PyQt licence, or a switch to another toolkit. The firmware isn't affected. This is a summary, not legal advice. See the [README](../README.md#license).

### I found a bug or have an idea. What do I do?
Thank you! Open an issue on GitHub — there are forms for bugs, questions, ideas and sharing results. If you'd like to fix it yourself, see [CONTRIBUTING.md](../CONTRIBUTING.md).
