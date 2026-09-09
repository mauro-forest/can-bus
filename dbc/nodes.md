# Node addresses

The bus uses 29-bit identifiers that read as four bytes, `PP TT AA BB`
(see `bikecan/ids.py`). This file maps those onto the physical components and
records what each claim rests on.

Four components are **confirmed** -- by tying a frame to a commanded action, to
a measured value, or by disconnecting the component and watching its traffic
disappear. The method that worked is not staring at payloads: it is the IoT
serial console. The tracker speaks the Queclink @Track protocol, it logs every
remote command by name with a timestamp on the same clock as the CAN log
(`+ACK:GTRTO,...,HLON,...`), relays BMS frames into its reports verbatim
(`+RESP:GTBMI`), and reports measured values the bus can be fitted against.
See `bikecan/queclink.py`.

## Two bikes in the corpus

**Read this before comparing any session across the 7/9 September line.** The
9 September sessions were recorded from a different bike. An audit of the
whole corpus against this file found it; no session label says so.

| | 7 September (nine sessions and the 4 September legacy set) | 9 September (three sessions) |
|---|---|---|
| IoT IMEI | `865969076362128` | `864864077015703` |
| Firmware groups, serial and `03FF1501` / `05124501` | `4A / 04 / 0103` | `51 / 04 / 0104` |
| `GTFRI` total mileage | 0.1 km | 4819.3 to 4819.5 km |
| ECU error code while unlocked | `0210…` in 32 of 32 reports | `0000…` in 45 of 45 reports |
| `03FF1000` heartbeat bytes 0-1 | `08 40` (`00 40` for 2 s after a cold boot) | `00 00`, all 7,241 frames |
| After `LOCK` | sleeps to five identifiers, heartbeat continues | whole bus silent 14 to 20 s after every LOCK, heartbeat included |
| `04FF3400` uptime | resets only on pack power | restarts from 0 at every UNLOCK |
| `0260B606` byte 0 | 1 | 0 |
| `02FF2605` byte 4 | `07` | `09` |
| `05124616` | 12486 | 7591 |
| `05124618` | 3540 to 3861 | −243 to 30 |
| `02203606` byte 5 while awake | 1 | 0 |
| `02F82400` | 965 to 994, then 0 to 11 | 36291 to 36348 |

Identical on both: hardware version `C3 / EB / 05`, BMS model `RP13S35A`, both
lamp bytes, the HubLock and its burst pattern, the unlock and lock sequence
timings, the request/reply families and the identifier inventory (the
9 September sessions add no new identifier). So the bus layout is the model's;
the differences above are the bike's or the firmware's.

Three consequences are recorded as corrections in the sections below: the
five-identifier sleep, the ECU faults and the heartbeat's `SystemReady` bit
are all bike-1 observations. And a message that was "constant" on one bike
and holds a different constant on the other (`0260B606`, `02FF2605` byte 4,
`05124616`) is a configuration or identity value, the same lesson already
recorded against the max-speed byte.

## Confirmed components

| Address | Component | Identifiers | How it was established |
|---|---|---|---|
| `AA` = `36` | **Headlight** | `02203606` | `HLON` at 1788785214.368 and `HLOFF` at 1788785234.208 were followed by byte 1 of `02203606` going to 1 after 0.77 s and to 0 after 0.33 s. Nothing else on the bus changed in either window. Cross-checks against `<ECU Info>` field 5. |
| `AA` = `16` | **Rear light** | `02181606` | `RLONEN` at 1788785392.364 and `RLOFF` at 1788785412.125 were followed by byte 4 going to 1 after 0.76 s and to 0 after 0.40 s. Nothing else changed. Cross-checks against `<ECU Info>` field 6. |
| `AA` = `64` | **HubLock** | `13B76400` | **Confirmed by disconnection.** With the HubLock disabled this was the only identifier to vanish: 95 identifiers became 94, none appeared in its place, and both lamps kept toggling. Re-checked as a set difference over the whole recordings: `session2 - session3 = {13B76400}` exactly, and `session3 - session2` is empty. Note it is not periodic: it sends ~20 frames after `UNLOCK` and three bursts of three after `LOCK`, and nothing otherwise, so a locked idle recording never shows it whether or not the HubLock is fitted. |
| `AA` = `45`, `46` | **BMS** | `05FF46xx`, `05124[56]xx` | `+RESP:GTBMI` relays the `46` payloads **verbatim**. Thirteen cell voltages sum to the pack voltage `05FF4610` reports separately. `05124502` at the identity address is the ASCII string `RP13S35A` -- the pack's own model number, and `13S` is 13 cells in series. Pack current matches `<Battery Status>` byte for byte in 27 of 29 reports; state of charge matches `<Scooter Battery Percentage>` at 88, 98, 99 and 100 %. |

Both lamps also switch on with `UNLOCK` and off with `LOCK`, because unlocking
wakes the whole bike. That is why a periodic `GTFRI` still showed
`head_light_status=1` after `HLOFF`: the unlock that followed turned it back
on.

**The lamps are not lamp nodes.** Byte 0 of each light message carries a wake
state, not the light, and `AA` = `16` also publishes a pack voltage
(`03FF1603`) while `AA` = `36` publishes the fastest message on the bus
(`04FF3604`, 30 ms). Neither is what a lamp sends. These are two controllers,
each reporting a lamp it drives. See the channel scheme below.

**And the two wake bytes are not one fact.** When the bus is woken without an
`UNLOCK` (see "An IoT reboot wakes the bus"), `02181606` byte 0 goes to 1 and
`02203606` byte 0 stays at 2. Component 1 wakes; component 3 does not. Each
byte reports its own controller.

## `AA` reads as `<component><channel>`

This replaces the earlier "`AA` names a subsystem and `BB` selects an item"
note, which was right as far as it went but did not see the structure inside
`AA` itself.

The version strings prove it. `<ECU Info>` gives `<Firmware Version>` and
`<Hardware Version>` as three 8-hex-digit groups each, and all six groups are
assembled byte for byte from six CAN replies at three addresses:

