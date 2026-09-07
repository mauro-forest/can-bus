"""`bikelog` -- start a capture session, or annotate and inspect one.

Read-only with respect to the bus: this tool listens, and never transmits.

    bikelog start "headlight on and off"   # run this in tmux; Ctrl-C to stop
    bikelog mark "brake squeezed"          # from another pane
    bikelog list
    bikelog status
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from bikelog import can_iface, config, marks, meta
from bikelog.recorder import Recorder


def _load(args: argparse.Namespace) -> config.Config:
    return config.load(Path(args.config) if args.config else None)


def cmd_start(args: argparse.Namespace) -> int:
    cfg = _load(args)
    label = args.label or "session"

    iface, notes = can_iface.ensure_up(cfg.can.interface, cfg.can.bitrate)
    for note in notes:
        print(f"  {note}", file=sys.stderr)
    if not iface.exists and not args.force:
        print(
            f"refusing to start: {cfg.can.interface} is not present "
            "(pass --force to record serial only)",
            file=sys.stderr,
        )
        return 1

    cfg.session_dir.mkdir(parents=True, exist_ok=True)
    session = meta.create(cfg.session_dir, label)
    session.write_meta(
        {
            "config_source": str(cfg.source),
            "can_interface": cfg.can.interface,
            "can_bitrate_configured": cfg.can.bitrate,
            "can_bitrate_actual": iface.bitrate,
            "can_state_at_start": iface.can_state,
            "serial_port": cfg.serial.port,
            "serial_baudrate": cfg.serial.baudrate,
            "clean_shutdown": False,
        }
    )

    print(f"session {session.session_id}")
    print(f"  {iface.describe()}")
    print(f"  serial {cfg.serial.port} @ {cfg.serial.baudrate}")
    print(f"  writing to {session.path}")
    print("  type a label + Enter to mark; bare Enter marks unlabelled; Ctrl-C to stop")

    recorder = Recorder(cfg, session)
    counts = recorder.run()
    session.finalise(counts.as_meta())

    print(
        f"stopped: {counts.can_frames:,} frames, {len(counts.can_ids)} ids, "
        f"{counts.serial_lines:,} serial lines, {counts.marks} marks"
    )
    if counts.can_error_frames:
        print(
            f"  {counts.can_error_frames} error frames -- check the bitrate",
            file=sys.stderr,
        )
    print(f"  pull it with: tools/pull-session.sh {session.session_id}")
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    cfg = _load(args)
    session = meta.latest(cfg.session_dir)
    if session is None:
        print(f"no sessions under {cfg.session_dir}", file=sys.stderr)
        return 1
    try:
        marks.send(session.marks_fifo, " ".join(args.label))
    except (FileNotFoundError, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"marked {session.session_id}: {' '.join(args.label) or marks.UNLABELLED}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    cfg = _load(args)
    sessions = meta.find_all(cfg.session_dir)
    if not sessions:
        print(f"no sessions under {cfg.session_dir}")
        return 0
    for session in sessions:
        info = json.loads(session.meta_path.read_text())
        clean = info.get("clean_shutdown")
        state = "ok     " if clean else "UNCLEAN"
        duration = info.get("duration_s")
        length = f"{float(duration):6.0f}s" if duration else "   ?  s"
        frames = info.get("can_frames")
        frames_text = f"{int(frames):>8,}" if frames is not None else "       ?"
        print(
            f"{state}  {session.session_id:<44} {length} "
            f"{frames_text} frames  {session.label}"
        )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """What deploy.sh runs to prove a deploy actually landed."""
    cfg = _load(args)
    iface = can_iface.state(cfg.can.interface)
    clock = meta.clock_state()
    synced = clock.get("ntp_synchronized")
    port = Path(cfg.serial.port)
    sessions = meta.find_all(cfg.session_dir)

    print(f"git rev      {meta.git_rev()}")
    print(f"config       {cfg.source}")
    print(f"can          {iface.describe()}")
    print(
        f"serial       {cfg.serial.port} @ {cfg.serial.baudrate} "
        f"({'present' if port.exists() else 'NOT PRESENT'})"
    )
    print(
        f"clock        {clock['utc']} "
        f"(ntp {'synced' if synced else 'NOT SYNCED' if synced is False else 'unknown'})"
    )
    print(f"candump      {shutil.which('candump') or 'NOT FOUND (install can-utils)'}")
    print(f"sessions     {len(sessions)} under {cfg.session_dir}")
    if sessions:
        print(f"latest       {sessions[-1].session_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bikelog", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="path to capture.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="record a session until Ctrl-C")
    start.add_argument("label", nargs="?", help="what this experiment is")
    start.add_argument(
        "--force", action="store_true", help="start even if the CAN interface is missing"
    )
    start.set_defaults(func=cmd_start)

    mark = sub.add_parser("mark", help="annotate the running session")
    mark.add_argument("label", nargs="*", help="what just happened")
    mark.set_defaults(func=cmd_mark)

    listing = sub.add_parser("list", help="list recorded sessions")
    listing.set_defaults(func=cmd_list)

    status = sub.add_parser("status", help="report the rig's state")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
