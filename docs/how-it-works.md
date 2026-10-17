# How it works

*Words in this page are explained in the [Glossary](GLOSSARY.md).*

## The idea

Picture a still swimming pool. On one side a machine makes small, steady waves; on the other side a float records the shape of the waves that reach it. While the pool is empty, that shape never changes. The moment someone steps in, the pattern breaks — even if you never see them.

Ghost-Vision does the same with WiFi. The router is the wave machine, the air of the room is the water, and each ESP32-S3 is a float.

## What is actually measured

Every WiFi packet is sent across many narrow frequencies at once. Each receiver reports, for each of those frequencies, how strong the signal arrived and how its phase shifted. This is **Channel State Information (CSI)**.

On this hardware, each packet gives **128 bytes of CSI** — 64 subcarriers, each as a pair of signed 8-bit values (imaginary, real). Each link is sampled roughly **50 times per second**.

A human body is mostly salt water, which absorbs and reflects 2.4 GHz radio. When a body moves near a link, the CSI on that link starts to fluctuate.

## From raw CSI to a decision

For each of the 16 directed paths, the console runs a separate engine:

1. **Normalise each packet by its own mean.** The radio's automatic gain control occasionally jumps; without this step, a gain jump looks exactly like motion.
2. **Measure variance over a short window.** A still room gives a flat, low line. Movement makes it rise.
3. **Compare against that path's own threshold**, measured during calibration in an empty room. Paths differ enormously — in one quiet room, the natural wobble ranged from 2.2 to 11.5 across the 16 paths, a 5× spread caused by geometry alone. A single shared threshold cannot work.
4. **Require persistence, and corroboration where it's needed.** Every path must be disturbed repeatedly, in quick succession, before it counts. On top of that, the 12 board-to-board paths — the most sensitive and the noisiest — only report motion when at least two paths agree. A real body blocks several crossing paths at once; a single sensitive path firing on its own is the signature of a glitch. The 4 router paths can each report motion alone, as they did before the board-to-board paths existed.

Two protective gates sit in front of the decision. If a window took much longer than normal to fill (because packets slowed down), or if a path's packet rate collapsed, that window is not scored. Both were added after they were found to be the root cause of phantom alarms.

## Why four nodes and sixteen paths

One path is one line. Someone who doesn't cross it isn't seen.

Four nodes in a room give 4 links to the router plus 6 links between nodes — 10 physical paths that cross each other. Each node-to-node pair is measured from both ends, giving 16 directed measurements. In simulation, the 4 router paths alone left 22 of 80 test points more than 0.75 m from any path; all 16 paths left none.

Four is a recommendation, not a minimum. One board and the router form a single path: it detects movement that crosses that line, but it can't place it, because anywhere along one line fits equally well. Two boards give 3 paths and a rough zone; three give 6. The paths grow faster than the boards — that's why the fourth board is worth adding.

## Zone estimation

The console estimates which part of the room the activity is in by finding where the disturbed paths cross, weighted by how strongly each is disturbed. The output is a coarse zone with an **uncertainty ring** — the ring is the honest answer, not decoration. See [limitations.md](limitations.md) for why this stops at metre scale.
