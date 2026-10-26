#!/usr/bin/env bash
# Keep the day-trader skills' state across cloud sessions. The container is
# wiped between sessions, and trader/ holds what the day limits depend on:
# today's risk, trade count, day trades (trader/state/risk.json), approvals
# and fills (trader/state/broker.json).
#
#   scripts/stock_state.sh restore   rules from config/trader.json, state from desk-log
#   scripts/stock_state.sh save      state back to desk-log (trader/ there)
set -euo pipefail
mode="${1:?restore or save}"
cd "$(dirname "$0")/.."

# The owner's rules live in the repo; trader/config.json is always a copy.
mkdir -p trader/state
if [ -f config/trader.json ]; then
  cp config/trader.json trader/config.json
else
  echo "WARNING: no config/trader.json yet; the skills will ask for their rules"
fi
git fetch -q origin desk-log

if [ "$mode" = restore ]; then
  for f in $(git ls-tree -r --name-only origin/desk-log -- trader); do
    [ "$f" = trader/config.json ] && continue
    mkdir -p "$(dirname "$f")"
    git show "origin/desk-log:$f" > "$f"
  done
  echo "restored: $(git ls-tree -r --name-only origin/desk-log -- trader | wc -l) files from desk-log"
  exit 0
fi

log=$(mktemp -d)
git worktree add -q "$log" origin/desk-log
mkdir -p "$log/trader"
# The PTR PDFs and the symbol universe are caches the skills rebuild.
find "$log/trader" -mindepth 1 -delete
tar -C trader --exclude ./config.json --exclude ./universe.txt --exclude ./state/ptr-pdfs -cf - . | tar -C "$log/trader" -xf -
git -C "$log" add -A trader
if git -C "$log" diff --cached --quiet; then
  echo "nothing new to save"
else
  git -C "$log" -c user.name="trades desk" -c user.email="desk@users.noreply.github.com" \
      commit -qm "stock desk state $(date -u +%Y-%m-%dT%H:%MZ)"
  saved=no
  for i in 1 2 3; do
    if git -C "$log" push -q origin HEAD:desk-log; then saved=yes; break; fi
    git -C "$log" pull -q --rebase origin desk-log || break
  done
  [ "$saved" = yes ] && echo "saved to desk-log" || echo "WARNING: could not save trader/ state to desk-log"
fi
git worktree remove --force "$log"