```
serial fw = 0000004A 00000004 01030000
serial hw = 000000C3 000000EB 05000000
            03FF1501 04F93501 05124501   <- BB=01, firmware
            03FF1500 04F93500 05124500   <- BB=00, hardware
              AA=15    AA=35    AA=45
```

The only difference anywhere is that the two-byte BMS replies are
zero-extended to four in the report. So `BB` = `00` is the hardware version and
`BB` = `01` the firmware version, at three addresses ending in `5`. And `AA` =
`45` is certainly the battery, because `05124502` next to it is the pack's own
model string. So the low digit of `AA` is a channel:

| Channel | Meaning | Instances |
|---|---|---|
| `x4` | A counter | `14`, `24`, `34`, `44` |
| `x5` | Identity: hardware and firmware version, model, serial | `15`, `35`, `45`, `75` |
| `x6` | Live data | `16`, `36`, `46` |

Channel `x4` was "a 10-second counter". Three of the four are; `44` is a frame
index, not a clock (see the counter section). The channel still holds -- every
`x4` carries a counter -- but not every one counts seconds.

Which gives four components, one of them confirmed:

| Component | Counter | Identity | Live data | What it is |
|---|---|---|---|---|
| 1 | `14` | `15` | `16` | Rear light bit, a pack voltage, 11 identifiers. **Hypothesis: the speed panel.** Its counter and its latched voltage both reset on pack power and survive an IoT reboot. |
| 2 | `24` | -- | `26`, `27` | The unlocked-time counter, the 10 s nonce, the `MEULK` challenge. **Probable: the IoT module.** Its counter survived four pack removals and reset across three IoT reboots, which only the component with its own backup battery can do. No `25` identity poll exists because the master does not poll itself. |
| 3 | `34` | `35` | `36` | Headlight bit, the 30 ms message, the uptime counter. **Hypothesis: the ECU.** |
| 4 | `44` | `45` | `46` | **The BMS, confirmed.** |
| 7 | -- | `75` | -- | Answers identity polls only. Found at cold boot; nothing else known. |

Component 3 keeps its counter running while the bus sleeps, and it owns both
the fastest and one of the slowest messages on the bus. That is what a
controller looks like, and it is why the confirmed uptime counter is
attributed there. `<ECU Error Code>` names a meter and an ECU as separate
nodes, which is where the two hypotheses come from; **which of components 1
and 3 is which is not established.** The `<ECU Info>` group order would settle
it, and that is in the spec PDF in `reference/`, not yet extracted.

**A correction: component 3 is not permanently powered.** That was the earlier
wording, inferred from its counter running through sleep. Pull the pack and
`04FF3400` stops with everything else and restarts from 0. It is supplied from
the pack like the rest of the bike; only the IoT module is not.

Component 7 is new and thin. It answers `02417500` and `02417501` with
`0x000000F4` and `0x00000015`, in the same shape as every other identity
reply, and it does nothing else on the bus. It cannot be matched against
`<ECU Info>`, which carries three version groups and has all three already
spoken for by `15`, `35` and `45`.

**What argues against the scheme.** `AA` = `10`, `B0`, `B6` and `A6` do not
fit it at all. `AA` = `26` has a `BB` of `05`, which under a strict reading of
the table should not happen. `AA` = `24`, `26`, `27` used to be listed here as a
component whose channels do not line up (`24` is a counter, but there is no
`25`); the reboot session resolved that -- see component 2 above -- and the
missing `25` is what you would expect of the node that sends the polls.

The cold-boot sweep adds three more misfits: `AA` = `60`, `86` and `95` are
polled and never answer, and their channel digits are `0` and `5` and `6`
without a matching set anywhere. `95` is polled 24 times at `BB` = `00` and
`01`, which is the identity pattern exactly, so a component may simply be
absent -- but `60` is polled at `BB` = `01`, and `86` at `BB` = `01` and `02`,
which is neither an identity nor a live-data index.

### `BB` and the request/reply pattern

`BB` is echoed from request to reply, in five families, all answered in under
2 ms:

```
02 29 45 xx  ->  05 12 45 xx     xx = 00..04
02 29 46 xx  ->  05 12 46 xx     xx = 01..07, 09..18
02 21 35 xx  ->  04 F9 35 xx     xx = 00..04
02 19 15 xx  ->  03 FF 15 xx     xx = 00..04
02 41 75 xx  ->  08 F9 75 xx     xx = 00..01
```

The last family, and the `46` indices `01`-`07`, come from the battery-removal
session. Those requests are sent only when the pack is first connected, which
no earlier recording caught -- see "Cold boot polls the whole bus" below.

`AA` and `BB` both survive the round trip; only `PP` and `TT` change, in fixed
pairs (`29`->`12`, `21`->`F9`, `19`->`FF`).

`BB` is an index whose meaning depends on the channel. On an identity channel
it selects a property: `00` hardware version, `01` firmware version, `02`
model string. On the BMS live channel it selects a data block: `02`-`05` are
the cell voltages, `06`-`07` temperatures, `09` charge state, `10` pack
voltage and current. For the two confirmed lamps `BB` is `06` in both cases
while `AA` differs, so `06` looks like a common index within a live-data
channel rather than a peer address.

**A caveat on the tooling.** `discover.request_response()` mis-pairs when a
fast periodic message is on the bus. It reported `02213501 -> 03FF1603` at
3 ms, when the real reply is `04F93501` at 9 ms -- a 105 ms periodic simply
landed closer. Check that a claimed pair shares its `AA BB` before trusting it.

## Three kinds of 10-second counter

Three identifiers carry a 10-second tick and they do **not** count the same
thing. This matters: any of them looks like an odometer or an uptime at a
glance, and only one of them is elapsed time.

