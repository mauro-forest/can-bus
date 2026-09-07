# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Capture the CAN bus and IoT serial console of a Forest ebike on a Raspberry Pi,
pull the recordings to a laptop, and progressively work out what the traffic
means. Nothing about the bus is documented by the manufacturer.

**The deliverable is knowledge, not code**: `dbc/signals.toml` and
`dbc/nodes.md`. The code exists to produce and defend those. When you learn
something, the job is not done until it is written there with its evidence.

The bike's network joins IoT (GPS, cellular, accelerometer, BLE, sound), ECU,
torque sensor, headlight, backlight, speed panel and BMS.

## Commands

```sh
uv sync                                  # laptop: everything
uv run pytest -q                         # 108 tests, ~3 s
uv run pytest tests/test_dbc.py -q       # one file
uv run pytest -k compare_states -q       # one test by name
uv run python -m bikecan.dbc             # REQUIRED after editing dbc/signals.toml
uv run python -m bikecan.wireshark       # REQUIRED after editing dbc/signals.toml
uv run python -m bikecan.logreader data/sessions/legacy/*.log   # quick inventory
uv run jupyter lab                       # the decode workflow
```

Wireshark reads `candump` logs natively, so a session needs no conversion.
`dbc/ebike.lua` names the messages and signals, and marks every value that is
not `confirmed`:

```sh
tshark -X lua_script:dbc/ebike.lua -r data/sessions/<session>/can0.log \
       -Y ebike.bmspackvoltagecurrent.packcurrent -V
```

Filter by message name with `ebike.message`, which every message carries even
when it has no signals. Negate as `!(ebike.message == "Heartbeat_1000")`: the
`!=` form also drops every frame the dissector does not claim, because an
absent field never compares unequal.

For the GUI, symlink `dbc/ebike.lua` into the personal plugin folder shown by
Help > About Wireshark > Folders. On macOS the CLI is not on `PATH`; it is at
`/Applications/Wireshark.app/Contents/MacOS/tshark`.

Notebooks are version-controlled and must stay runnable. To check them
headlessly (note: `--output` is required, and `--inplace=false` is not valid):

```sh
cd notebooks && uv run jupyter execute --output=/tmp/out 01-bus-inventory.ipynb
```

Pi side, from the laptop:

```sh
tools/deploy.sh              # rsync bikelog + verify with `bikelog status` over SSH
tools/deploy.sh --watch      # re-sync on change; never restarts a running capture
tools/pull-session.sh --latest
```

On the Pi, in tmux: `bikelog start "label"`, then type labels + Enter to mark;
Ctrl-C stops and finalises. `bikelog mark "..."` works from another pane.

## Hard constraints

- **Never transmit on the bus.** `candump` only listens and nothing in
  `bikelog/` writes to CAN. Sending frames to a live ebike can move the motor
  or upset the BMS. If transmit is ever needed it goes in a separate,
  clearly-named tool — never a flag on the capture path.
- **Never hand-edit `dbc/ebike.dbc`.** It is generated from `signals.toml`;
  editing it loses the evidence, which is the part that matters.
  `tests/test_dbc.py::test_committed_dbc_is_current` fails if it is stale.
- **Never hand-edit `dbc/ebike.lua`** either. Same rule, same reason: it is
  generated from `signals.toml` by `bikecan/wireshark.py`, and
  `tests/test_wireshark.py::test_committed_lua_is_current` fails if it is
  stale. Unlike the DBC it carries the confidence levels, so a value shown
  without a marker is one tied to a measurement or a commanded action.
- **Recordings are irreplaceable evidence.** `data/` is gitignored, never
  rewritten in place, and `pull-session.sh` deletes nothing on the Pi.
- **macOS ships bash 3.2.** No `mapfile`/`readarray` in `tools/*.sh`. Check with
  `/usr/bin/env bash -n`.

## Architecture

Two packages that deliberately never import each other:

- **`bikelog/`** runs on the Pi. Only dependency is pyserial, so the Pi never
  installs pandas (`uv sync --no-default-groups --group capture`). A single
  foreground CLI supervises `candump -L` and a serial reader as child
  processes, piping both so one place counts frames, tracks IDs and prints a
  live status line. Running in the foreground is the point: a dead serial cable
  or wrong bitrate is visible in a second, not after an hour.
- **`bikecan/`** runs on the laptop: parsers, discovery, experiment methods,
  DBC generation.

`[tool.uv] package = false` — the project is never installed. Both sides run it
in place with `uv run python -m ...`, which is why pytest needs
`pythonpath = ["."]`.

### The shared time base is the load-bearing idea

A session is a **directory**, not a file: `can0.log`, `serial.log`,
`marks.log`, `meta.json`. Both logs are stamped from the Pi's own clock — the
serial reader stamps each line at the moment of the read, precisely so it can
be compared with `candump` timestamps. Everything downstream (window diffs,
state comparison, serial correlation) assumes it. `meta.json` records the NTP
sync state because a session recorded before the Pi reached a time server
cannot be compared with any other, and the only way to know later is to have
written it down then.

`session.load()` also accepts a directory of loose logs with no `meta.json` —
that is what `data/sessions/legacy/` is.

## How components actually get identified

Three techniques, strongest first. All three depend on the serial console.

