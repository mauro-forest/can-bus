# Node addresses

The bus uses 29-bit identifiers that read as four bytes, `PP TT AA BB`
(see `bikecan/ids.py` for the reasoning). `AA BB` is believed to be a node
pair. This file maps those addresses onto the physical components — IoT
(GPS, cellular, accelerometer, BLE, sound), ECU, torque sensor, headlight,
backlight, speed panel and BMS — and records what each claim rests on.

**Nothing here is confirmed yet.** Every row below is inferred from traffic
patterns alone: no stimulus experiment has been run with `bikelog` marks, so
no address has been tied to a component by making that component do something.
The evidence column is the point of this file — a reader must be able to tell a
measurement from a guess.

## Observed node pairs

From the 12 unique legacy recordings (7,588 frames):

| `AA BB` | Frames | Identifiers | Message types | Evidence so far |
|---|---:|---|---|---|
| `10 00` | 7243 | `03FF1000` | `FF` | A 105 ms heartbeat with a payload that never changes. `BB`=00 appears with no other `AA`, so this looks like a broadcast rather than a pair. Unidentified. |
| `36 06` | 103 | `02203606` | `20` | 510 ms period, one byte varying over two values. Present in the recording the operator named "Headlight on and off", which is suggestive and nothing more — the filename is not a timestamped mark. |
| `34 00` | 77 | `04FF3400` | `FF` | Carries the confirmed 10 s uptime counter (see `signals.toml`). A component that reports uptime is more likely a controller than a lamp. Unidentified. |
| `16 06` | 132 | `02181606`, `03121606` | `18`, `12` | Two message types between one pair at 510 ms and 525 ms — a controller polling a peripheral, or two halves of one exchange. |
| `36 03` | 12 | `04FF3603` | `FF` | 60 s period. Slow status. |
| `76 03` | 6 | `08F97603` | `F9` | 298 ms, only ever in one short burst. Priority 08, the lowest seen. |
| `76 00` | 5 | `02407600` | `40` | 600 ms, one short burst. |
| `26 02` | 5 | `08122602`, `02472602` | `12`, `47` | 199 ms burst plus one lone frame. |
| `46 09` | 4 | `02294609`, `05124609` | `29`, `12` | **The most informative pair on the bus.** A zero-length `02294609` is answered 1.5 ms and 0.9 ms later by `05124609` carrying `0100037003E800`. Request and reply share `AA BB` and differ only in `TT`. |
| `16 08` | 1 | `02181608` | `18` | One frame. Note `02181606` shares its `TT` and `AA` and differs only in `BB`, which is what the same message addressed to a different node would look like. |

## What would actually settle this

1. **Stimulus experiments.** Record with `bikelog`, mark the instant each
   component is operated — headlight on, brake squeezed, throttle turned, wheel
   spun by hand — and run `bikecan.experiment.report`. An identifier that only
   appears while a component is being operated belongs to that component. This
   is the fastest route and none of it has been done yet.
2. **Disconnection.** With one component unplugged, the traffic that stops, or
   the error/timeout traffic that starts, names its address. Do this on a bench,
   never on a bike anyone is about to ride.
3. **The `46 09` exchange.** Identify which node polls and which answers, and
   `AA` versus `BB` stops being ambiguous for every other pair at once.

## Message types seen

`TT` values so far: `12`, `18`, `20`, `29`, `40`, `47`, `F9`, `FF`.

`FF` occurs only on the three periodic, unaddressed-looking messages
(`03FF1000`, `04FF3400`, `04FF3603`), which fits `FF` being a broadcast or
status type rather than a command. `12` appears against three different node
pairs, so message types are reused across nodes — `TT` alone never identifies
a component.
