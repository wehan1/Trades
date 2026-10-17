#!/usr/bin/env bash
# One scheduled run in a fresh cloud session: restore the desk log, run the
# session desk (or only the stop check), save the log back to the desk-log
# branch. Usage: scripts/scheduled_run.sh session|protect
set -euo pipefail
mode="${1:?session or protect}"
cd "$(dirname "$0")/.."

python3 -m venv .venv >/dev/null 2>&1 || true
.venv/bin/pip install -q -e . >/dev/null

# Keys are network secrets; this session never sees them.
export NETWORK_SECRETS=true
# Paper orders on. The live endpoint is not configurable (trades/config.py).
export PAPER_EXECUTION=true
# The owner's risk settings for the session desk (approved 2026-10-17):
# up to 75% of the account in crypto, 3% account risk per entry.
export MAX_CRYPTO_POSITION_PCT=75 MAX_CRYPTO_EXPOSURE_PCT=75 MAX_RISK_PER_TRADE_PCT=3

mkdir -p data
git fetch -q origin desk-log
git show origin/desk-log:records.jsonl > data/records.jsonl 2>/dev/null || : > data/records.jsonl
before=$(wc -l < data/records.jsonl)

if [ "$mode" = session ]; then
  .venv/bin/trades session
fi
.venv/bin/trades protect

after=$(wc -l < data/records.jsonl)
if [ "$after" -gt "$before" ]; then
  log=$(mktemp -d)
  git worktree add -q "$log" origin/desk-log
  cp data/records.jsonl "$log/records.jsonl"
  git -C "$log" -c user.name="trades desk" -c user.email="desk@users.noreply.github.com" \
      commit -qam "desk log: $mode $(date -u +%Y-%m-%dT%H:%MZ)"
  for i in 1 2 3; do
    git -C "$log" push -q origin HEAD:desk-log && break
    git -C "$log" pull -q --rebase origin desk-log
  done
  git worktree remove --force "$log"
fi
