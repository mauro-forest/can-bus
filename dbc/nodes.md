# Node addresses

The bus uses 29-bit identifiers that read as four bytes, `PP TT AA BB`
(see `bikecan/ids.py`). This file maps those onto the physical components and
records what each claim rests on.

Four components are now **confirmed** -- by tying a frame to a commanded
action, to a measured value, or by disconnecting the component and watching its
traffic disappear. The method that worked is not staring at payloads:
it is the IoT serial console. The tracker speaks the Queclink @Track protocol,
and it logs every remote command by name with a timestamp on the same clock as
the CAN log (`+ACK:GTRTO,...,HLON,...`), and relays BMS frames into its reports
verbatim (`+RESP:GTBMI`). See `bikecan/queclink.py`.

## Confirmed components

| Address | Component | Identifiers | How it was established |
|---|---|---|---|
| `AA` = `36` | **Headlight** | `02203606` | `HLON` at 1788785214.368 and `HLOFF` at 1788785234.208 were followed by byte 1 of `02203606` going to 1 after 0.77 s and to 0 after 0.33 s. Nothing else on the bus changed in either window. Cross-checks against `<ECU Info>` field 5. |
| `AA` = `16` | **Rear light** | `02181606` | `RLONEN` at 1788785392.364 and `RLOFF` at 1788785412.125 were followed by byte 4 going to 1 after 0.76 s and to 0 after 0.40 s. Nothing else changed. Cross-checks against `<ECU Info>` field 6. |
| `AA` = `64` | **HubLock** | `13B76400` | **Confirmed by disconnection.** With the HubLock disabled this was the only identifier to vanish: 95 identifiers became 94, none appeared in its place, and both lamps kept toggling. Byte 0 is 1 unlocked, 0 locked, disjoint across every interval of two earlier sessions, with unlock taking 2.1–2.6 s and lock 0.3–0.6 s. |
| `AA` = `46` | **BMS** | `05FF4600`–`05FF4610` | `+RESP:GTBMI` relays these payloads **verbatim**: the identical string `1410191019101910` appears in both the CAN frame and the report. GTBMI states 13 cells; `05FF4602`–`05FF4605` supply exactly 13 little-endian uint16 values, summing to 53.622 V against the 53.696 V that `+RESP:GTFRI` reported for the pack at that moment. |

Both light commands also fire on `UNLOCK` and `LOCK`, so unlocking the bike
switches both lamps on. That is why a periodic `GTFRI` report still showed
`head_light_status=1` after `HLOFF`: the unlock that followed turned it back on.

## Still unidentified

| `AA BB` | Identifiers | What is known |
|---|---|---|
| `10 00` | `03FF1000` | The 105 ms heartbeat with a payload that never changes. `BB`=00, and `AA`=10 appears with no other `BB`, so it looks like a broadcast rather than an addressed message. |
| `34 00` | `04FF3400` | Carries the confirmed 10 s uptime counter. A component that reports its own uptime is more likely a controller than a lamp, but nothing confirms which. |
| `36 04` | `04FF3604` | 30 ms period, the fastest message on the bus, 25 distinct payloads with bytes 1, 3, 5 and 6 varying. `AA`=36 is the headlight address, which makes a 30 ms lamp message surprising -- so either `AA` is not solely a component address, or this is something else at the same address. **This is the most interesting open question on the bus.** |
| `46 10` | `05FF4610` | 273 ms, bytes 3 and 7 varying, at the BMS address. Not part of the GTBMI relay. |
| `26 05` | `02FF2605` | 1011 ms, bytes 6-7 varying, entropy 3.3 bits. |
| `15 xx`, `35 xx`, `45 xx` | `03FF15xx`, `04F935xx`, `05124xxx` | Only ever seen during the unlock burst, in request/response pairs. Look like an enumeration or capability scan rather than steady-state traffic. |

The 4 September corpus saw 13 identifiers; the 7 September session saw 98,
because the bus sleeps. Only 5 identifiers are present on an idle locked bike;
unlocking wakes the rest. Any inventory taken from a locked bike is therefore
a small fraction of what exists.

## What the identifier fields look like now

Across all three sessions:

- `PP` takes `02 03 04 05 08 13 1F` — seven values in the range 0x02–0x1F,
  which is exactly the five bits available above the low 24. Consistent with a
  priority field. **This corrects an earlier note that it only reached 0x08.**
