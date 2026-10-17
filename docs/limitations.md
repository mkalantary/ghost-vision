# Limitations — and why

*Knowing what a tool can't do is how you use it well. Words in this page are explained in the [Glossary](GLOSSARY.md).*

Everything here comes from the physics of the signal, not from unfinished software. More code, more boards or machine learning will not remove these.

## Four hard limits

**1. You must control both sides.** The signal has to pass *through* the sensed space to a receiver you placed. There is no "walk up to a wall and scan" mode. A body outside the geometry is invisible.

**2. No range, no velocity.** Radar knows how far and how fast by timing echoes and measuring Doppler shift. This system measures only "this line was disturbed by this much." Range and velocity are not weak in the data — they are absent.

**3. Bodies don't add up.** Two people are not "twice one person." The second stands in the first one's radio shadow, so the amount of disturbance cannot be inverted into a headcount.

**4. Reinforced concrete stops it.** Rebar is a metal grid. Drywall, wood, glass, doors and furniture are fine; reinforced concrete and steel are not.

## Common questions

### Can it count people?
**No.** Two people disturb paths that cross in four places, not two — and amplitude alone can't tell real crossings from ghost ones. With three people, nearly every path in a normal room lights up at once: not three blobs, one smear. Going from 4 to 16 paths did not change this.

What the console does instead is report **activity regions**: no activity, one region, N separated regions, or widespread activity. Regions are not people, and the console says so.

If you need a count, count **crossings at a door** with a separate sensor, and report it as a separate number.

### Can it show an image or outline?
**No.** CSI contains no spatial image.

### Can it give exact coordinates?
**No.** A coarse zone with an uncertainty ring, roughly metre scale. The ring is the real answer.

### Can it detect a hidden bug or a phone in someone's pocket?
**No.** A passive receiver or a silent phone emits nothing for CSI to see. Bug detection is a separate discipline (TSCM).

### Will AI fix this?
**No.** Machine learning learns a rule from data; it cannot create information the signal doesn't contain. A model trained in one room produces confident-looking answers everywhere — including where it has no evidence. Fingerprint-based localisation does work, but needs a training walk in every room, forever.

### Is it covert?
**It's unremarkable, not invisible.** Anyone with a WiFi analyser sees a router and four clients. What they can't tell is that it's a sensor. Don't name the network after yourself.

## Practical limits

- **Large animals and fans** can trigger it. Calibrate with them in mind.
- **Moving the nodes invalidates the calibration.** Their positions are part of the measurement.
- **Powerbanks are for demos.** Many switch off below 50–75 mA; a node draws 100–150 mA. Permanent installs need wall power.
