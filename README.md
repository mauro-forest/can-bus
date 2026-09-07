# Forest ebike CAN bus

Capture the CAN bus and the IoT serial console of a Forest ebike on a Raspberry
Pi, pull the recordings to a laptop, and work out what the traffic means.

The bike's internal CAN network joins IoT (GPS, cellular, accelerometer, BLE,
sound), ECU, torque sensor, headlight, backlight, speed panel and BMS. The IoT
module also prints an untimestamped debug console over serial, showing what the
bike is being told over the cellular network. None of it is documented.

**The deliverable is `dbc/ebike.dbc` and `dbc/nodes.md`** — the accumulated
knowledge, with the evidence for each claim. The code exists to produce those.

Capture is read-only. Nothing in this repository transmits on the bus.

## Layout

| Path | Runs on | What it is |
|---|---|---|
| `bikelog/` | the Pi | the capture CLI: one foreground command per session |
| `bikecan/` | the laptop | the analysis library |
| `notebooks/` | the laptop | the decode workflow, in order |
| `dbc/signals.toml` | — | **the record**: what is known, and how confident |
| `dbc/ebike.dbc` | — | generated from `signals.toml`; never edit by hand |
| `dbc/nodes.md` | — | node address → component, with the evidence |
| `tools/` | the laptop | deploy to the Pi, pull recordings back |
| `data/sessions/` | the laptop | recordings (gitignored) |

`bikelog` and `bikecan` are deliberately separate. The Pi installs only
`bikelog` and pyserial; pandas, cantools and Jupyter stay on the laptop.

## Setup

```sh
cp tools/pi.env.example tools/pi.env   # then fill in PI_HOST and PI_DIR
$EDITOR config/capture.toml            # the real serial port, baud and bitrate
uv sync                                # laptop: everything
tools/deploy.sh                        # push bikelog to the Pi and verify
```

`deploy.sh` finishes by running `bikelog status` over SSH. If that does not
print a git rev, the CAN interface state and the serial port, the deploy did
not work.

While iterating on the capture code:

```sh
tools/deploy.sh --watch    # re-syncs on every change to bikelog/
```

It never restarts a running capture — that would cut a session in half. The
next `bikelog start` picks up the new code.

## Recording a session

On the Pi, **in tmux**, in the foreground:

```sh
bikelog start "headlight on and off"
```

It brings `can0` up if it is down, spawns `candump` and the serial reader, and
prints a live status line:

```
0:02:14 | can 412/s 55,208 frames 13 ids | serial 3/s 402 lines | marks 4
```

That line is the reason this runs in the foreground. A dead serial cable or a
wrong bitrate (which shows as `ERR` frames) is obvious within a second, not
after an hour of experiments.

**Annotate as you go.** Type a label and press Enter:

```
headlight on          ->  mark 1: headlight on @ 1788539377.601
                      ->  bare Enter marks unlabelled, for when both hands are busy
```

From another tmux pane, `bikelog mark "brake squeezed"` does the same through a
FIFO in the session directory.

Ctrl-C stops it, finalises the metadata and prints the pull command. `SIGTERM`
(`tmux kill-session`) stops it just as cleanly.

Marks are the whole basis of the analysis: two marks with the same label bracket
a window, and a lone mark opens a five-second one. Without them a recording is
just a wall of frames.

```sh
bikelog list      # every session, and whether it stopped cleanly
bikelog status    # the rig's state
```

## A session on disk

```
2026-09-07T14-03-11_headlight-on-and-off/
    can0.log     candump log format, absolute epoch timestamps
    serial.log   "<epoch.micros> <line>"
    marks.log    "<epoch.micros> <label>"
    meta.json    git rev, bitrate, counts, and the clock state
```

Both logs are stamped from the Pi's own clock, and that shared time base is the
only thing joining the two streams. `meta.json` records whether NTP had synced,
because a session recorded before the Pi reached a time server cannot be
compared with any other one — and the only way to know that later is to have
written it down at the time.

## Analysis

```sh
tools/pull-session.sh --latest
uv run jupyter lab
```

Work the notebooks in order:

1. **`01-bus-inventory`** — every identifier, its rate, and which bytes ever
   change. The first useful result is negative: most bytes on this bus are
   constants, and ruling them out is what makes the rest tractable.
2. **`02-id-structure`** — test whether the 29-bit identifiers encode node
   addresses (see below).
3. **`03-stimulus-response`** — diff each marked window against a quiet
   baseline. An identifier that appears only while a component is being operated
   belongs to that component. Also correlates cellular commands on the serial
   console against the CAN traffic they produce.
4. **`04-signal-hunt`** — fit a candidate field against a measured ground truth.

## What is known so far

Four components are **confirmed**, each tied to a commanded action, a measured
value, or the component's traffic vanishing when it is unplugged — never to a
plausible-looking payload:

