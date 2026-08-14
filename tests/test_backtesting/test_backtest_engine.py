from __future__ import annotations

import math
from datetime import date, timedelta

from options_advisor.backtest import engine
from options_advisor.broker.models import PriceBar
from options_advisor.config import load_settings


def _bars(closes, symbol="AAA", start=date(2021, 1, 4)):
    bars = []
    d = start
    for c in closes:
        # OHLC plano alrededor del cierre (rango chico) salvo que se indique
        bars.append(PriceBar(symbol=symbol, trade_date=d, open=c, high=c * 1.005, low=c * 0.995, close=c, volume=1_000_000))
        d += timedelta(days=1)
    return bars


def _settings():
    return load_settings().simulator.model_copy(update={"margin_mode": "naked", "broker_margin_factor": 0.56})


def test_bs_put_price_and_delta_sane():
    # Put OTM: precio positivo y delta entre -1 y 0.
    px = engine.bs_put_price(100, 90, 40 / 365, 0.3)
    assert px > 0
    d = engine.bs_put_delta(100, 90, 40 / 365, 0.3)
    assert -1 < d < 0


def test_select_strike_hits_target_delta():
    strike = engine.select_put_strike(100.0, 40 / 365, 0.3, target_delta=0.25)
    assert strike is not None and strike < 100
    d = abs(engine.bs_put_delta(100.0, strike, 40 / 365, 0.3))
    assert abs(d - 0.25) < 0.08


def test_naked_puts_uptrend_mostly_wins():
    # Serie que sube sostenido: los puts vendidos vencen OTM → ganadoras.
    closes = [100 * (1.004 ** i) for i in range(400)]
    trades = engine.backtest_naked_puts(_bars(closes), "AAA", _settings(),
                                        engine.NakedPutParams(target_delta=0.25, target_dte=30))
    assert len(trades) >= 3
    win_rate = sum(1 for t in trades if t.won) / len(trades)
    assert win_rate >= 0.7


def test_naked_puts_crash_produces_losses():
    # Peor caso (rueda apagada): un derrumbe deja pérdidas por asignación contadas como 'expired'.
    up = [100 * (1.003 ** i) for i in range(200)]
    peak = up[-1]
    crash = [peak * (0.97 ** i) for i in range(1, 60)]   # -3%/día sostenido
    trades = engine.backtest_naked_puts(_bars(up + crash), "AAA", _settings(),
                                        engine.NakedPutParams(target_delta=0.30, target_dte=30, model_assignment=False))
    assert any(t.pnl < 0 for t in trades)
    assert any(t.close_reason == "expired" and t.pnl < 0 for t in trades)


def test_wheel_converts_itm_expiry_into_assignment():
    # Con la rueda ON, una expiración ITM ya NO es 'expired' con pérdida: pasa a 'assigned_*'.
    up = [100 * (1.003 ** i) for i in range(200)]
    peak = up[-1]
    crash = [peak * (0.97 ** i) for i in range(1, 60)]
    trades = engine.backtest_naked_puts(_bars(up + crash), "AAA", _settings(),
                                        engine.NakedPutParams(target_delta=0.30, target_dte=30, model_assignment=True))
    assert any(t.close_reason.startswith("assigned") for t in trades)
    # Todo lo que quedó como 'expired' con la rueda es vencimiento OTM = ganancia.
    for t in trades:
        if t.close_reason == "expired":
            assert t.pnl > 0


def test_flat_profit_target_used_when_set():
    # profit_target_pct plano: no depende de las reglas escalonadas.
    closes = [100 * (1.002 ** i) for i in range(300)]
    trades = engine.backtest_naked_puts(_bars(closes), "AAA", _settings(),
                                        engine.NakedPutParams(target_delta=0.25, target_dte=30, profit_target_pct=0.45))
    assert len(trades) >= 1  # corre sin romper con objetivo plano


def test_summary_has_equity_and_drawdown():
    closes = [100 * (1.002 ** i) for i in range(300)]
    trades = engine.backtest_naked_puts(_bars(closes), "AAA", _settings())
    s = engine.summarize(trades)
    assert s["n"] == len(trades)
    assert len(s["equity_curve"]) == len(trades)
    assert s["max_drawdown"] <= 0.0
    assert 0 <= s["win_rate"] <= 100


def test_iron_condor_calm_wins_volatile_loses():
    calm = PriceBar(symbol="X", trade_date=date(2022, 1, 3), open=500, high=501, low=499, close=500, volume=1)
    wild = PriceBar(symbol="X", trade_date=date(2022, 1, 4), open=500, high=520, low=480, close=515, volume=1)
    # necesita historia previa para la IV
    hist = _bars([500] * 30, symbol="X", start=date(2021, 11, 1))
    trades = engine.backtest_iron_condor_daily(hist + [calm, wild], "X")
    by_day = {t.exit_date: t for t in trades}
    assert by_day[date(2022, 1, 3)].won is True
    assert by_day[date(2022, 1, 4)].won is False


def test_sweep_delta_returns_ranking():
    closes = [100 * (1.002 ** i) for i in range(300)]
    res = engine.sweep_delta({"AAA": _bars(closes)}, _settings(), deltas=(0.20, 0.30))
    assert len(res) == 2
    assert all("win_rate" in r and "total_pnl" in r for r in res)


def test_empty_bars_no_trades():
    assert engine.backtest_naked_puts([], "AAA", _settings()) == []
    assert engine.summarize([])["n"] == 0


