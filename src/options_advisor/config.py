from __future__ import annotations

import logging.config
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[2]

RiskLevel = Literal["conservador", "moderado", "agresivo"]
ExperienceLevel = Literal["principiante", "intermedio", "avanzado"]
RiskPreference = Literal["defined", "undefined"]
BrokerMode = Literal["mock", "schwab"]


class BrokerSettings(BaseModel):
    mode: BrokerMode
    fixtures_dir: Path

    def resolved_fixtures_dir(self) -> Path:
        return PROJECT_ROOT / self.fixtures_dir


class DatabaseSettings(BaseModel):
    path: Path

    def resolved_path(self) -> Path:
        return PROJECT_ROOT / self.path


class MarketSettings(BaseModel):
    risk_free_rate: float


class LlmSettings(BaseModel):
    model: str
    max_tokens: int


class SchedulerSettings(BaseModel):
    timezone: str
    poll_interval_minutes: int
    market_open_snapshot_time: str
    market_close_snapshot_time: str
    market_hours_start: str
    market_hours_end: str
    premarket_digest_time: str


class InvestorProfileSettings(BaseModel):
    capital_available: float
    loss_tolerance_pct: float
    experience_level: ExperienceLevel
    risk_preference: RiskPreference
    risk_level: RiskLevel


class ConvictionThresholds(BaseModel):
    conservador: int
    moderado: int
    agresivo: int

    def for_risk_level(self, risk_level: RiskLevel) -> int:
        return getattr(self, risk_level)


class IvRankSettings(BaseModel):
    min_sessions_for_real_iv: int
    full_window_sessions: int
    hv_window_days: int


class Settings(BaseModel):
    broker: BrokerSettings
    database: DatabaseSettings
    market: MarketSettings
    llm: LlmSettings
    scheduler: SchedulerSettings
    investor_profile: InvestorProfileSettings
    conviction_thresholds: ConvictionThresholds
    iv_rank: IvRankSettings


class SymbolsConfig(BaseModel):
    symbols: list[str]


def load_settings(path: Path | None = None) -> Settings:
    path = path or (PROJECT_ROOT / "config" / "settings.yaml")
    with open(path) as f:
        raw = yaml.safe_load(f)
    return Settings.model_validate(raw)


def load_symbols(path: Path | None = None) -> list[str]:
    path = path or (PROJECT_ROOT / "config" / "symbols.yaml")
    with open(path) as f:
        raw = yaml.safe_load(f)
    return SymbolsConfig.model_validate(raw).symbols


def configure_logging(path: Path | None = None) -> None:
    path = path or (PROJECT_ROOT / "config" / "logging.yaml")
    with open(path) as f:
        raw = yaml.safe_load(f)
    logging.config.dictConfig(raw)