**The serial console is not a debug log** — it speaks the Queclink @Track
protocol (`bikecan/queclink.py`, spec in `reference/`). It supplies what
payload-staring never can: `+ACK:GTRTO` logs each remote command by name
(`HLON`, `RLOFF`, `UNLOCK`) timestamped on the CAN log's clock, and
`+RESP:GTBMI` relays BMS frames **verbatim**.

1. **Disconnect the component** and diff the identifier sets. Settled the
   HubLock: exactly one ID vanished, nothing replaced it. Bench only.
2. **`experiment.compare_states()`** — build a state timeline from the serial
   reports, find bytes whose value sets are *disjoint* between states.
3. **`experiment.diff_window()`** on a tight event window, when a command has
   an isolated effect. Found both lamps.

`queclink.as_marks()` turns commands into a marks table that
`experiment.windows()`/`report()` accept unchanged, so an unannotated session
still yields stimulus windows. `queclink.ground_truth()` returns the measured
values (pack voltage, speed, mileage, light states, pack current, cell temps)
to fit candidate signals against.

## Traps that have already cost time

- **Scoring bytes against commands does not work.** A repeated `UNLOCK` on an
  already-unlocked bike produces no frame. One session's six commands yielded
  two transitions, so the correct answer ranked 3rd of 8 behind high-churn
  bytes matching by chance. Compare states instead.
- **A monotonic counter is disjoint across any two time intervals**, so
  `04FF3400`'s uptime byte is a guaranteed false positive in
  `compare_states()`. It is flagged `looks_like_counter` and ranked down.
- **`GTFRI` field 13 is altitude in metres, not a voltage.** 12.4 looks
  exactly like a lead-acid reading. Pack voltage is field 26, in millivolts.
- **Pack current is signed.** A real capture held `4294967209` = −87 mA.
- **A zeroed ECU field is not a measured zero.** With the bike locked the ECU
  does not publish and every derived field reads 0 -- error code, cell temps,
  pack current, battery health. `0000000000000000` in the error code means "no
  ECU data", not "no faults". Check `ecu_lock_state` before comparing any
  ECU-derived field across sessions. This has produced two wrong readings.
- **A configuration byte nobody has changed looks exactly like a constant.**
  `02181606` byte 2 held 25 in every session and was read as a brightness
  percentage; it is the max speed in km/h, and 25 is the EU pedelec default.
  The test that settles this kind of byte is to change the thing itself.
- **`GTFRI`'s `ECU Lock State` is not ground truth for the bus.** With the
  HubLock physically absent it still flipped 0/1 — it reflects what the IoT
  module believes, not what the hardware did.
- **The bus sleeps.** An idle locked bike shows 5 identifiers; unlocking wakes
  it to ~95. Any inventory from a locked bike is a small fraction of what
  exists.
- **`data/sessions/legacy/` contains one recording twice** under different
  names. `session.load()` de-duplicates by hash; reading both double-counted
  every frame and made the uptime counter appear to stand still.
- **Bit numbering differs by endianness** in `signals.toml`: `big_endian`
  `start_bit` counts MSB-first from byte 0 (matching
  `discover.bit_detail()`/`candidate_fields()`); `little_endian` `start_bit` is
  the DBC position of the LSB, i.e. `8 * byte_index`. Both are exercised by
  tests because getting it wrong produces plausible-looking nonsense.
- **`can0` needs root on the Pi.** `bikelog` will not `sudo`; it prints the
  command. `sudo ip link set can0 up type can bitrate 250000`.
- **`ssh host command` does not source `.bashrc`**, so `~/.local/bin` is not on
  the PATH and a bare `uv` fails. `deploy.sh` resolves `uv` and `$HOME` to
  absolute paths; `PI_UV` in `tools/pi.env` overrides.

## Recording findings

`dbc/signals.toml` is the record; every message and signal carries an explicit
confidence level, and the evidence, in prose:

- `hypothesis` — consistent with the recordings, no stimulus test
- `probable` — a stimulus experiment supports it, nothing measured
- `confirmed` — tied to a measured value or a commanded action

Be strict about this. A future reader must be able to tell a measurement from a
guess, and write down what would *disprove* a claim: the "power-cycle the bike"
note recorded against the uptime counter is what later confirmed it was uptime
and not an odometer. `dbc/nodes.md` holds the address→component mapping and the
identifier-structure hypothesis (`PP TT AA BB`), with what argues against it.

Corrections belong in the files too. Two claims have already been overturned by
later evidence (the nibble reading of `AA`, and the priority range), and both
are recorded as corrections rather than silently edited away.

## Current state

Four components confirmed: headlight (`02203606` byte 1), rear light
(`02181606` byte 4), BMS (`05FF4602`–`05FF4605`, 13 cells), HubLock
(`13B76400` byte 0). Plus a confirmed 10-second uptime counter (`04FF3400`).

Open, in rough priority order:

1. **`04FF3604`** — 30 ms period, the fastest message on the bus, at the
   headlight's address but far too fast for a lamp. The most valuable unknown.
2. **Nothing has been ridden.** Every session so far is stationary, speed 0.0.
   Speed, torque and motor current cannot be found where they are all zero.
3. **Two undocumented ECU faults** are active in every session: error code
   `0210000000000000`, bits 52 and 57, outside the documented 0–35 range.

## Notes

- Session logs contain the IMEI, VIN and GPS position. `data/` is gitignored.
- Deployments at Forest go through the tech team's GitHub and Vercel accounts;
  the Pi here is a bench machine reached over SSH, separate from that.
