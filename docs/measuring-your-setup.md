# Measuring your setup

"It works pretty well" is a feeling. "It detected 47 of 50 walk-throughs, with one false alarm in 24 hours" is a fact someone else can check. This page shows you how to get those two numbers for your own room — no special equipment, just a tape measure, a notebook and some patience.

Ghost-Vision's own field numbers haven't been measured yet. **If you measure yours and share them, you help everyone** — there's an issue form called **Share your results** for exactly this.

---

## Before you start

- Your system is set up and calibrated ([GUIDE.md](GUIDE.md), Parts 1–9).
- The room map in **ROOM PLANNER** matches the real room.
- Nothing changes during the test: same furniture, nodes and router in the same places.

---

## Test 1 — Detection rate

**Question:** when someone walks through the room, how often does it notice?

1. **Mark a route.** Put tape on the floor along a path that crosses the room — for example, from the door to the far wall. Number some points along it.
2. **Start recording.** On **SETUP / CONTROL**, click **START RECORDING**. This saves a file with a row for every update.
3. **Walk the route 50 times** at a normal pace. Between walks, leave the room and wait until the console shows **AREA CLEAR** again.
4. **Write each walk down** in a notebook or spreadsheet: the walk number, the time you entered, and whether the console showed **MOTION DETECTED** while you were walking.
5. Click **STOP RECORDING**.

**Your result:** walks detected ÷ 50. For example, 47 of 50 = **94%**.

> Try a few different routes — along the walls, through the middle, slowly, quickly. Paths near the walls are often less sensitive, and knowing that is useful.

## Test 2 — False alarms

**Question:** when the room is empty, how often does it wrongly report motion?

1. Calibrate with the room empty.
2. **ARM** the system, click **START RECORDING**, and leave the room **empty for 24 hours** — no people, no pets.
3. Afterwards, look at the event log, or count the **MOTION DETECTED** events in the recording.

**Your result:** false alarms per 24 hours. For example, **1 in 24 h**.

> A fan, a heater or curtains moving in a draught can cause alarms. Write down anything like that in the room, so your number has context.

## Test 3 — Zone accuracy (optional)

1. Stand still on a few marked, measured points for about 20 seconds each.
2. Note where the console's zone appears, and its **uncertainty ring**.
3. Measure the distance between where you stood and the zone's centre.

**Your result:** the typical distance (the middle value of your measurements) in metres.

---

## Sharing your results

Open an issue using the **Share your results** form and include:

- Your three numbers, and how many walks / hours they're based on
- Room size and construction (drywall, brick, concrete…)
- Where the nodes and router were, and the router model
- Anything unusual: fans, pets, metal furniture, neighbouring WiFi

Results from rooms that perform badly are just as useful as good ones — they show where the limits really are.