| Identifier | Encoding | Counts | Evidence |
|---|---|---|---|
| `04FF3400` | bytes 2-3, big-endian | **Elapsed time.** Uptime. | 845/845 steps equal the wall clock exactly. Continuous across the three 7 September sessions to the tick: +174 over a 1740 s gap, +64 over 640 s. |
| `02F82400` | bytes 0-1, **little-endian** | **Unlocked time.** | Advanced 2 over a 2390 s sleep and 1 over 947 s, while `04FF3400` advanced 174 and 64. Runs 965 -> 994 across four sessions **including four pack removals**, then reads 0 in the first session after the three IoT `REBOOT`s. Held in the IoT module's RAM. Absent during wakes that are not unlocks. |
| `03FF1400` | bytes 0-3, big-endian | **Awake time.** | Same test: +1 over 2402 s of sleep. Reset to 0 by the pack removals (16 -> 0), untouched by the IoT reboots (2 -> 3 -> 13). Pack-powered. |

**A correction: pack power is not the only thing that resets `04FF3400`.** On
the 9 September bike (see "Two bikes in the corpus") it read 0 at the first
tick after every one of the eight UNLOCKs, because that bike's bus goes
completely silent 14 to 20 s after each LOCK and `04FF3400` restarts with it.
So on bike 2 it counts time since the last unlock, not since the pack was
fitted. The reading below is from bike 1 and stands for bike 1.

`04FF3400` is the volatile one. It resets to 0 on pack power-on, every time:
in the battery-removal session it read 2282 before the first removal, and
after each of the four restores its first frame arrived +10.009, +9.992,
+10.010 and +10.011 s later carrying 0. That also explains the drop from 2878
to 509 between 4 and 7 September -- the pack had been off the bike. It is
uptime since the pack was connected, and it runs through sleep but not through
a disconnection.

`02F82400` sits at the other extreme: it crossed all four removals without
resetting. It was recorded here as non-volatile, and that was wrong: it read
994 at the end of the battery-removal session and 0 at the start of the
unlock/lock session, and the only recording between the two is the reboot
session with its three IoT `REBOOT`s. A counter that survives the pack being
pulled and not a software restart of the tracker is in the tracker's RAM,
which is the one RAM on the bike that the pack does not power. The reset
itself was not caught on tape; the inference is from what lies between 994
and 0.

**A correction: `05FF4400` is not a counter of time and never was.** It was
listed here as a fourth 10-second counter that reset after a long sleep. It is
a frame counter. It steps by exactly +1 per frame -- in the battery-removal
session across transmit intervals of 2.7 s and of 68.3 s alike. No clock does
that. The earlier reading survived three sessions only because the message
usually goes out about every 10.5 s, so the count and the wall clock advanced
together by coincidence.

It is not, however, "the frame index within the session", which is how it was
first corrected here: the reboot session runs 12-17, continuing from the
battery-removal session's 11 across 430 s and three IoT reboots. It reset to 0
at the first frame after `UNLOCK` in five sessions, each following a sleep of
640 s or more, and did not reset at the `UNLOCK` in the battery-removal session
(BMS already awake) or across a 495 s sleep in the reboot session. Whatever
resets it is on the BMS side and looks like a sleep timer between 495 and
640 s. That is a bracket from one pair of gaps, not a measurement.

The deep-sleep threshold that was derived from it -- "between 51 s and 947 s"
-- rests on nothing, and the experiment proposed to narrow it would have read
a frame index as elapsed time. Both are withdrawn. The 495-640 s bracket above
is a different claim, resting on when the frame count resets rather than on
its value, and it is the one lead left on the sleep threshold.

## The sleeping bus is exactly five identifiers

An idle locked bike sends only:

```
03FF1000   105 ms    heartbeat, payload 0840112200000001 once booted
04FF3400   10 s      the uptime counter
04FF3603   60 s      payload 0300000000000000
02294609   300 s     the IoT module polling the BMS
05124609   300 s     the BMS replying with state of charge and health
```

Unlocking wakes 89 more, so any inventory from a locked bike is a small
fraction of what exists. The 30 ms `04FF3604` is the best **unlock** detector
on the bus: its active runs begin 2.05-3.2 s after each `UNLOCK` and end
0.26-0.81 s after each `LOCK`, over ten unlocks and eight locks. It is not a
bus-activity detector: the two ways of waking the bus without an unlock (next
section) bring up ~65 identifiers and this is not among them.

Five is the count between wakes. It is not the count for the first 190 s after
a pack restore, nor for ~5 s after an IoT reboot; see the next section. And
with the HubLock fitted, the seconds after a `LOCK` add `13B76400`'s bursts.

**A correction: this is the 7 September bike.** The 9 September bike does not
sleep, it stops. After every LOCK in its two command sessions (seven LOCKs
followed by a recording long enough to see) the heartbeat, `04FF3400` and the
HubLock bursts ran for 13.8 to 19.6 s and then the bus fell silent, heartbeat
included, until the next UNLOCK. Its 300 s BMS poll was never seen while
locked. Whether that firmware powers the bus down on lock or the pack
contactor opens is not known; the `GTFRI` main power voltage stays at 52.8 V
through those silences, which argues against the contactor. The
longest heartbeat gap on bike 1 outside a MEULK blackout is 0.3 s.

That 300 s poll is worth noticing. It is how the tracker can report a battery
percentage on a locked bike, and it explains why `GTFRI` gives a pack voltage
and a percentage while zeroing the whole `<ECU Info>` compound: the battery
answers in its sleep, the ECU does not.

**This accounts for the 4 September corpus completely.** Its 13 identifiers are
these five, plus the four in the `MEULK` exchange below, plus the four light
identifiers (`02203606`, `02181606`, `03121606`, `02181608`). Nothing in that
recording is unexplained, and the bike was never unlocked in it.

## Unlock and lock, second by second

The 9 September unlock/lock session (`2026-09-09T13-24-04_unlock-lock`) ran
four UNLOCK/LOCK cycles with the HubLock fitted and the console reporting
every command and state report. Timed from each `+ACK:GTRTO`, the four
repeats agree to within about 10 ms at every step. This is the sequence the
bike follows.

**UNLOCK**

