# day-trader-skill (vendored)

Source: https://github.com/Jakeschincariol/day-trader-skill
Pinned commit: 3d0f4ef (2026-10-20), MIT licence (see LICENSE).

The eleven skills live in `.claude/skills/trader-*` so every Claude session in
this repository can use them. This folder keeps the upstream README, licence
and tests. The code was read before vendoring: it reads only free public data
(Yahoo Finance, SEC EDGAR, House Clerk, Nasdaq/NYSE symbol lists, Stooq),
reads no API keys, writes only under `trader/`, and its broker skill never
contacts a broker itself. To upgrade, read the new upstream diff first, then
copy it over and update the pinned commit here.
