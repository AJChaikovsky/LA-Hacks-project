#!/usr/bin/env bash
# A5: start all three agents on the always-on MAC host, in a tmux session. Owner: Dev A, ticket A5 (#6).
#
#   ./scripts/run_all.sh
#
# Starts points (8001) -> paperwork (8002) -> counselor (8000), each in its own tmux window
# ("choices"), wrapped in caffeinate -i -s, logging to /tmp/<name>.log. Then runs ping.py and
# asserts each agent's SELF-CHECK passed and no mailbox is missing. Loud failure on any problem.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"
SESSION="choices"

fail() { echo "FATAL: $*" >&2; exit 1; }

# --- preconditions --------------------------------------------------------
[ -x "$PY" ] || fail ".venv not found — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
[ -f .env ]  || fail ".env not found"
grep -qE '^USE_STUBS=false' .env || fail "USE_STUBS must be false in .env (demo path = real modules, not fixtures)"
for v in COUNSELOR_SEED POINTS_SEED PAPERWORK_SEED ASI1_API_KEY \
         COUNSELOR_AGENT_ADDRESS POINTS_AGENT_ADDRESS PAPERWORK_AGENT_ADDRESS; do
  grep -qE "^${v}=.+" .env || fail "${v} missing or empty in .env"
done
command -v tmux >/dev/null 2>&1 || fail "tmux not installed (brew install tmux)"
for p in 8000 8001 8002; do
  if lsof -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "port $p already in use — an old prod process? stop it first (NEVER two processes on a prod seed)"
  fi
done

CAFF=""
command -v caffeinate >/dev/null 2>&1 && CAFF="caffeinate -i -s"

# --- start in tmux --------------------------------------------------------
tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" -n points    "cd '$ROOT'; $CAFF $PY agents/points_agent.py    2>&1 | tee /tmp/points.log"
tmux new-window  -t "$SESSION"    -n paperwork  "cd '$ROOT'; $CAFF $PY agents/paperwork_agent.py 2>&1 | tee /tmp/paperwork.log"
sleep 2
tmux new-window  -t "$SESSION"    -n counselor  "cd '$ROOT'; $CAFF $PY agents/orchestrator.py    2>&1 | tee /tmp/orchestrator.log"

echo "starting all 3 in tmux session '$SESSION' (points -> paperwork -> counselor)…"
sleep 15  # let mailboxes connect + SELF-CHECK (sleeps 4s) log

# --- assertions -----------------------------------------------------------
for n in points paperwork orchestrator; do
  cur="$(sed -n "$(grep -n 'Starting agent with address' "/tmp/$n.log" | tail -1 | cut -d: -f1),\$p" "/tmp/$n.log" 2>/dev/null)"
  echo "$cur" | grep -q 'SELF-CHECK ok'       || fail "$n: SELF-CHECK not ok — local server down. See /tmp/$n.log ; tmux attach -t $SESSION"
  if echo "$cur" | grep -q 'Agent mailbox not found'; then
    fail "$n: mailbox not found — connect it in the Agent Inspector. See /tmp/$n.log"
  fi
done

"$PY" scripts/ping.py || fail "ping.py failed — a health check is down. tmux attach -t $SESSION"

echo
echo "ALL 3 UP in tmux session '$SESSION'.  Attach to watch logs:  tmux attach -t $SESSION"
echo
echo "Lid-close sleep is NOT disabled by this script. To keep answering with the lid closed, run:"
echo "    sudo pmset -a disablesleep 1          # (re-enable after the event: sudo pmset -a disablesleep 0)"
echo "Keep the Mac on AC power."