```
+0.003    +ACK:GTRTO UNLOCK on the console
+0.174    02294503 -> 05124503  x5 at 5 ms     the IoT module polls the BMS manufacture block
+0.27     03FF1000                              heartbeat starts
+0.29     02181606, then 03121606               component 1 up
+0.40     03FF1001 03FF1603 03FF1605 05FF4610
+0.47     08F97603  0101000000000000  x3 at 300 ms
+0.55     05FF4000/4001, 05FF4602-4609          BMS broadcasts
+0.87     05FF4400 05FF4600 05FF4601
+1.17     03FF1600-1604                         speed-limit block
+2.06     02294609 -> 05124609                  state of charge
+2.6-2.7  13B76400 = 01, 04FF3604 starts, 02203606 wakes   component 3 and the HubLock
+2.7      +RESP:GTULS on the console, 12 ms after the first HubLock frame
+2.8-2.9  1FF8B028, once; then identity polls every 0.76 s until +20 s
+3.7      +RESP:GTFRI; +RESP:GTMLS at about +8
+10.17    04FF3400 and 03FF1400 first tick
```

**LOCK**

```
+0.003     +ACK:GTRTO LOCK
+0.011     +RESP:GTLOR
+0.2-0.5   +RESP:GTLOC, 70-85 ms BEFORE the HubLock reports
+0.3-0.54  13B76400 = 00; 04FF3604 stops
+0.4-0.65  1FF8B028, once
+1.3-1.8   component 1 and 02203606 go quiet; 03121606 is the last
+3.6-3.9   the HubLock's second burst of three
```

From +2 s only the sleep set remains: `03FF1000` at 4 Hz, `04FF3400` every
10 s, and the HubLock bursts. The whole bike is asleep under two seconds after
a LOCK. On this bike (the 9 September one) that state lasts another 12 to
18 s and then even the heartbeat stops; see the correction in the previous
section.

Two things follow. `GTULS` is the IoT module reporting the unlock after it has
seen the HubLock confirm it, so it is a report of state. `GTLOC` comes before
the HubLock reports, so it is a report of intent -- the same caveat as
`ECU Lock State` in `GTFRI`. `GTULS`, `GTLOR` and `GTLOC` are not yet parsed
by `bikecan/queclink.py`.

The unlock sequence up to +1.2 s is the tracker's boot sequence from "Cold
boot polls the whole bus" below: the same first step (the manufacture block,
five times) and the same `08F97603` handshake. What an UNLOCK adds, at
+2.6 s, is component 3 and the HubLock, which those wakes never bring up.

## An IoT reboot wakes the bus

The reboot session sent `REBOOT` to the tracker three times with the bike
locked throughout, and each time the bus came up. Measured on `05FF4610`, the
BMS's 273 ms message:

| `REBOOT` ack | `+RESP:GTPNA` (tracker back) | Bus active | Identifiers |
|---|---|---|---|
| rel 7.8 | rel 26.1 | rel 28.1 - 32.7 | 65 |
| rel 94.2 | rel 112.2 | rel 114.1 - 119.0 | 65 |
| rel 592.8 | rel 611.1 | rel 613.0 - 617.9 | 64 |

Between the bursts the bus is the five-identifier sleep set: heartbeat,
uptime, `04FF3603`, and the 300 s BMS poll pair once in the 460 s quiet
stretch. (An earlier version said three; the poll pair had been missed.)
The identifiers in each burst are the pack-restore sweep of the battery-removal
session, less the `MEULK` handshake: identity polls at `45`, `75`, `95`, the
BMS live blocks `4601`-`4607` and `4610`, `13B16001`, plus `03FF16xx`,
`02181606` and the `02FF2602` nonce. One identifier is new, `0258A604`, all
zero, 15 frames.

Three things follow.

1. **The master is the IoT module.** The same sweep runs when the pack is
   restored and when the tracker restarts, and only the tracker is involved in
   both. So "cold boot polls the whole bus" below is the tracker's boot
   sequence, run whenever it comes up or sees main power return.
2. **Not every component wakes.** `04FF3604`, `02F82400`, `13B76400` and the
   awake state of `02203606` never appear in these wakes, while `02181606`
   publishes its full awake payload (`0100190001030200`: byte 0 = 1, rear light
   bit set, max speed 25). Component 1 and the BMS answer the tracker;
   component 3 does not.
3. **After a pack restore the bus stays in this state.** In the battery-removal
   session the ~65 identifiers ran from the last restore (rel 92) to the
   `UNLOCK` (rel 283) without a break. The five-identifier sleep is what the
   bus settles into, not what it boots into.

A caution that comes with this: `GTFRI` in those states reports a headlight
on and a rear light off while `02203606` byte 1 reads 0 and `02181606` byte 4
reads 1 -- the opposite of the bus, in both directions. The console's
`<ECU Info>` block is a cached copy (see `05124615` in `signals.toml`, where
that is shown for the temperatures), so these are stale values from the last
unlock, not a contradiction to resolve. Do not compare `<ECU Info>` fields with
the bus unless the bike is unlocked.

## `AA` = `26` is an authentication channel

`02FF2602` carries eight high-entropy bytes, fresh every time, every 10 s while
the bike is awake -- and while the bike is unlocked it arrives 30 ms after each
tick of `02F82400`. A counter paired with an unpredictable value is the shape
of a rolling code, and the counter is what stops one being replayed. The
pairing is not a dependency, though: during the IoT-triggered wakes above the
nonce ran 39 times with `02F82400` absent from the bus. What the two share is
an owner, the IoT module (component 2 above).

`MEULK` produces a fixed five-second exchange on the same channel, identical in
structure in both corpora apart from the challenge:

```
02472602  <8 high-entropy bytes>   x1     C2D31EB512687FF3 / 52539E5502CBE49B
08122602  0100000000000000         x4     first reply 0.07 s later
02407600  0100000000000000         x5
08F97603  0101000000000000         x6
```

It runs with the bus otherwise **asleep** -- 27 s after `LOCK` in the
7 September session -- so whatever it releases does not need the ECU awake.
`MEULK` is the @Track command for releasing the battery lock, and `<ECU Info>`
has separate Battery-Lock State and Battery-Lock Door fields, so the battery
latch is what to check next.

