#!/usr/bin/env bash
# Pull recordings off the Pi into data/sessions/.
#
#   tools/pull-session.sh                    every session not already local
#   tools/pull-session.sh <session-id>       just that one
#   tools/pull-session.sh --latest           the most recent one
#
# Never deletes anything on the Pi: recordings are irreplaceable evidence, and
# clearing space is a decision to make deliberately, not a side effect of a pull.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO/tools/pi.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "no $ENV_FILE -- copy tools/pi.env.example and fill it in" >&2
    exit 1
fi
# shellcheck source=/dev/null
source "$ENV_FILE"
: "${PI_HOST:?set PI_HOST in tools/pi.env}"

LOCAL_DIR="$REPO/data/sessions"
# Read the Pi's session directory out of the config we deployed, so the two
# cannot drift apart.
REMOTE_DIR="$(python3 - "$REPO/config/capture.toml" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as handle:
    print(tomllib.load(handle).get("session", {}).get("dir", "~/bikelog-sessions"))
PY
)"

mkdir -p "$LOCAL_DIR"

case "${1:-}" in
    --latest)
        target="$(ssh "$PI_HOST" "ls -1d $REMOTE_DIR/*/ 2>/dev/null | tail -1")"
        [[ -z "$target" ]] && { echo "no sessions on $PI_HOST" >&2; exit 1; }
        sessions=("$(basename "$target")")
        ;;
    "")
        # Not `mapfile`: macOS ships bash 3.2, which does not have it.
        sessions=()
        while IFS= read -r line; do
            [[ -n "$line" ]] && sessions+=("$line")
        done < <(ssh "$PI_HOST" "ls -1 $REMOTE_DIR 2>/dev/null")
        if [[ ${#sessions[@]} -eq 0 ]]; then
            echo "no sessions on $PI_HOST" >&2
            exit 1
        fi
        ;;
    *)
        sessions=("$1")
        ;;
esac

for session in "${sessions[@]}"; do
    [[ -z "$session" ]] && continue
    echo "$session"
    # --partial so a dropped link resumes rather than restarting a long log.
    # marks.fifo is a runtime artefact of a running capture, not data.
    # Copying a named pipe into the analysis tree is at best useless and at
    # worst a reader that blocks forever.
    rsync -av --partial --progress --exclude 'marks.fifo' \
        "$PI_HOST:$REMOTE_DIR/$session/" "$LOCAL_DIR/$session/"

    # A session with no end time is either still recording or was cut off.
    # The mark FIFO exists only while `bikelog start` is running, so it tells
    # the two apart -- reporting a live capture as a power loss is both
    # alarming and wrong.
    if ssh "$PI_HOST" "test -p '$REMOTE_DIR/$session/marks.fifo'"; then
        export STILL_RECORDING=yes
    else
        export STILL_RECORDING=""
    fi

    meta="$LOCAL_DIR/$session/meta.json"
    if [[ -f "$meta" ]]; then
        python3 - "$meta" <<'PY'
import json, os, sys
meta = json.load(open(sys.argv[1]))
clean = meta.get("clean_shutdown")
live = bool(os.environ.get("STILL_RECORDING"))
if clean:
    note = ""
elif live:
    # The counts are written at shutdown, so a live session reports none.
    note = "   [STILL RECORDING -- partial snapshot; pull again after Ctrl-C]"
else:
    note = "   [UNCLEAN -- ended without finalising; the data is still valid]"
print(
    f"  {meta.get('can_frames', '?')} frames, "
    f"{meta.get('can_id_count', '?')} ids, "
    f"{meta.get('serial_lines', '?')} serial lines, "
    f"{meta.get('marks', '?')} marks" + note
)
if meta.get("clock", {}).get("ntp_synchronized") is False:
    print("  WARNING: the Pi's clock was not NTP-synced; timestamps are local only")
PY
    else
        echo "  no meta.json -- session may still be recording"
    fi
done