- `TT` takes `12 18 19 20 21 29 40 47 48 49 60 B7 F8 F9 FF`. `FF` appears only
  on periodic status messages, never on a command, so `FF` looks like
  "unsolicited report".
- `AA` takes 19 values, `BB` takes 20.

**`BB` is echoed from request to reply.** Four families, all with sub-millisecond
to 2 ms replies:

```
02 29 45 xx  ->  05 12 45 xx     xx = 00..04
02 29 46 xx  ->  05 12 46 xx     xx = 09, 11..17
02 21 35 xx  ->  04 F9 35 xx     xx = 00, 02, 03, 04
02 19 15 xx  ->  03 FF 15 xx     xx = 00, 04
```

`AA` and `BB` both survive the round trip; only `PP` and `TT` change, in fixed
pairs (`29`→`12`, `21`→`F9`, `19`→`FF`).

**But `BB` cannot simply be a peer node address.** For the two confirmed lamps
`BB` is `06` in both cases while `AA` differs, yet for the BMS `AA` is fixed at
`46` and `BB` runs `00`–`10` selecting *which block of cell data* is being
reported. So `BB` is an index whose meaning depends on `AA`: a peer in one
context, a data block in another. The simple two-node reading does not survive
the BMS evidence.

A reading that fits everything so far: **`AA` names a subsystem and `BB`
selects an item within it.**

The nibble hypothesis has lost ground. It looked promising while the low nibble
of `AA` was `6` for all three confirmed components (`36`, `16`, `46`), but the
HubLock is at `AA` = `64`, whose low nibble is `4`. So the low nibble is not a
fixed marker, and `AA` is more likely one flat address space after all.

## The method that identifies a component

Three techniques, in order of strength.

**1. Compare states, not events.** Build a state timeline from the serial
reports, then find bytes whose value sets are *disjoint* between states. This is
what found the lock. It beats diffing event windows because a repeated command
on an already-satisfied state produces no frame at all: session 2 issued six
`UNLOCK` commands and produced two unlock transitions, so scoring against
commands scored the correct answer at 3/8 and buried it under high-churn bytes
that matched by chance.

Watch for one false positive: a monotonic counter is disjoint between any two
time intervals. `04FF3400`'s uptime byte surfaces every time and is never the
answer.

**2. Diff a tight event window.** Good when a command has an immediate,
isolated effect, which is how both lamps were found — `HLON` produced exactly
one changed message inside a 20 s window.

**3. Disconnect the component.** The strongest of the three, and the one
that settled the HubLock: record a session with the component disabled and
diff the identifier sets. Exactly one identifier disappeared and nothing
replaced it, which no amount of correlation can match for certainty. It
also shows the bus is otherwise healthy, because everything else keeps
working. Bench only.

A caution from that session: the serial console's own state fields are not
a substitute for the CAN signal. With the HubLock physically absent, GTFRI
still reported `ECU Lock State` flipping between 0 and 1, because it
reflects what the IoT module believes rather than what the hardware did.

## What would settle the rest

1. **`04FF3604` at the headlight address, every 30 ms.** Resolving what this is
   probably resolves what `AA` and `BB` actually mean.
2. **Ride the bike.** Both sessions were stationary: speed 0.0 and one fixed GPS
   position throughout. Speed, torque and motor current cannot be found in data
   where they are all zero, and they are the signals most worth having.
3. **More remote commands.** The method is proven and costs nothing: every
   `+ACK:GTRTO` is a free, precisely-timestamped stimulus. The protocol document
   in `reference/` lists the full command set; each one exercised on a recorded
   session is another component identified.
4. **Disconnection.** With one component unplugged, the traffic that stops names
   its address. This is how the HubLock was confirmed, and it is the most
   decisive of the three methods. Bench only, never on a bike anyone is about
   to ride.

## An open puzzle

The ECU error code is `0210000000000000` in every session so far — bits 52 and
57, both outside the range this document version defines (0–35). It is
**unchanged** by disabling the HubLock, so it is not a Hub-Lock fault, and bit
11 ("abnormal communication with Hub-Lock") never sets even with the HubLock
absent. Either the ECU does not monitor for it, or the way it was disabled
leaves the ECU believing it was never fitted. Two undocumented faults have been
active on this bike throughout.