**Do not transmit on this identifier.** If this is an immobiliser handshake
then replaying or forging it is the interesting part, and forging an
immobiliser is not what this project is for. The hard constraint in `CLAUDE.md`
covers it: nothing here writes to CAN, and if transmit is ever needed it goes
in a separate, clearly-named tool.

## Pulling the battery: what `MEULK` really does

The 7 September battery-removal session settled the open question left by the
authentication channel. Four `MEULK` commands, four pack removals, and the
result is the same every time.

| # | `+ACK:GTRTO` | Bus goes silent | Latency | Silent for |
|---|---|---|---|---|
| 1 | rel 71.57 | rel 74.55 | +2.98 s | 16.3 s |
| 2 | rel 138.78 | rel 141.78 | +3.00 s | 20.4 s |
| 3 | rel 187.41 | rel 190.53 | +3.11 s | 18.5 s |
| 4 | rel 237.36 | rel 240.40 | +3.04 s | 9.2 s |

"Silent" means the whole bus, heartbeat included -- not a sleeping bus, which
still sends five identifiers. There is no unexplained blackout in the session
and no `MEULK` without one.

The serial console confirms the cause independently: `+RESP:GTFRI` reports
`<Main Power Voltage>` = 0.000 in every one of the four blackouts and 53.5 V
outside them. So `MEULK` releases the battery, which is what the @Track name
says and what `<ECU Info>`'s Battery-Lock fields implied, and the CAN exchange
at `02472602` / `08122602` / `02407600` is the handshake that does it.

The 3.0 s delay is not the operator. It varies by 0.07 s over four trials, and
it falls at the end of the CAN exchange rather than at a fixed offset from the
command. What is **not** established is the mechanism: whether the contactor
opens and a dead pack is then lifted out, or the latch releases and the
contacts break as the pack moves. Releasing the pack without removing it would
separate the two.

**Do not read this as a way to cut power to the bike.** The constraint in
`CLAUDE.md` still holds: nothing here transmits, and this recording came from
commanding the tracker over its own channel, not from putting frames on CAN.

### The IoT module has its own supply

Serial output continued through all four blackouts -- `GTEPF`, `GTFRI`,
`GTMLS` and `GTSTT` all arrived while the pack was out -- and `GTFRI` reported
`<Backup Battery Voltage>` = 4.07 V and 91 % throughout, unchanged. The
tracker runs on that cell.

It also kept reporting stale pack figures. During every blackout `GTFRI` still
carried the last known battery percentage and mileage while `<Main Power
Voltage>` read 0.000 and the whole `<ECU Info>` compound was zeroed. This is
the same caution already recorded against `<ECU Lock State>`, in a sharper
form: **the tracker's battery fields do not tell you the pack is fitted.**

### Cold boot polls the whole bus

Reconnecting the pack runs a fixed sweep that no earlier recording had caught,
because no earlier recording contained a power-up. The reboot session later
showed the same sweep run by an IoT `REBOOT` alone, so it is the tracker's
start-up enumeration; see "An IoT reboot wakes the bus". It is worth more than the
removal itself: the session logged **121 identifiers against 94-98** in every
previous session, and 23 of those had never been seen at all. All 23 belong to
this sweep.

The boot repeats closely at all four restores. Timings below are from restore
1; the other three agree to within a few tens of milliseconds except where
noted:

```
+0.000  02407600  0100000000000000       tail of the release handshake
+0.018  02294503 -> 05124503   x5        BMS manufacture block, five times
+0.117  03FF1000  0040112200000001       heartbeat starts, SystemReady clear
+0.311  08F97603  0101010100000000       payload 0101000000000000 at restore 3
+0.71   02294609 -> 05124609             state of charge (+0.38 to +0.84)
~+2.0   03FF1000  0840112200000001       SystemReady sets (+1.70 to +2.22)
+10.0   04FF3400  0000000000000000       uptime counter's first tick, at 0
```

Then the master walks the BMS live channel -- `02294601` through `02294607`
and `02294610`, roughly every 0.5 s -- and polls `02417500/501`,
`02519500/501` and `13B16001`. Those last three had never appeared before.

**There are two sweeps, and they are not the same.** The pack-restore sweep
above ran four times. A second, different sweep ran once, 3 s after the
`UNLOCK` at the end of the session, and covers the identity channels and the
BMS's deeper blocks: `02191500`-`504`, `02213500`-`504`, `02213600/601`,
`02294502`, `02294504`, `02294612`-`618`, `02498601/602`. Nothing in that list
is new -- earlier sessions caught it, because earlier sessions contained
unlocks. Do not merge the two: a component that answers one and not the other
is telling you something.

### Requests that never get an answer

The two sweeps send 41 zero-length request identifiers between them. **34 are
answered within 50 ms; 7 never are:**

| Request | `AA BB` | Sweep | Polls | |
|---|---|---|---|---|
| `02519500`, `02519501` | `95 00`, `95 01` | pack restore, IoT reboot | 6 per restore, 6 per reboot | Identity channel of a component never otherwise seen |
| `13B16001` | `60 01` | pack restore, IoT reboot | 6 per restore, 6 per reboot | Same `PP` = `13` as the HubLock |
| `02213600`, `02213601` | `36 00`, `36 01` | unlock wake | 1 each | Component 3's live channel |
| `02498601`, `02498602` | `86 01`, `86 02` | unlock wake | 1 each | `AA` = `86` broadcasts `02488600` but answers nothing |

**This is a technique, not just a list.** A request that gets no answer names a
node the master expects and cannot reach. The rate is part of the evidence:
`02417500/501` is answered and goes out 3 times per restore, while the two
unanswered pack-restore polls go out 6 times, at two separate moments. That is
what a retry looks like.

`13B16001` is the strongest lead. `PP` = `13` appears nowhere else in the whole
corpus except `13B76400`, the HubLock, and the HubLock has been physically off
this bike since the disconnection session. A node polled six times per boot
without a reply, at the same priority as the one component known to be absent,
is what an absent component looks like.

It is a lead and not a finding, because `AA` = `60` is not `AA` = `64`.
Refitting the HubLock and recording another cold boot would settle it in one
session.

