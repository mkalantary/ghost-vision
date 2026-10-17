# Contributing to Ghost-Vision

Thank you for wanting to help! You don't need to be an expert. Some of the most valuable contributions here need no programming at all.

## Ways to help — no coding needed

- **Measure your own setup.** Ghost-Vision's field detection rate hasn't been measured yet. Follow [measuring-your-setup.md](docs/measuring-your-setup.md) and share your numbers using the **Share your results** issue form. Results from rooms where it works badly are just as useful.
- **Try it on something new** — macOS, Linux with real boards, a different router, a different ESP32-S3 board — and tell us what happened.
- **Improve the docs.** If a step in the [guide](docs/GUIDE.md) confused you, it will confuse others. Tell us where, or suggest better wording.
- **Translate the guide.** A Persian (فارسی) version would be especially welcome.
- **Answer questions** from other people in the issues.

## Ways to help — with code

Some areas that would make a real difference:

- **Alarm outputs.** Today the console only alerts on screen. A relay output for existing alarm panels, or notifications, would make it practical to deploy.
- **Securing the sensing stream.** Measurements travel unencrypted inside the sensing network — see [SECURITY.md](SECURITY.md).
- **Splitting up the console.** `console/ghost_vision_ops.py` is one large file (about 6,700 lines). Breaking it into modules would make it far easier to work on.
- **Per-path sensitivity calibration**, which is what's needed before positioning can use all 16 paths. See [lessons-learned.md](docs/lessons-learned.md).

## The one rule that matters most

**Never claim more than you can measure.** This project's credibility comes from saying exactly what it can and can't do. So:

- A change that improves detection should come with **before and after measurements**, not just a convincing explanation. Several changes in this project's history sounded right and made things worse — they're listed in [lessons-learned.md](docs/lessons-learned.md).
- **Change one thing at a time**, so the effect can be traced to its cause.
- Don't add features that imply abilities the signal doesn't have — counting people, identifying them, or showing images. [limitations.md](docs/limitations.md) explains why.

## How to send a change

**First time on GitHub?** That's fine:

1. Click **Fork** at the top of the repository page. This makes your own copy.
2. Make your change in your copy. For a doc fix, you can edit right in the browser: open the file and click the ✏️ pencil icon.
3. Click **Contribute → Open pull request**, and describe what you changed and why.

For bigger changes, please **open an issue first** to talk it over — it saves you from doing work that might not fit.

**Before you send code:**

- Run the console in simulation mode (`run_simulation.bat` or `bash run_simulation.sh`) and check it still starts and detects the virtual person.
- If you changed the firmware, say which ESP-IDF version you built it with, and whether you tested it on real boards.
- Never include WiFi passwords, real MAC addresses or personal settings files.

## Code of conduct

Be kind, assume good intentions, and remember there's a real person on the other side of every comment. Full details in [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Licence

By contributing, you agree that your contribution is licensed under the project's [Apache 2.0 licence](LICENSE).
