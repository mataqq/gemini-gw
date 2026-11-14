#!/usr/bin/env bash
# supervisor: keeps relay.py + a public tunnel alive for the life of the CI job
set -uo pipefail
PORT="${PORT:-8080}"
SUBDOMAIN="${SUBDOMAIN:-gemini-gw-mataqq}"
cd "$(dirname "$0")"

log() { echo "[sup $(date -u +%H:%M:%S)] $*"; }

relay_up() { curl -fsS --max-time 4 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; }

start_relay() {
  relay_up && return 0
  log "starting relay.py"
  nohup python3 -u relay.py >> /tmp/relay.log 2>&1 &
  for _ in $(seq 1 20); do
    sleep 1
    relay_up && { log "relay up"; return 0; }
  done
  log "relay FAILED to start"
  tail -30 /tmp/relay.log
  return 1
}

PUBURL_FILE=/tmp/public_url.txt
: > "$PUBURL_FILE"

start_serveo() {
  pkill -f "serveo\.net" >/dev/null 2>&1
  sleep 1
  log "serveo -> $SUBDOMAIN"
  rm -f /tmp/serveo.log
  nohup ssh -T -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ServerAliveInterval=20 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
      -o ConnectTimeout=20 \
      -R "${SUBDOMAIN}:80:127.0.0.1:${PORT}" serveo.net >> /tmp/serveo.log 2>&1 &
  sleep 8
  if grep -qi "forwarding" /tmp/serveo.log; then
    echo "https://${SUBDOMAIN}.serveo.net" > "$PUBURL_FILE"
    log "serveo url = https://${SUBDOMAIN}.serveo.net"
    return 0
  fi
  log "serveo not announced"; tail -12 /tmp/serveo.log
  return 1
}

start_cf() {
  pkill -f "cloudflared tunnel" >/dev/null 2>&1
  sleep 1
  log "cloudflared quick tunnel"
  rm -f /tmp/tunnel.log
  nohup /tmp/cloudflared tunnel --url "http://127.0.0.1:${PORT}" --no-autoupdate \
      > /tmp/tunnel.log 2>&1 &
  for _ in $(seq 1 30); do
    U=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/tunnel.log | head -1 || true)
    if [ -n "${U:-}" ]; then
      echo "$U" > "$PUBURL_FILE"
      log "cloudflare url = $U"
      return 0
    fi
    sleep 2
  done
  log "cloudflare not announced"; tail -20 /tmp/tunnel.log
  return 1
}

start_relay || exit 1

# one-shot diagnostic so the run log answers "is the RT alive?"
echo "--- /__diag ---"
curl -sS --max-time 120 "http://127.0.0.1:${PORT}/__diag" | tee /tmp/diag.json
echo
echo "--- end diag ---"

MODE=none
start_serveo && MODE=serveo || { start_cf && MODE=cloudflare; }

DEAD=0
while true; do
  sleep 60
  if ! relay_up; then
    log "relay died -> restart"
    start_relay || log "restart failed"
  fi
  case "$MODE" in
    serveo)
      if ! pgrep -f "serveo\.net" >/dev/null 2>&1; then
        log "serveo gone -> reconnect"; start_serveo || { start_cf && MODE=cloudflare; }
      fi ;;
    cloudflare)
      if ! pgrep -f "cloudflared tunnel" >/dev/null 2>&1; then
        log "cloudflared gone -> restart"; start_cf || true
      fi ;;
    *)
      DEAD=$((DEAD+1))
      [ "$DEAD" -gt 20 ] && { log "no tunnel for 20 cycles, exiting"; exit 1; }
      start_serveo && MODE=serveo || { start_cf && MODE=cloudflare; } ;;
  esac
done
