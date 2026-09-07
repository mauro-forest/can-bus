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

From the 12 unique legacy recordings (7,588 frames, 13 identifiers, 5,733 s of
coverage on 4 September 2026 — recorded before this tooling existed, so no marks
and no metadata):

- **`04FF3400` carries a 10-second uptime counter.** Bytes 2–3, big endian.
  Across all 12 logs the field's change between consecutive frames equals the
  elapsed wall-clock time divided by 10 — exactly, at all 76 steps, including
  across a 2520 s gap that advanced it by 252. The bike therefore stayed powered
  across the whole session. Wall-clock time is the ground truth, so the scale
  needs no further test; the field *width* and the epoch are still open.
- **`03FF1000` is a heartbeat.** 7,243 of 7,588 frames, every 105 ms, payload
  `0840112200000001`, which never varies once. A fixed payload at a fixed rate
  carries no information.
- **There is a request/response protocol.** A zero-length `02294609` is answered
  ~1 ms later by `05124609#0100037003E800`: same node pair, different message
  type.
- **No component has been identified yet.** Every entry in `dbc/nodes.md` rests
  on traffic patterns alone. Nothing has been tied to a physical part by making
  that part do something, because no session has been recorded with marks.

The identifier structure hypothesis — `PP TT AA BB`, priority / message type /
two node fields — is set out in `bikecan/ids.py` with the evidence for and
against. Confirming the node fields is the highest-value next step: it would
attribute every frame to two physical components at once.

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