**Settled against, by the 9 September pedalling session.** That bike has its
HubLock fitted (`13B76400` sends its 21 frames after every UNLOCK and nine
after every LOCK) and its three pack restores polled `13B16001` 18 times
without one reply. A node that is present and transmitting does not leave its
identity poll unanswered, so `13B16001` is not the HubLock. `PP` = `13` is
still shared with nothing else, and what sits at `AA` = `60` is open again.

### A confound in this session

`13B76400` is **absent** from the battery-removal recording. The HubLock was
still disconnected from the earlier experiment and was never refitted. So this
session's 121 identifiers are not a like-for-like inventory against sessions 1
and 2, and any claim about `AA` = `60` or `64` from it carries that caveat.

## Pedalling on the bench

The 9 September stationary-pedalling session
(`2026-09-09T16-10-23_unlock-pedal-stationary-lock`) is the first recording in
which anything on the bike moved: four UNLOCK / pedal / LOCK loops, three
brake presses, then three pack removals. GPS speed stayed 0.0 throughout.
**Assumption, not confirmed at the time: the rear wheel was off the ground
and spinning.** Every reading below depends on it.

What moved, all of it at component 1 (`AA` = `16`):

```
03FF1603  bytes 0-1   0 -> 218..256 -> 0     ramps while pedalling; 0.1 km/h fits the 25 km/h limit
03FF1602  bytes 4-5   0 -> 81 / 102          per-unlock counter, advances only while 1603 moves; metres fit
03FF1602  bytes 0-2   0x075A85 -> 0x075AA2   cumulative, +1 per 10 of the above; odometer in 10 m, CONFIRMED below
03FF1604  byte 1      0 -> ~190, then holds   tracks the speed but keeps its last value until LOCK
03FF1604  byte 3      0 -> 16 / 19            counts while pedalling, then holds
05FF4610  current     -0.45 to -0.86 A        loop 2 only; the motor assisted in one loop of four
```

**The odometer is confirmed, and it is three bytes wide.** The first reading
took bytes 1-2 as the field, with byte 1 (`0x5A`) a width guess, and got
231.7 km. Byte 0 belongs to it: bytes 0-2 big-endian in 10 m units give
`0x075A85` = 4819.25 km at the start of the session and `0x075AA2` = 4819.54 km
at the end, while `GTFRI`'s total mileage on the same clock read 4819.3, then
4819.4, then 4819.5. On the 7 September bike the same bytes read `0x000005` =
0.05 km against a reported 0.1 km, which is the same value at `GTFRI`'s
0.1 km resolution. Two bikes, two odometers, both matched to a measured value.
`signals.toml` carries it as `Distance_1602.Odometer`.

What did not move: `04FF3604`, the 30 ms message. Both channels held their
idle 2-9 through every loop, which removes speed, cadence and torque as
readings of it. Its one reaction was a sentinel: `0x0555` in one channel for
about a second when the pedals stopped, twice, and once in the other channel
mid-loop. Its `signals.toml` entry carries the detail.

Nothing responded to the brakes. A search for bits set only within 6 s of the
three brake marks, over all unlocked time, found none. The marks were typed
after pressing, so this is a weak negative; a brake test wants a mark before
the press and a hold of several seconds.

The wider lesson is the one already on record for `02181606` byte 2, now
repeated at `03FF1603`: a field nobody has exercised is indistinguishable from
padding. Four sessions had read bytes 0-3 as one 32-bit value because bytes
0-1 were always zero.

## Still unidentified

| `AA BB` | Identifiers | What is known |
|---|---|---|
| `36 04` | `04FF3604` | 30 ms, the fastest message on the bus. **Still the most valuable unknown**, but no longer a mystery in shape: two 16-bit analogue channels idling at 4.47 and 3.05 with no correlation between them, plus the constant 200 and a flag. Not a lamp message. It needs a ride -- see `signals.toml`. |
| `10 00`, `10 01` | `03FF1000`, `03FF1001` | The 105 ms heartbeat. Bytes 2-7 have never varied, across 101,080 frames and thirteen sessions. Bytes 0-1 are per bike: `08 40` on the 7 September bike (`00 40` for the first two seconds after a cold boot), `00 00` on the 9 September bike in all 7,241 frames, unlocked and pedalling included. So bit `0x08` of byte 0 is not a ready flag in the sense recorded; `Heartbeat_1000.SystemReady` is downgraded to `hypothesis` in `signals.toml` and the name is kept only as a label. `AA` = `10` fits no channel pattern, so it looks like a broadcast. |
| `26 05` | `02FF2605` | 1 Hz. Bytes 0-4 are `00 00 06 09 07` on the 7 September bike and `00 00 06 09 09` on the 9 September one, so byte 4 is a per-bike value. Byte 7 is a rolling 0-9 sequence counter, one step per second. Bytes 5-6 change slowly and inconsistently -- byte 6 held for 47 s in one session and 20 s in another -- so they are not a tens digit and are not decoded. |
| `16 03` | `03FF1603` | **A correction: two fields, and bytes 0-1 are probably speed.** The "voltage-like" value described here before is bytes 2-3 only; bytes 0-1 had simply never been non-zero until the bike was pedalled. See "Pedalling on the bench" and `Speed_1603` in `signals.toml`. The bytes 2-3 reading is weaker than it was: 2.4-2.8 V above the pack in the pedalling session. |
| `46 16`, `46 18` | `05124616`, `05124618` | BMS parameters. `4616` is a constant per pack: 12486 on the 7 September bike, 7591 on the 9 September one. `4618` falls across sessions on bike 1 (3861, 3603-3719, 3540) but not monotonically within one; on bike 2 it is small and negative (−243 to 30 as a signed 32-bit value), so it is a signed quantity that can cross zero. Undecoded. |
| `86 xx`, `B6 06`, `27 04`, `40 xx`, `A6 04` | `02488600`, `0260B606`, `02FF2704`, `05FF4000`, `0258A604` | Constant payloads within a bike, mostly zero. `0260B606` byte 0 is 1 on the 7 September bike and 0 on the 9 September one, so it is a per-bike setting or state, not padding. The rest carry no information, so there is nothing to fit until something moves them. `0258A604` appeared only in the reboot session, 15 all-zero frames inside the reboot wakes. |
| `B0 28` | `1FF8B028` | **A correction: not a constant.** It was listed with the constants because its payload is always zero. It is an event: one frame per lock-state transition in every session, 2.3-3.4 s after each UNLOCK and 0.4-0.9 s after each LOCK, within 0.1 s of the first `13B76400` frame either way. It also fired in the three sessions with the HubLock removed, so the HubLock does not send it. It fires **twice** around each `MEULK` (3.4 to 4.7 s apart, seven of seven) and twice after each IoT `REBOOT` (0.8 s apart, three of three). Session 3 has a fourth frame at rel 201 s with no command in the serial log; `04FF3604` stops there and the bus sleeps, so that is a lock the console did not record, not an exception. The information is in its timing, not its payload. See `LockTransition_B028` in `signals.toml`. |

