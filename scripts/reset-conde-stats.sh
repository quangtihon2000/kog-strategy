#!/usr/bin/env bash
# reset-conde-stats.sh — wipe conde_auto_entry signal/outcome history in Redis
# so /stats restarts from zero. Optional --telegram also resets the
# telegram_monitor "seen signals" baseline.
#
# Reads REDIS_URL from one of (in priority order):
#   1) $REDIS_URL env var
#   2) strategies/conde_auto_entry/agent/.env
#
# Usage:
#   scripts/reset-conde-stats.sh                  # streams only, dry-run preview
#   scripts/reset-conde-stats.sh --yes            # streams only, execute
#   scripts/reset-conde-stats.sh --yes --telegram # streams + telegram baseline
#
# Does NOT touch:
#   - heartbeat keys (TTL 180s, auto-recover)
#   - other strategies' streams (gvfx_signals, zone_signals, ...)
#   - FLUSHALL/FLUSHDB (never)

set -euo pipefail

YES=0
RESET_TELEGRAM=0
for arg in "$@"; do
    case "$arg" in
        --yes|-y)        YES=1 ;;
        --telegram|-t)   RESET_TELEGRAM=1 ;;
        --help|-h)
            sed -n '2,18p' "$0" | sed 's/^# //; s/^#//'
            exit 0
            ;;
        *)
            echo "Unknown arg: $arg" >&2
            exit 2
            ;;
    esac
done

if [[ -z "${REDIS_URL:-}" ]]; then
    ENV_FILE="$(dirname "$0")/../strategies/conde_auto_entry/agent/.env"
    if [[ -f "$ENV_FILE" ]]; then
        REDIS_URL="$(grep -E '^REDIS_URL=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
    fi
fi
if [[ -z "${REDIS_URL:-}" ]]; then
    echo "REDIS_URL not set and no .env file found" >&2
    exit 1
fi

if ! command -v redis-cli >/dev/null 2>&1; then
    echo "redis-cli not found in PATH" >&2
    exit 1
fi

# Parse REDIS_URL → host/port/password. We do this by hand because redis-cli's
# native -u handler doesn't URL-decode percent-escapes (e.g. %21 → !), and the
# password in .env is percent-encoded.
url_decode() {
    # printf %b interprets \xNN escapes; convert %NN → \xNN first.
    local s="${1//+/ }"
    printf '%b' "${s//%/\\x}"
}

# Format: redis://[:password@]host[:port][/db]
_url="${REDIS_URL#redis://}"
_authpart=""
if [[ "$_url" == *@* ]]; then
    _authpart="${_url%%@*}"
    _url="${_url#*@}"
fi
_hostport="${_url%%/*}"
REDIS_HOST="${_hostport%%:*}"
REDIS_PORT="${_hostport##*:}"
[[ "$REDIS_PORT" == "$REDIS_HOST" ]] && REDIS_PORT=6379
REDIS_PASS=""
if [[ -n "$_authpart" ]]; then
    REDIS_PASS="$(url_decode "${_authpart#:}")"
fi

run() {
    if [[ -n "$REDIS_PASS" ]]; then
        redis-cli --no-auth-warning -h "$REDIS_HOST" -p "$REDIS_PORT" -a "$REDIS_PASS" "$@"
    else
        redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" "$@"
    fi
}

# Sanity probe — fail fast if URL is wrong
if ! run PING >/dev/null; then
    echo "PING failed; check REDIS_URL" >&2
    exit 1
fi

echo "Target: ${REDIS_HOST}:${REDIS_PORT}"
echo

# ---------------------------------------------------------------------------
# Show what will be wiped
# ---------------------------------------------------------------------------
sig_len=$(run XLEN conde_signals 2>/dev/null || echo 0)
out_len=$(run XLEN conde_outcomes 2>/dev/null || echo 0)
ing_card=$(run SCARD conde_outcomes:ingested 2>/dev/null || echo 0)
dedup_n=$(run EVAL "return #redis.call('KEYS','dedup:conde_signals:*')" 0 2>/dev/null || echo 0)

echo "Conde streams to clear:"
printf "  conde_signals               : %s entries\n" "$sig_len"
printf "  conde_outcomes              : %s entries\n" "$out_len"
printf "  conde_outcomes:ingested     : %s members\n" "$ing_card"
printf "  dedup:conde_signals:*       : %s keys\n" "$dedup_n"

if [[ $RESET_TELEGRAM -eq 1 ]]; then
    sig_seen=$(run HLEN telegram_monitor:signals_new:seen 2>/dev/null || echo 0)
    svc_prev=$(run HLEN telegram_monitor:service_edges:prev 2>/dev/null || echo 0)
    echo
    echo "Telegram monitor state to clear:"
    printf "  telegram_monitor:signals_new:seen  : %s fields\n" "$sig_seen"
    printf "  telegram_monitor:service_edges:prev: %s fields\n" "$svc_prev"
    echo
    echo "  ⚠️  After reset, the FIRST signal observed becomes baseline (silent)."
    echo "      Only the SECOND signal onwards will trigger Telegram alerts."
fi

if [[ $YES -ne 1 ]]; then
    echo
    echo "(dry-run — re-run with --yes to execute)"
    exit 0
fi

# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------
echo
echo "Executing..."
run DEL conde_signals conde_outcomes conde_outcomes:ingested >/dev/null
run EVAL "for _,k in ipairs(redis.call('KEYS','dedup:conde_signals:*')) do redis.call('DEL',k) end return #redis.call('KEYS','dedup:conde_signals:*')" 0 >/dev/null

if [[ $RESET_TELEGRAM -eq 1 ]]; then
    run DEL telegram_monitor:signals_new:seen telegram_monitor:service_edges:prev >/dev/null
fi

echo "Done. New state:"
printf "  conde_signals               : %s\n" "$(run XLEN conde_signals 2>/dev/null || echo 0)"
printf "  conde_outcomes              : %s\n" "$(run XLEN conde_outcomes 2>/dev/null || echo 0)"
printf "  conde_outcomes:ingested     : %s\n" "$(run SCARD conde_outcomes:ingested 2>/dev/null || echo 0)"
echo
echo "Next steps on the VPS:"
echo "  nssm restart conde_auto_entry_agent"
if [[ $RESET_TELEGRAM -eq 1 ]]; then
    echo "  nssm restart telegram_monitor_bot"
fi
