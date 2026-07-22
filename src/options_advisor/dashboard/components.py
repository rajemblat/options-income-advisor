from __future__ import annotations

import os
import sqlite3

import streamlit as st
from dotenv import load_dotenv

from options_advisor.broker import get_broker_client
from options_advisor.broker.base import BrokerClient
from options_advisor.config import PROJECT_ROOT, Settings, load_settings, load_symbols
from options_advisor.storage import db

load_dotenv(PROJECT_ROOT / ".env")


@st.cache_resource
def get_settings() -> Settings:
    return load_settings()


@st.cache_resource
def get_symbols() -> list[str]:
    return load_symbols()


@st.cache_resource
def get_connection() -> sqlite3.Connection:
    settings = get_settings()
    return db.connect(settings.database.resolved_path())


@st.cache_resource
def get_broker() -> BrokerClient:
    return get_broker_client(get_settings())


def get_anthropic_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")


def risk_badge(score: int) -> str:
    if score >= 80:
        return f"🟢 {score}"
    if score >= 65:
        return f"🟡 {score}"
    return f"🔴 {score}"