## What the identifier fields look like

Across all four corpora, unchanged from the previous measurement:

- `PP` takes `02 03 04 05 08 13 1F` -- seven values in the range 0x02-0x1F,
  exactly the five bits available above the low 24. Consistent with a priority
  field. **This corrects an earlier note that it only reached 0x08.** The
  battery-removal session added 24 identifiers and no new `PP`, which is what
  a five-bit field that is already full looks like.
- `TT` takes `12 18 19 20 21 29 40 41 47 48 49 51 58 60 B1 B7 F8 F9 FF` -- 19
  values, four of them new at cold boot (`41`, `49`, `51`, `B1`) and one
  (`58`) seen only in the reboot session. `FF` appears only on periodic status
  messages, never on a command, so `FF` looks like "unsolicited report".
- `AA` takes 23 values, `BB` takes 20. The new `AA` values are `60`, `75`,
  `86` and `95` from the cold-boot sweep, and `A6` from the reboot session.

These counts are over the whole corpus: 123 identifiers across thirteen session
directories (nine of 7 September, three of 9 September from the second bike,
and the 4 September legacy set). The 9 September sessions add no identifier
and no field value. Sessions 6
and 7 add nothing: 6 contains no valid command (a malformed `GTRTO` that was
never acknowledged) and is a pure locked baseline of five identifiers; 7 sent
three `GTECC`s to a locked bike and shows three identifiers.

The nibble hypothesis, in its original form, is dead: it looked promising while
the low nibble of `AA` was `6` for the three then-confirmed components (`36`,
`16`, `46`), and the HubLock at `AA` = `64` broke it. What replaced it is the
channel scheme above, which is a different claim -- the low digit is a channel
within a component, and `6` recurring was the live-data channel recurring, not
a marker.

## The method that identifies a component

Five techniques now, in order of strength.

**1. Match a measured value.** The strongest and the cheapest, and it was
under-used until now. `+RESP:GTFRI` carries pack voltage, pack current, state
of charge, state of health, temperatures, mileage and light states on the CAN
log's own clock. A candidate field is confirmed by reproducing one of those,
not by looking plausible. This is what settled `05FF4610`, `05FF4609` and
`05124611`, and it needs no experiment at all -- only a recording with the bus
awake while reports arrive.

Better still, prefer a value that **moves**. The state of charge was confirmed
by a single step from 100 % to 99 % landing inside the gap between the two
serial reports that bracket it. A constant matching a constant proves almost
nothing, which is why the BMS temperatures are still unresolved: the whole
corpus spans 27 to 29 degC.

**2. Check one decoded signal against another.** `05124614` was confirmed
without any new recording, by showing its maximum, minimum and spread agree
with the 13 cell voltages -- decoded independently, by a different endianness
-- in all 8 frames, indices included. Two readings of the same physical
quantity that were never fitted to each other are strong evidence.

**3. Compare states, not events.** Build a state timeline from the serial
reports, then find bytes whose value sets are *disjoint* between states. This
found the lock. It beats diffing event windows because a repeated command on an
already-satisfied state produces no frame at all: one session issued six
`UNLOCK` commands and produced two unlock transitions, so scoring against
commands ranked the correct answer 3rd of 8, behind high-churn bytes matching
by chance.

Watch for one false positive: a monotonic counter is disjoint between any two
time intervals. There are **four** such counters (see above), not one, and all
four surface every time.

And a second caution, learned from the temperatures: **the console's
`<ECU Info>` is a cached copy, refreshed by the tracker's own polls at unlock.**
Its cell temperatures changed only in the first report after a `05124615`
reply, and to that reply's values. Comparing a cached field in time against a
live frame produced a wrong "no byte is the cell maximum" and was withdrawn.
Fit against `GTFRI`'s directly measured fields (pack voltage, current, SOC) or
against the bus itself; treat `<ECU Info>` as evidence of what was last polled.

**4. Cut power and watch the boot.** New, and it pays better than expected.
A cold boot makes the master interrogate every node it knows about, so one
power cycle reveals identifiers that months of idling never would -- 23 of
them here. It also separates volatile state from non-volatile: whatever resets
was in RAM. Nothing else distinguishes those two.

**5. Disconnect the component.** The most decisive: record a session with the
component disabled and diff the identifier sets. Exactly one identifier
disappeared and nothing replaced it, which no amount of correlation can match
for certainty. It also shows the bus is otherwise healthy, because everything
else keeps working. Bench only, never on a bike anyone is about to ride.

A caution from that session: the serial console's own state fields are not a
substitute for the CAN signal. With the HubLock physically absent, `GTFRI`
still reported `ECU Lock State` flipping between 0 and 1, because it reflects
what the IoT module believes rather than what the hardware did.

## What would settle the rest

1. **Ride the bike.** Every session is still stationary in the sense that
   matters: the bench pedalling session drove the ECU speed to 24.3 km/h and
   the GPS speed to 3.7 km/h, but the wheel carried no load, the motor
   assisted in one loop of four, and `04FF3604` did not move. Torque and
   motor current cannot be found where they are zero, and they are the
   signals most worth having. `04FF3604` will move first, because it is the
   fastest message on the bus.