| Component | Identifier | Signal | Evidence |
|---|---|---|---|
| **Headlight** | `02203606` | byte 1 = on/off | `HLON` then `HLOFF` were followed by byte 1 changing after 0.77 s and 0.33 s. Nothing else on the bus changed. |
| **Rear light** | `02181606` | byte 4 = on/off | `RLONEN` then `RLOFF`, followed by byte 4 changing after 0.76 s and 0.40 s. |
| **BMS** | `05FF4602`–`05FF4605` | 13 cell voltages, little-endian uint16 mV | The tracker relays these payloads **verbatim** in `+RESP:GTBMI`. 13 cells summing to 53.622 V against the 53.696 V that `+RESP:GTFRI` reported for the pack. |
| **HubLock** | `13B76400` | byte 0 = 1 unlocked, 0 locked | **Disconnection.** With the HubLock disabled this was the only identifier to vanish — 95 became 94, nothing replaced it, and both lamps kept toggling. Unlock takes 2.1–2.6 s, lock 0.3–0.6 s. |

Also confirmed: **`04FF3400` is a 10-second uptime counter** (bytes 2–3, big
endian). Its delta equalled elapsed wall-clock time over 10 at every step in
both sessions, and it *reset* between them — so it is uptime, not an odometer.
The heartbeat `03FF1000` carries no information: a fixed payload at a fixed rate.

`dbc/ebike.dbc` now decodes 94.7% of frames in the latest session.

### How this was found, and how to find the rest

Three techniques, strongest first.

**Disconnect the component** and diff the identifier sets. This settled the
HubLock: exactly one identifier disappeared and nothing replaced it. Bench only.

**Compare states, not events** — `experiment.compare_states()`. Build a state
timeline from the serial reports, then find bytes whose value sets are
*disjoint* between states. Scoring against commands instead does not work: a
repeated `UNLOCK` on an already-unlocked bike produces no frame, so one session's
six commands yielded two transitions and the correct answer ranked 3rd of 8.
Watch for the one guaranteed false positive — a monotonic counter is disjoint
across any two intervals, so `04FF3400`'s uptime byte surfaces every time.

**Diff a tight event window** when a command has an isolated effect, which is
how both lamps were found.

All three rely on the serial console. Not on staring at payloads. The IoT serial console speaks the **Queclink @Track
protocol** (`bikecan/queclink.py`, spec in `reference/`), and it gives two
things nothing else does:

- **`+ACK:GTRTO` logs every remote command by name** — `HLON`, `RLOFF`,
  `UNLOCK` — timestamped on the same clock as the CAN log. `queclink.as_marks`
  turns those into a marks table, so a session nobody annotated still yields
  stimulus windows. This is what identified both lamps.
- **`+RESP:GTFRI` carries measured ground truth**: pack voltage, speed,
  mileage, light states, pack current, cell temperatures. Signal fitting had
  none of this before. Note that `altitude` is metres above sea level — a
  reading of 12.4 looks convincingly like a battery voltage and is not one.

So the cheapest way to identify a component is to record a session and fire
remote commands at the bike. Every command is a free, precisely-timed stimulus.

### What is still open

- **`04FF3604`**: 30 ms period, the fastest message on the bus, sitting at the
  headlight's address. Far too fast for a lamp. Resolving it probably resolves
  what the identifier's node fields actually mean.
- **The bus sleeps.** An idle locked bike shows 5 identifiers; unlocking wakes
  it to 98. Any inventory taken from a locked bike is a small fraction of what
  exists.
- **Nothing has been ridden.** Every session so far has been stationary — speed
  0.0 and one fixed GPS position. Speed, torque and motor current cannot be
  found in data where they are all zero, and they are the signals most worth
  having.
- **Two undocumented ECU faults are active.** The error code is
  `0210000000000000` in every session — bits 52 and 57, outside the 0–35 range
  the protocol document defines. Unchanged by disabling the HubLock, so not a
  lock fault.
- **Don't trust the console's state fields as ground truth for the bus.** With
  the HubLock physically absent, `GTFRI` still reported `ECU Lock State`
  flipping 0/1. It reflects what the IoT module believes, not what the hardware
  did.

The identifier structure — `PP TT AA BB` — is set out in `bikecan/ids.py`, with
the confirmed component addresses and the evidence that `BB` is an index rather
than simply a peer address. `dbc/nodes.md` holds the full mapping.

## Recording what you find

Findings go in `dbc/signals.toml` (a field's meaning) or `dbc/nodes.md` (an
address's component), **always with the evidence and an honest confidence
level**:

- `hypothesis` — consistent with the recordings, no stimulus test
- `probable` — a stimulus experiment supports it, nothing measured
- `confirmed` — fitted against a measured value

Then regenerate the DBC and run the tests:

```sh
uv run python -m bikecan.dbc
uv run pytest -q
```

`dbc/ebike.dbc` is generated, so editing it by hand would lose the evidence —
which is the part that matters when someone asks in six months why a signal is
scaled the way it is. A test fails if the committed DBC is stale.

## Safety

`candump` only listens, and nothing in `bikelog/` transmits. Sending frames to a
live ebike can move the motor or upset the BMS. If transmit is ever needed it
belongs in a separate, clearly-named tool — never a flag on the capture path.

Unplug components on a bench, never on a bike anyone is about to ride.

## Notes on the legacy recordings

`data/sessions/legacy/` holds the 4 September candump logs. One recording is
present twice: the same bytes saved once as
`candump-2026-09-04_172937 (Headlight on and off).log` and once as
`candump-2026-09-04_172937.log`. `bikecan.session.load` detects byte-identical
logs and reads only one — reading both double-counted every frame, which made
the uptime counter appear to stand still. Both files stay on disk; they are
evidence.

That descriptive filename is also the reason `marks.log` exists. The instinct
was right; a filename just cannot say *when*.
