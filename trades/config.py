"""Settings read from the environment (and .env at the repo root)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Paper only. There is deliberately no setting for the live endpoint: trading
# real money should take a code change someone reviews, not a typo in .env.
ALPACA_TRADING_URL = "https://paper-api.alpaca.markets"
ALPACA_DATA_URL = "https://data.alpaca.markets"

OPENROUTER_JEV_URL = "https://openrouter.ai/api/v1/systemone"
TYPESAFE_JEV_URL = "https://api.typesafe.ai/v1/systemone"


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"{name} must be true or false, got {raw!r}")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    events_path: Path
    max_position_pct: float
    max_daily_loss_pct: float
    default_size_pct: float
    allow_short: bool
    block_threshold: float
    proposal_max_age_min: float
    paper_execution: bool
    allowed_setups: tuple[str, ...]
    max_crypto_position_pct: float
    max_crypto_exposure_pct: float
    max_risk_per_trade_pct: float
    crypto_universe: tuple[str, ...]
    crypto_allowed_setups: tuple[str, ...]
    crypto_stop_limit_buffer_pct: float
    jev_url: str | None
    jev_key: str | None
    jev_model: str
    alpaca_key: str | None
    alpaca_secret: str | None
    # Keys stored as the cloud environment's network secrets: the session never
    # sees them; the egress proxy adds them to requests for their hosts.
    network_secrets: bool = False

    @property
    def jev_configured(self) -> bool:
        return bool(self.jev_url and (self.jev_key or self.network_secrets))

    @property
    def alpaca_configured(self) -> bool:
        return bool(self.alpaca_key and self.alpaca_secret) or self.network_secrets

    def setups_for(self, ticker: str) -> tuple[str, ...]:
        from trades.assets import is_crypto
        return self.crypto_allowed_setups if is_crypto(ticker) else self.allowed_setups

    def restrictions(self) -> list[dict]:
        try:
            return json.loads(self.events_path.read_text()).get("restrictions", [])
        except FileNotFoundError:
            return []


def load() -> Settings:
    openrouter = os.environ.get("OPENROUTER_API_KEY") or None
    typesafe = os.environ.get("TYPESAFE_API_KEY") or None
    model = os.environ.get("JEV_MODEL") or None
    if openrouter:
        jev_url, jev_key, jev_model = OPENROUTER_JEV_URL, openrouter, model or "jev-1.13"
    elif typesafe:
        jev_url, jev_key, jev_model = TYPESAFE_JEV_URL, typesafe, model or "jev-latest"
    elif _bool("NETWORK_SECRETS", False):
        jev_url, jev_key, jev_model = OPENROUTER_JEV_URL, None, model or "jev-1.13"
    else:
        jev_url, jev_key, jev_model = None, None, model or "jev-1.13"

    setups = os.environ.get("ALLOWED_SETUPS") or (
        "pullback within an established trend;"
        "breakout from a defined range on rising volume;"
        "fundamental or news thesis citing specific reported figures or filings;"
        "post-earnings drift after a reported surprise"
    )
    crypto_setups = os.environ.get("CRYPTO_ALLOWED_SETUPS") or (
        "pullback to the rising 20- or 50-day average while price holds above its 200-day average;"
        "daily close breaking out of a range of at least two weeks on rising volume;"
        "flow or news thesis citing specific reported figures such as ETF net flows, "
        "exchange reserves or a dated protocol upgrade"
    )
    universe = os.environ.get("CRYPTO_UNIVERSE") or "BTC/USD,ETH/USD,SOL/USD"
    data_dir = Path(os.environ.get("TRADES_DATA_DIR") or ROOT / "data")
    return Settings(
        data_dir=data_dir,
        events_path=Path(os.environ.get("TRADES_EVENTS_PATH") or ROOT / "config" / "events.json"),
        max_position_pct=_float("MAX_POSITION_PCT", 5.0),
        max_daily_loss_pct=_float("MAX_DAILY_LOSS_PCT", 2.0),
        default_size_pct=_float("DEFAULT_SIZE_PCT", 3.0),
        allow_short=_bool("ALLOW_SHORT", False),
        block_threshold=_float("BLOCK_THRESHOLD", 0.5),
        proposal_max_age_min=_float("PROPOSAL_MAX_AGE_MIN", 60.0),
        paper_execution=_bool("PAPER_EXECUTION", False),
        allowed_setups=tuple(s.strip() for s in setups.split(";") if s.strip()),
        max_crypto_position_pct=_float("MAX_CRYPTO_POSITION_PCT", 10.0),
        max_crypto_exposure_pct=_float("MAX_CRYPTO_EXPOSURE_PCT", 30.0),
        max_risk_per_trade_pct=_float("MAX_RISK_PER_TRADE_PCT", 1.0),
        crypto_universe=tuple(u.strip().upper() for u in universe.split(",") if u.strip()),
        crypto_allowed_setups=tuple(s.strip() for s in crypto_setups.split(";") if s.strip()),
        crypto_stop_limit_buffer_pct=_float("CRYPTO_STOP_LIMIT_BUFFER_PCT", 1.5),
        jev_url=jev_url,
        jev_key=jev_key,
        jev_model=jev_model,
        alpaca_key=os.environ.get("ALPACA_API_KEY_ID") or None,
        alpaca_secret=os.environ.get("ALPACA_API_SECRET_KEY") or None,
        network_secrets=_bool("NETWORK_SECRETS", False),
    )