2. **Move a temperature.** Six temperature bytes are recorded and not one can
   be assigned, because the whole corpus is 27-29 degC. A charge cycle heats
   the MOS, a discharge heats the cells; either separates them.
3. **Charge the pack.** `PackCurrent` is confirmed as signed, but every frame
   ever recorded is a discharge of -32 to -104 mA. The positive direction is
   untested, and so is the whole top of the state-of-charge range.
4. **More remote commands.** The method is proven and costs nothing: every
   `+ACK:GTRTO` is a free, precisely-timestamped stimulus. `MEULK` alone
   revealed a four-identifier authentication exchange. The protocol document
   in `reference/` lists the full command set.
5. **Extract `<ECU Info>`'s version group order** from the spec PDF. It is a
   reading task, not a recording task, and it would settle which of components
   1 and 3 is the ECU and which is the speed panel.
6. **Find something that still measures the deep-sleep threshold.** The
   `05FF4400` value is a frame count and is withdrawn as a clock; but its
   reset brackets a BMS sleep timer between 495 and 640 s (see above). Lock,
   wait a chosen interval, unlock, and read whether the count restarts. Two or
   three intervals would pin it. `03FF1400`'s reset is now known to be pack
   power, so it cannot serve.
10. **Unlock, `REBOOT`, unlock, in one recording.** That catches `02F82400`
   resetting on tape instead of inferring it, and settles whether component 2
   is the IoT module.
7. **Disconnection.** With one component unplugged, the traffic that stops
   names its address. Bench only.
8. **Reconnect the HubLock.** Done, in effect: the 9 September bike has its
   HubLock fitted and `13B76400` is in all three of its sessions. That is what
   showed `13B16001` is not the HubLock (see "Requests that never get an
   answer"). A cold boot of bike 1 with its HubLock refitted would still be
   worth having, to see whether the same poll goes unanswered there too.
9. **Release the pack without removing it.** That separates the two readings
   of the `MEULK` result below: contactor first, or contacts broken by the
   pack moving.

## A trap: a zeroed field is not a measured zero

When the ECU is asleep it does not publish, and everything downstream reports
zero rather than reporting nothing. A zero that means "no data" is
indistinguishable, field by field, from a zero that means "I measured zero".
This has now caused two wrong readings, both recorded above and both costly:

- The ECU error code reading `0000000000000000` was taken as "no faults". It
  means the ECU is not in the report at all. The faults never went away.
- `02181606` byte 2 reading 0 while asleep was taken as evidence it was an
  awake-flag scaled to 25, and the 25 as a brightness percentage. It is the
  speed limit, and the 0 is absence.

**Before comparing any ECU-derived field across sessions, check `ecu_lock_state`
and check whether the other live measurements are zero too.** Cell temperatures,
pack current and battery health all zero at once is not a bike in a strange
state; it is a report with no ECU data in it. Sessions where the bike stayed
locked cannot be compared with sessions where it was unlocked, on any
ECU-derived field.

The same caution applies to the CAN side: an identifier absent from a locked
session has not disappeared, it is asleep. The sleeping bus is five
identifiers and unlocking brings up about ninety more.

## An open puzzle

The ECU error code was `0210000000000000` in every session up to and including
the battery-removal session -- bits 52 and 57, both outside the range this
document version defines (0-35). It is **unchanged** by disabling the HubLock,
so it is not a Hub-Lock fault, and bit 11 ("abnormal communication with
Hub-Lock") never sets even with the HubLock absent. Either the ECU does not
monitor for it, or the way it was disabled leaves the ECU believing it was
never fitted.

**CORRECTION, WITHDRAWN.** This section previously claimed the faults were not
active throughout and that an IoT reboot cleared them, on the evidence that
they appeared in the first four sessions and not in the reboot session or the
two after it. That claim is wrong and is withdrawn. It was produced by
aggregating faults per session and comparing sessions, which hid the fact that
the error code varies *within* a session.

**What is actually true: on the 7 September bike the faults are present
whenever the ECU is reporting at all.** Across its 75 GTFRI reports:

| ECU lock state | error code `0210…` | error code `0000…` |
|---|---|---|
| 0 (unlocked) | 25 | 0 |
| 1 (locked) | 8 | 42 |

Unlocked is 25 out of 25. The 8 locked-with-fault reports are the ones arriving
a second or two after a `LOCK`, before the ECU goes quiet.

**And on the 9 September bike there are no faults at all**: 45 unlocked
reports with the ECU publishing (health 95 %, cell temperature 21 degC, pack
current non-zero) and every one of them reads `0000000000000000`. Bits 52 and
57 are a property of bike 1, not of the model or the firmware line. Whole-corpus
totals are 144 reports, 32 of 77 unlocked with the fault; that number mixes
two bikes and should not be read as a rate.

And the "clean" reports are not clean. Decoding `<ECU Info>` shows every live
measurement zeroed while the static fields survive:

| field | locked, "clean" | unlocked, "fault" |
|---|---|---|
| `battery_health_pct` | 0 | 97 |
| `cell_temp_max_c` | 0 | 29 |
| `pack_current_ma` | 0 | -49 |
| `ecu_remaining_mileage` | 0.0 | 56.0 |
| `ecu_firmware_version` | `0000004A…` | `0000004A…` (same) |
| `ecu_total_mileage` | 0.1 | 0.1 (same) |

Only the version strings and the persisted total mileage come through. So
`0000000000000000` means **no ECU data in this report**, not "no faults". The
reboot session and the two after it were locked throughout, which is the whole
of why they looked clean.

The original sentence in this section -- two undocumented faults active on this
bike throughout -- was right for bike 1. Both faults stand there, and nothing
so far has cleared either of them. Bike 2 never had them.

**What would still be worth knowing:** whether bits 52 and 57 appear anywhere in
the CAN traffic. If they exist only in `GTFRI`, they are the IoT module's
bookkeeping; if a frame the ECU emits carries them, they are the ECU's.
`+RESP:GTBMI` relays bus frames verbatim and is the place to look first.
