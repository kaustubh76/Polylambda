"""Pydantic request bodies for the POST endpoints."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ScoreRequest(BaseModel):
    category: str = Field("politics", description="derived market category (the λ_select stratum)")
    fill_count: int = Field(500, ge=0, description="liquidity proxy → market_size = log1p(fills)")
    price: float = Field(0.62, gt=0.0, lt=1.0, description="current YES mid probability")
    proposer: str | None = Field(None, description="optional proposer address (reliability feature)")
    inventory: float = Field(0.0, description="signed position (+long / -short), for the exit-gate eval")
    horizon_days: float = Field(7.0, gt=0.0, description="time to resolution (T−t), days")


class SessionRequest(BaseModel):
    scenario: str = Field("dispute_defense", description="dispute_defense | live_quoting")
    # dispute_defense knobs
    category: str = "politics"
    entry_price: float = Field(0.62, gt=0.0, lt=1.0)
    inventory: float = Field(100.0, description="starting position both arms hold")
    dispute_tick: int = Field(5, ge=1, le=40)
    gap_logit: float = Field(-1.35, description="realized dispute jump size (logit)")
    n_ticks: int = Field(13, ge=3, le=60)
    # live_quoting knobs
    n_markets: int = Field(4, ge=2, le=6)
    seed: int = 7