def test_rsi_trigger_reduces_trades():
    # Serie que sube (RSI alto casi siempre): con gatillo RSI<=30 casi no debería operar.
    closes = [100 * (1.004 ** i) for i in range(300)]
    bars = _bars(closes)
    base = engine.backtest_naked_puts(bars, "AAA", _settings(), engine.NakedPutParams(target_dte=30))
    gated = engine.backtest_naked_puts(bars, "AAA", _settings(),
                                       engine.NakedPutParams(target_dte=30, rsi_max=30))
    assert len(gated) < len(base)


def test_earnings_backtest_one_trade_per_earnings():
    closes = [100 * (1.001 ** i) for i in range(300)]
    bars = _bars(closes, start=date(2021, 1, 4))
    # dos earnings dentro del rango
    earnings = [bars[120].trade_date, bars[240].trade_date]
    trades = engine.backtest_earnings_puts(bars, earnings, "AAA", _settings(),
                                           engine.NakedPutParams(target_delta=0.25, target_dte=30), days_before=5)
    assert len(trades) <= 2
    assert all(t.strategy == "naked_put_earnings" for t in trades)
    # cada trade abrió antes de su earnings
    for t in trades:
        assert any(t.entry_date < e for e in earnings)


def test_earnings_backtest_empty_when_no_dates():
    bars = _bars([100] * 60)
    assert engine.backtest_earnings_puts(bars, [], "AAA", _settings()) == []


def test_near_support_trigger_runs():
    # No debe romper; con un umbral amplio deja pasar algunas entradas.
    closes = [100 + 5 * math.sin(i / 10) for i in range(300)]
    bars = _bars(closes)
    out = engine.backtest_naked_puts(bars, "AAA", _settings(),
                                     engine.NakedPutParams(target_dte=30, near_support_pct=0.10))
    assert isinstance(out, list)


def test_summary_breakdown_fields():
    # Serie que sube y luego cae: mezcla ganadoras y asignaciones (venció ITM).
    up = [100 * (1.003 ** i) for i in range(200)]
    peak = up[-1]
    crash = [peak * (0.97 ** i) for i in range(1, 60)]
    trades = engine.backtest_naked_puts(_bars(up + crash), "AAA", _settings(),
                                        engine.NakedPutParams(target_delta=0.30, target_dte=30, model_assignment=False))
    s = engine.summarize(trades)
    # neto = ganadoras + perdedoras
    assert round(s["gross_win"] + s["gross_loss"], 2) == s["total_pnl"]
    assert s["wins"] + s["losses"] == s["n"]
    assert set(s["by_reason"].keys()).issubset(
        {"profit_target", "near_strike", "expired", "stop_loss", "assigned_recovered", "assigned_open"})
    assert s["assignments"] >= 1            # el crash tuvo que asignar al menos una
    assert s["assignment_loss"] <= 0.0


def test_earnings_exit_before_vs_hold():
    closes = [100 * (1.001 ** i) for i in range(300)]
    bars = _bars(closes, start=date(2021, 1, 4))
    earnings = [bars[120].trade_date, bars[240].trade_date]
    hold = engine.backtest_earnings_puts(bars, earnings, "AAA", _settings(),
                                         engine.NakedPutParams(target_delta=0.25, target_dte=30),
                                         days_before=5, hold_through=True)
    exitb = engine.backtest_earnings_puts(bars, earnings, "AAA", _settings(),
                                          engine.NakedPutParams(target_delta=0.25, target_dte=30),
                                          days_before=5, hold_through=False)
    # salir antes deja el motivo 'pre_earnings_exit' y cierra ANTES de la fecha de earnings
    assert exitb and all(t.close_reason == "pre_earnings_exit" for t in exitb)
    for t in exitb:
        assert any(t.exit_date < e for e in earnings)
    assert hold  # aguantar también produce operaciones


def test_portfolio_replay_100k():
    closes = [100 * (1.002 ** i) for i in range(400)]
    trades = engine.backtest_naked_puts(_bars(closes), "AAA", _settings(),
                                        engine.NakedPutParams(target_delta=0.25, target_dte=30))
    port = engine.portfolio_replay(trades, initial_capital=100_000, max_pct_per_trade=0.10)
    assert port["initial_capital"] == 100_000
    assert port["taken"] >= 1
    # equity final = 100K + P&L neto realizado
    assert round(port["final_equity"], 2) == round(100_000 + port["realized_pnl"], 2)
    assert port["peak_capital_used"] <= 100_000 * 1.0001   # nunca usa más que el capital
    assert isinstance(port["equity_curve"], list) and port["equity_curve"]


def test_portfolio_replay_skips_when_capital_tiny():
    closes = [100 * (1.002 ** i) for i in range(300)]
    trades = engine.backtest_naked_puts(_bars(closes), "AAA", _settings(),
                                        engine.NakedPutParams(target_delta=0.25, target_dte=30))
    # Capital chiquito: no alcanza para casi nada → saltea la mayoría.
    port = engine.portfolio_replay(trades, initial_capital=1000, max_pct_per_trade=0.10)
    assert port["skipped"] >= 1


def test_portfolio_replay_empty():
    port = engine.portfolio_replay([], initial_capital=100_000)
    assert port["taken"] == 0 and port["final_equity"] == 100_000


def test_portfolio_exposure_reported():
    closes = [100 * (1.002 ** i) for i in range(300)]
    trades = engine.backtest_naked_puts(_bars(closes), "AAA", _settings(),
                                        engine.NakedPutParams(target_delta=0.25, target_dte=30, model_assignment=False))
    port = engine.portfolio_replay(trades, 100_000, 0.10)
    # exposición (notional) tiene que ser MAYOR que el colateral (margen naked chico)
    assert port["peak_exposure"] >= port["peak_capital_used"]
    assert port["peak_exposure_pct"] >= 0
    assert "return_on_exposure_pct" in port
