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


class KeeperRunRequest(BaseModel):
    ticks: int = Field(10, ge=1, le=10_000, description="burst length for the testnet keeper")
