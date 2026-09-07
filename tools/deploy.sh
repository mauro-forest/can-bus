#!/usr/bin/env bash
# Push the capture code to the Raspberry Pi and prove it landed.
#
#   tools/deploy.sh            sync once, then verify
#   tools/deploy.sh --watch    re-sync whenever bikelog/ changes
#
# Only `bikelog` goes to the Pi. The analysis side (`bikecan`, notebooks) stays
# on the laptop, so the Pi never has to install pandas.

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
: "${PI_DIR:?set PI_DIR in tools/pi.env}"
PI_BIN="${PI_BIN:-}"
PI_UV="${PI_UV:-}"

LOCK_STAMP="$PI_DIR/.deployed-lock-hash"

log() { printf '  %s\n' "$*"; }

RESOLVED=""

resolve_remote_paths() {
    # Probing costs two SSH round trips, and both sync_once and verify need the
    # paths, so do it once per run.
    [[ -n "$RESOLVED" ]] && return 0

    # `ssh host command` runs a non-interactive shell, and Debian's default
    # .bashrc returns early for those, so ~/.local/bin is not on the PATH and a
    # bare `uv` fails with "command not found" even though it works in tmux.
    # Both of these are therefore resolved to absolute paths, once, up front.
    if [[ -z "$PI_BIN" ]]; then
        PI_BIN="$(ssh "$PI_HOST" 'printf "%s" "$HOME/.local/bin"')"
    fi

    if [[ -z "$PI_UV" ]]; then
        PI_UV="$(ssh "$PI_HOST" '
            for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" \
                             /usr/local/bin/uv /usr/bin/uv; do
                if [ -x "$candidate" ]; then printf "%s" "$candidate"; exit 0; fi
            done
            command -v uv 2>/dev/null || true
        ')"
    fi

    if [[ -z "$PI_UV" ]]; then
        echo "cannot find uv on $PI_HOST" >&2
        echo "  install it:  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
        echo "  or set PI_UV=/path/to/uv in tools/pi.env" >&2
        exit 1
    fi

    RESOLVED="yes"
    log "uv at $PI_UV, shim to $PI_BIN"
}

current_rev() {
    local sha dirty
    sha="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    dirty="$(git -C "$REPO" status --porcelain 2>/dev/null || true)"
    if [[ -n "$dirty" && "$sha" != "unknown" ]]; then
        # Deploying uncommitted work is fine; recording it as clean is not.
        echo "${sha}-dirty"
    else
        echo "$sha"
    fi
}

lock_hash() {
    if [[ -f "$REPO/uv.lock" ]]; then
        # shasum is on both macOS and Raspberry Pi OS; sha256sum is not on macOS.
        shasum -a 256 "$REPO/uv.lock" | cut -d' ' -f1
    else
        echo "no-lock"
    fi
}

sync_once() {
    local rev
    rev="$(current_rev)"

    resolve_remote_paths
    ssh "$PI_HOST" "mkdir -p '$PI_DIR/config' '$PI_BIN'"

    # --delete is scoped to bikelog/ on purpose. Pointed at $PI_DIR it would
    # take out the .venv and anything else the Pi keeps there; scoped here it
    # does the one job it is needed for -- removing a module you renamed
    # locally, so you are never debugging code you already deleted.
    rsync -az --delete \
        --exclude '__pycache__/' \
        "$REPO/bikelog/" "$PI_HOST:$PI_DIR/bikelog/"

    local extras=("$REPO/pyproject.toml")
    [[ -f "$REPO/uv.lock" ]] && extras+=("$REPO/uv.lock")
    rsync -az "${extras[@]}" "$PI_HOST:$PI_DIR/"
    # Straight into config/, which is where the CLI's default path looks.
    rsync -az "$REPO/config/capture.toml" "$PI_HOST:$PI_DIR/config/"

    # The Pi has no git checkout, so stamp the rev it is running. bikelog reads
    # this into every session's meta.json.
    printf '%s\n' "$rev" | ssh "$PI_HOST" "cat > '$PI_DIR/.deployed-rev'"
    log "synced $rev"

    local want have
    want="$(lock_hash)"
    have="$(ssh "$PI_HOST" "cat '$LOCK_STAMP' 2>/dev/null || true")"
    if [[ "$want" != "$have" ]]; then
        log "dependencies changed; running uv sync on the Pi"
        # --no-default-groups keeps the analysis stack off the Pi.
        ssh "$PI_HOST" "cd '$PI_DIR' && '$PI_UV' sync --no-default-groups --group capture"
        printf '%s\n' "$want" | ssh "$PI_HOST" "cat > '$LOCK_STAMP'"
    else
        # A dependency resolve over a cellular link is slow enough that you
        # would stop running deploy if it happened on every push.
        log "dependencies unchanged; skipping uv sync"
    fi

    install_shim
}

install_shim() {
    ssh "$PI_HOST" "cat > '$PI_BIN/bikelog' && chmod +x '$PI_BIN/bikelog'" <<SHIM
#!/bin/sh
# Installed by tools/deploy.sh. Do not edit on the Pi.
exec "$PI_UV" run --directory "$PI_DIR" --no-default-groups --group capture \\
    python -m bikelog "\$@"
SHIM
}

verify() {
    resolve_remote_paths
    echo
    echo "bikelog status on $PI_HOST:"
    # A deploy that cannot report the rig's state is not a deploy that worked.
    ssh "$PI_HOST" "'$PI_BIN/bikelog' status"
}

watch() {
    log "watching $REPO/bikelog for changes; Ctrl-C to stop"
    local previous=""
    while true; do
        local now
        now="$(find "$REPO/bikelog" "$REPO/config" "$REPO/pyproject.toml" \
            -type f -not -name '*.pyc' -exec shasum -a 256 {} + | shasum -a 256)"
        if [[ "$now" != "$previous" ]]; then
            [[ -n "$previous" ]] && log "change detected"
            sync_once
            # Deliberately does not restart a running capture: that would cut a
            # session in half. The next `bikelog start` picks this up.
            log "redeployed; the next 'bikelog start' will use it"
            previous="$now"
        fi
        sleep 2
    done
}

if [[ "${1:-}" == "--watch" ]]; then
    watch
else
    sync_once
    verify
fi
