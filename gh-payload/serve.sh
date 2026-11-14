#!/usr/bin/env bash
# supervisor: relay.py + verified public tunnel; writes /tmp/public_url.txt only after an
# external round-trip proves the tunnel reaches OUR relay.
set -uo pipefail
PORT="${PORT:-8080}"
SUB="${SUBDOMAIN:-gw-mataqq}"
cd "$(dirname "$0")"

log() { echo "[sup $(date -u +%H:%M:%S)] $*"; }
relay_up() { curl -fsS --max-time 4 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; }
: > /tmp/public_url.txt

start_relay() {
  relay_up && return 0
  log "starting relay.py"
  nohup python3 -u relay.py >> /tmp/relay.log 2>&1 &
  for _ in $(seq 1 25); do sleep 1; relay_up && { log "relay up"; return 0; }; done
  log "relay FAILED"; tail -40 /tmp/relay.log; return 1
}

# does this public URL really reach our relay?
verify_url() {
  local u="$1" body
  body=$(curl -fsS --max-time 20 "${u}/healthz" 2>/dev/null || true)
  case "$body" in *gemini-relay*) return 0 ;; esac
  return 1
}

accept() {
  if verify_url "$1"; then
    printf '%s\n' "$1" > /tmp/public_url.txt
    log "ACCEPTED $1"
    return 0
  fi
  log "reject $1 (no relay behind it)"
  return 1
}

try_serveo() {
  pkill -f "serveo" >/dev/null 2>&1; sleep 1
  rm -f /tmp/serveo.log
  log "tunnel: serveo subdomain=$SUB"
  nohup ssh -T -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
      -o ConnectTimeout=25 \
      -R "${SUB}:80:127.0.0.1:${PORT}" serveo.net >> /tmp/serveo.log 2>&1 &
  for _ in $(seq 1 20); do
    sleep 2
    U=$(grep -oE 'https://[a-z0-9.-]+\.serveo\.net' /tmp/serveo.log | head -1 || true)
    if [ -n "${U:-}" ] && accept "$U"; then return 0; fi
  done
  head -40 /tmp/serveo.log 2>/dev/null > /tmp/serveo_tail.txt
  return 1
}

try_localhostrun() {
  pkill -f "localhost.run" >/dev/null 2>&1; sleep 1
  rm -f /tmp/hr.log
  log "tunnel: localhost.run"
  nohup ssh -T -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
      -R 80:127.0.0.1:${PORT} nokey@localhost.run >> /tmp/hr.log 2>&1 &
  for _ in $(seq 1 20); do
    sleep 2
    U=$(grep -oE 'https://[a-z0-9]+(\.\.)?[a-z0-9.-]*\.(lhr\.life|localhost\.run)' /tmp/hr.log | head -1 || true)
    if [ -n "${U:-}" ] && accept "$U"; then return 0; fi
  done
  return 1
}

try_cloudflare() {
  pkill -f "cloudflared tunnel" >/dev/null 2>&1; sleep 1
  rm -f /tmp/tunnel.log
  log "tunnel: cloudflared quick"
  nohup /tmp/cloudflared tunnel --url "http://127.0.0.1:${PORT}" --no-autoupdate \
      --protocol http2 > /tmp/tunnel.log 2>&1 &
  for _ in $(seq 1 25); do
    sleep 3
    U=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/tunnel.log | head -1 || true)
    if [ -n "${U:-}" ] && accept "$U"; then return 0; fi
  done
  tail -25 /tmp/tunnel.log 2>/dev/null
  return 1
}

start_relay || exit 1
: > /tmp/serveo_tail.txt

echo "===== /__diag (local) ====="
curl -sS --max-time 180 "http://127.0.0.1:${PORT}/__diag" | tee /tmp/diag.json
echo
echo "===== diag end ====="

MODE=none
for attempt in 1 2 3; do
  try_serveo      && { MODE=serveo; break; }
  try_localhostrun && { MODE=localhostrun; break; }
  try_cloudflare  && { MODE=cf; break; }
  log "all tunnels failed (attempt $attempt)"; sleep 10
done
[ "$MODE" = none ] && { log "FATAL: no tunnel"; exit 1; }

log "mode=$MODE url=$(cat /tmp/public_url.txt)"
while true; do
  sleep 45
  relay_up || { log "relay down -> restart"; start_relay || log "restart failed"; }
  [ -s /tmp/public_url.txt ] && verify_url "$(cat /tmp/public_url.txt)" && continue
  log "tunnel lost -> re-establish"
  MODE=none
  for attempt in 1 2 3; do
    try_serveo      && { MODE=serveo; break; }
    try_localhostrun && { MODE=localhostrun; break; }
    try_cloudflare  && { MODE=cf; break; }
    sleep 10
  done
  [ "$MODE" = none ] && log "still no tunnel"
done
