from __future__ import annotations

from datetime import date, timedelta

import pytest

from options_advisor.broker.models import Greeks, OptionChain, OptionContract
from options_advisor.config import SimulatorSettings
from options_advisor.simulator import positions
from options_advisor.storage import db
from options_advisor.storage import repository as repo

AS_OF = date(2026, 3, 2)


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    repo.init_simulated_account(c, 100_000.0, __import__("datetime").datetime(2026, 3, 1))
    return c


def test_evaluate_put_close_pure_matches_rules():
    """La decisión de cierre EXTRAÍDA (usada por el simulador Y el cierre real) dispara con las mismas
    reglas: profit target al 30% de la prima, y stop-loss por múltiplo."""
    s = _settings(profit_target_pct=0.30, stop_loss_multiple=2.0, close_at_dte=0)
    # Recién abierta (valor ≈ prima): NO cierra.
    close, reason = positions.evaluate_put_close(1.00, 1.00, 90.0, 100.0, dte=20, age_days=0, settings=s)
    assert close is False and reason is None
    # Ganancia grande: la opción cayó a 0.20 → +80% de la prima, supera cualquier tramo → profit_target.
    close, reason = positions.evaluate_put_close(1.00, 0.20, 90.0, 100.0, dte=25, age_days=1, settings=s)
    assert close is True and reason == "profit_target"
    # Pérdida grande: la opción vale 3× la prima (stop_loss_multiple=2 → 1×(1+2)=3) → stop_loss.
    close, reason = positions.evaluate_put_close(1.00, 3.10, 90.0, 100.0, dte=20, age_days=1, settings=s)
    assert close is True and reason == "stop_loss"


def _settings(**overrides) -> SimulatorSettings:
    defaults = dict(
        enabled=True,
        initial_capital=100_000.0,
        max_position_pct=0.10,
        profit_target_pct=0.30,
        dte_range=(30, 45),
        max_delta=0.18,
        rsi_range=(30.0, 40.0),
        iv_rank_min=50.0,
        iv_percentile_min=50.0,
        support_max_distance_pct=0.05,
        weekly_support_max_distance_pct=0.09,
        sma_periods=[8, 20, 50],
        sma_min_distance_pct=0.03,
    )
    defaults.update(overrides)
    return SimulatorSettings(**defaults)


def _put(strike: float, expiration: date, mid: float) -> OptionContract:
    half_spread = 0.05
    return OptionContract(
        symbol="TST",
        option_type="put",
        strike=strike,
        expiration=expiration,
        bid=round(mid - half_spread, 2),
        ask=round(mid + half_spread, 2),
        last_price=mid,
        implied_volatility=0.30,
        open_interest=500,
        volume=50,
        greeks=Greeks(delta=-0.15, gamma=0.01, theta=-0.02, vega=0.05, rho=0.01, source="calculated"),
    )


def test_size_position_limits_to_max_pct_of_equity():
    result = positions.size_position(strike=100.0, underlying_price=110.0, premium=1.5, cash_available=100_000.0, account_equity=100_000.0, settings=_settings(max_position_pct=0.10))
    assert result is not None
    assert result.quantity == 1  # 10% de 100k = 10,000 / (100*100 por contrato) = 1 contrato
    assert result.collateral == 10_000.0


def test_size_position_none_when_strike_too_expensive_for_any_contract():
    result = positions.size_position(strike=5000.0, underlying_price=5200.0, premium=1.5, cash_available=100_000.0, account_equity=100_000.0, settings=_settings(max_position_pct=0.01))
    assert result is None


def test_size_position_capped_by_available_cash_even_if_equity_allows_more():
    # 50% de 100k de equity permitiría 5 contratos (50,000 / 10,000 por contrato), pero solo
    # hay 25,000 de cash libre — se recorta a 2 contratos (25,000 // 10,000).
    result = positions.size_position(strike=100.0, underlying_price=110.0, premium=1.5, cash_available=25_000.0, account_equity=100_000.0, settings=_settings(max_position_pct=0.50))
    assert result is not None
    assert result.quantity == 2
    assert result.collateral == 20_000.0


def test_size_position_price_tier_sizing():
    s = _settings(use_price_tier_sizing=True, price_tier_low=70.0, price_tier_high=160.0, contracts_cheap=5, contracts_mid=3, contracts_expensive=2)
    cheap = positions.size_position(strike=50.0, underlying_price=55.0, premium=1.5, cash_available=100_000.0, account_equity=100_000.0, settings=s)
    assert cheap.quantity == 5  # acción < 70 → 5 contratos
    mid = positions.size_position(strike=100.0, underlying_price=120.0, premium=1.5, cash_available=100_000.0, account_equity=100_000.0, settings=s)
    assert mid.quantity == 3    # 70-160 → 3
    exp = positions.size_position(strike=180.0, underlying_price=200.0, premium=1.5, cash_available=100_000.0, account_equity=100_000.0, settings=s)
    assert exp.quantity == 2    # >= 160 → 2


def test_tier_contracts_real_trading_boundaries():
    """Tabla de trading real (usuario 2026-08-07): hasta $50 → 4 · $50-$400 → 3 · $400+ → 1.
    Bordes: $50 inclusive en el tramo bajo (4), $400 inclusive en el alto (1)."""
    s = _settings(use_price_tier_sizing=True, price_tier_low=50.0, price_tier_high=400.0,
                  contracts_cheap=4, contracts_mid=3, contracts_expensive=1)
    assert positions._tier_contracts(30.0, s) == 4
    assert positions._tier_contracts(50.0, s) == 4     # "hasta $50" = inclusive
    assert positions._tier_contracts(50.01, s) == 3
    assert positions._tier_contracts(399.0, s) == 3
    assert positions._tier_contracts(400.0, s) == 1    # "$400 o más" = inclusive
    assert positions._tier_contracts(650.0, s) == 1


def test_max_puts_per_day_flag(conn):
    """El tope diario de naked puts es ajustable desde el dashboard y cae al default si no se tocó."""
    assert repo.get_max_puts_per_day(conn, 5) == 5      # sin flag → default de config
    repo.set_max_puts_per_day(conn, 12)
    assert repo.get_max_puts_per_day(conn, 5) == 12
    repo.set_max_puts_per_day(conn, 0)                  # mínimo 1 (se fuerza)
    assert repo.get_max_puts_per_day(conn, 5) == 1


def test_size_position_tier_capped_by_cash():
    s = _settings(use_price_tier_sizing=True, contracts_cheap=5, price_tier_low=70.0)
    # 5 contratos de strike 60 = 30,000 de garantía, pero solo hay 12,000 de cash → 2 contratos
    r = positions.size_position(strike=60.0, underlying_price=55.0, premium=1.5, cash_available=12_000.0, account_equity=100_000.0, settings=s)
    assert r.quantity == 2
    assert r.collateral == 12_000.0


def test_open_fill_negotiates_toward_ask():
    # mid 1.50, bid 1.45, ask 1.55. edge 0.5 → 1.50 + 0.5*(1.55-1.50) = 1.525
    contract = _put(strike=80.0, expiration=AS_OF + timedelta(days=35), mid=1.50)
    assert positions._open_fill(contract, _settings(fill_edge_pct=0.5)) == 1.525
    assert positions._open_fill(contract, _settings(fill_edge_pct=1.0)) == 1.55  # ask (vender caro)
    assert positions._open_fill(contract, _settings(fill_edge_pct=0.0)) == 1.50  # mid


def test_close_fill_negotiates_toward_bid():
    contract = _put(strike=80.0, expiration=AS_OF + timedelta(days=35), mid=1.50)
    assert positions._close_fill(contract, _settings(fill_edge_pct=0.5)) == 1.475  # 1.50 - 0.5*(1.50-1.45)
    assert positions._close_fill(contract, _settings(fill_edge_pct=1.0)) == 1.45   # bid (comprar barato)


def test_open_position_reserves_collateral_and_credits_premium(conn):
    contract = _put(strike=80.0, expiration=AS_OF + timedelta(days=35), mid=1.50)
    positions.open_position(conn, "TST", contract, quantity=3, collateral=24_000.0, entry_date=AS_OF)

    account = repo.get_simulated_account(conn)
    # 100,000 - 24,000 (garantía) + 1.50*100*3 (prima) = 76,450
    assert account["cash"] == pytest.approx(76_450.0)

    open_positions = repo.get_open_simulated_positions(conn, "TST")
    assert len(open_positions) == 1
    assert open_positions[0]["entry_premium"] == 1.50
    assert open_positions[0]["status"] == "open"


def test_mark_position_closes_at_profit_target(conn):
    contract = _put(strike=80.0, expiration=AS_OF + timedelta(days=35), mid=2.00)
    position_id = positions.open_position(conn, "TST", contract, quantity=2, collateral=16_000.0, entry_date=AS_OF)
    row = repo.get_open_simulated_positions(conn, "TST")[0]
    assert row["id"] == position_id

    # El valor actual del put cayó a 1.30 (35% menos que la prima cobrada de 2.00) — supera el 30%.
    chain = OptionChain(symbol="TST", as_of=AS_OF, underlying_price=90.0, contracts=[_put(80.0, contract.expiration, 1.30)])
    outcome = positions.mark_position(conn, row, chain, underlying_price=90.0, as_of=AS_OF + timedelta(days=5), settings=_settings())

    assert outcome["closed"] is True
    assert outcome["reason"] == "profit_target"
    closed = repo.get_closed_simulated_positions(conn)
    assert len(closed) == 1
    assert closed[0]["realized_pnl"] == pytest.approx((2.00 - 1.30) * 100 * 2)
    assert repo.get_open_simulated_positions(conn, "TST") == []


def test_mark_position_closes_at_expiration_using_intrinsic_value(conn):
    expiration = AS_OF + timedelta(days=30)
    contract = _put(strike=80.0, expiration=expiration, mid=1.00)
    positions.open_position(conn, "TST", contract, quantity=1, collateral=8_000.0, entry_date=AS_OF)
    row = repo.get_open_simulated_positions(conn, "TST")[0]

    # Vencimiento ya pasó, contrato ITM (precio 75 < strike 80) — se cierra al valor intrínseco.
    outcome = positions.mark_position(conn, row, None, underlying_price=75.0, as_of=expiration, settings=_settings())
    assert outcome["closed"] is True
    assert outcome["reason"] == "expired"
    assert outcome["current_value"] == 5.0  # max(80-75, 0)


def test_mark_position_stays_open_when_no_trigger(conn):
    expiration = AS_OF + timedelta(days=35)
    contract = _put(strike=80.0, expiration=expiration, mid=1.00)
    positions.open_position(conn, "TST", contract, quantity=1, collateral=8_000.0, entry_date=AS_OF)
    row = repo.get_open_simulated_positions(conn, "TST")[0]

    chain = OptionChain(symbol="TST", as_of=AS_OF, underlying_price=95.0, contracts=[_put(80.0, expiration, 0.95)])
    outcome = positions.mark_position(conn, row, chain, underlying_price=95.0, as_of=AS_OF + timedelta(days=2), settings=_settings())

    assert outcome["closed"] is False
    assert repo.get_open_simulated_positions(conn, "TST") != []
    marked = repo.get_open_simulated_positions(conn, "TST")[0]
    assert marked["last_unrealized_pnl"] == pytest.approx((1.00 - 0.95) * 100)


def test_current_contract_value_returns_none_when_missing_and_not_expired():
    # Fix de auditoría: si el put no está en la cadena y NO venció, devolver None (hueco de
    # datos) en vez de asumir intrínseco 0 y auto-cerrar por una "ganancia" del 100% falsa.
    expiration = AS_OF + timedelta(days=30)
    empty_chain = OptionChain(symbol="TST", as_of=AS_OF, underlying_price=70.0, contracts=[])
    value = positions.current_contract_value(empty_chain, strike=80.0, expiration=expiration, underlying_price=70.0)
    assert value is None


def test_mark_position_does_not_close_when_missing_and_not_expired(conn):
    # Fix auditoría: contrato ausente en la cadena y NO vencido => NO cerrar (hueco de datos),
    # nada de auto-cierre espurio al 100%.
    contract = _put(strike=80.0, expiration=AS_OF + timedelta(days=30), mid=1.00)
    positions.open_position(conn, "TST", contract, quantity=1, collateral=8_000.0, entry_date=AS_OF)
    row = repo.get_open_simulated_positions(conn, "TST")[0]
    empty_chain = OptionChain(symbol="TST", as_of=AS_OF, underlying_price=90.0, contracts=[])
    outcome = positions.mark_position(conn, row, empty_chain, underlying_price=90.0, as_of=AS_OF + timedelta(days=5), settings=_settings())
    assert outcome["closed"] is False
    assert outcome.get("skipped") is True
    assert len(repo.get_open_simulated_positions(conn, "TST")) == 1


def test_mark_position_stop_loss_closes_when_option_triples(conn):
    contract = _put(strike=80.0, expiration=AS_OF + timedelta(days=35), mid=1.00)
    positions.open_position(conn, "TST", contract, quantity=1, collateral=8_000.0, entry_date=AS_OF)
    row = repo.get_open_simulated_positions(conn, "TST")[0]
    # stop_loss_multiple=2.0 => cerrar si la opción vale 3x la prima (1.00 -> 3.00).
    chain = OptionChain(symbol="TST", as_of=AS_OF, underlying_price=78.0, contracts=[_put(80.0, contract.expiration, 3.00)])
    outcome = positions.mark_position(conn, row, chain, underlying_price=78.0, as_of=AS_OF + timedelta(days=5), settings=_settings(stop_loss_multiple=2.0))
    assert outcome["closed"] is True
    assert outcome["reason"] == "stop_loss"


def _mark_open(conn, expiration, mark, underlying, as_of):
    """Marca la posición TST abierta a `mark` (mid del put en la cadena) y devuelve el outcome."""
    row = repo.get_open_simulated_positions(conn, "TST")[0]
    chain = OptionChain(symbol="TST", as_of=AS_OF, underlying_price=underlying, contracts=[_put(80.0, expiration, mark)])
    return positions.mark_position(conn, row, chain, underlying_price=underlying, as_of=as_of, settings=_settings())


def test_profit_dia_5_pide_35pct(conn):
    """Escalón por ANTIGÜEDAD (usuario 2026-08-12): días 0-2 → 30%, días 3-6 → 35%, día 7+ → 40%.

    Este test probaba el esquema viejo de semana1/semana2 (cerraba al 30% en el día 5) y quedó
    desactualizado cuando se cambió la regla; se corrigió el 2026-08-22 al correr la suite completa.
    A los 5 días manda el escalón del 35%."""
    exp = AS_OF + timedelta(days=35)
    contract = _put(strike=80.0, expiration=exp, mid=2.00)
    positions.open_position(conn, "TST", contract, quantity=1, collateral=8_000.0, entry_date=AS_OF)
    # edad 5 días, ganancia 20% (mark 1.60) -> NO cierra
    assert _mark_open(conn, exp, 1.60, 100.0, AS_OF + timedelta(days=5))["closed"] is False
    # ganancia 30% (mark 1.40) -> tampoco: en el día 5 la vara es 35%
    assert _mark_open(conn, exp, 1.40, 100.0, AS_OF + timedelta(days=5))["closed"] is False
    # ganancia 35% (mark 1.30) -> cierra
    assert _mark_open(conn, exp, 1.30, 100.0, AS_OF + timedelta(days=5))["closed"] is True


def test_profit_week1_stays_below_18pct(conn):
    exp = AS_OF + timedelta(days=35)
    contract = _put(strike=80.0, expiration=exp, mid=2.00)
    positions.open_position(conn, "TST", contract, quantity=1, collateral=8_000.0, entry_date=AS_OF)
    # edad 5, ganancia 10% (mark 1.80) -> NO cierra
    assert _mark_open(conn, exp, 1.80, 100.0, AS_OF + timedelta(days=5))["closed"] is False


def test_profit_dia_10_pide_40pct(conn):
    """Día 7 en adelante la vara sube a 40% (escalón 3). Corregido el 2026-08-22: probaba el
    esquema viejo de semana2, que pedía 35%."""
    exp = AS_OF + timedelta(days=40)
    contract = _put(strike=80.0, expiration=exp, mid=2.00)
    positions.open_position(conn, "TST", contract, quantity=1, collateral=8_000.0, entry_date=AS_OF)
    # edad 10, ganancia 20% -> NO cierra
    assert _mark_open(conn, exp, 1.60, 100.0, AS_OF + timedelta(days=10))["closed"] is False
    # ganancia 35% (mark 1.30) -> tampoco: en el día 10 la vara es 40%
    assert _mark_open(conn, exp, 1.30, 100.0, AS_OF + timedelta(days=10))["closed"] is False
    # ganancia 40% (mark 1.20) -> cierra
    assert _mark_open(conn, exp, 1.20, 100.0, AS_OF + timedelta(days=10))["closed"] is True


def test_profit_near_exp_far_from_strike_needs_45pct(conn):
    """Faltando <20 días y LEJOS del strike: objetivo 45%. 35% NO cierra; 50% sí."""
    exp = AS_OF + timedelta(days=35)
    contract = _put(strike=80.0, expiration=exp, mid=2.00)
    positions.open_position(conn, "TST", contract, quantity=1, collateral=8_000.0, entry_date=AS_OF)
    # edad 20 (dte 15 <20), cobertura 20% (lejos), ganancia 35% -> NO cierra
    assert _mark_open(conn, exp, 1.30, 100.0, AS_OF + timedelta(days=20))["closed"] is False
    # ganancia 50% (mark 1.00) -> cierra
    assert _mark_open(conn, exp, 1.00, 100.0, AS_OF + timedelta(days=20))["closed"] is True


def test_cerca_del_strike_y_del_vto_igual_manda_el_escalon_por_antiguedad(conn):
    """Cerca del strike y del vencimiento NO hay excepción: manda el escalón por antigüedad.

    Antes existía una regla que cerraba con "cualquier ganancia" en esa situación; el 2026-08-11 se
    le puso un piso de 30% y el 2026-08-12 el esquema entero pasó a la escalera por antigüedad. Este
    test seguía pidiendo 30% en el día 20, donde la vara real es 40%. Corregido el 2026-08-22.
    Lo que sí sigue valiendo, y es lo importante: NUNCA cierra en pérdida por ganancia."""
    exp = AS_OF + timedelta(days=35)
    contract = _put(strike=80.0, expiration=exp, mid=2.00)
    positions.open_position(conn, "TST", contract, quantity=1, collateral=8_000.0, entry_date=AS_OF)
    # subyacente 83 -> cobertura (83-80)/83 = 3.6% <=6% (cerca del strike), edad 20 (dte 15)
    # en PÉRDIDA (mark 2.20 = -10%) -> NO cierra
    assert _mark_open(conn, exp, 2.20, 83.0, AS_OF + timedelta(days=20))["closed"] is False
    # ganancia chica 5% (mark 1.90) -> NO cierra
    assert _mark_open(conn, exp, 1.90, 83.0, AS_OF + timedelta(days=20))["closed"] is False
    # ganancia 30% (mark 1.40) -> tampoco: en el día 20 la vara es 40%
    assert _mark_open(conn, exp, 1.40, 83.0, AS_OF + timedelta(days=20))["closed"] is False
    # ganancia 40% (mark 1.20) -> cierra
    assert _mark_open(conn, exp, 1.20, 83.0, AS_OF + timedelta(days=20))["closed"] is True


def test_naked_margin_is_much_smaller_than_cash_secured():
    from options_advisor.simulator import rules
    # AAPL-like: subyacente 300, strike 280, prima 3 -> margen naked = max(0.2*300-20+3, 0.1*280+3)=43/acc
    assert rules.naked_put_margin(300.0, 280.0, 3.0) == 4300.0
    # En modo naked, 1 contrato compromete 4300 (no 28000 de cash-secured)
    s = _settings(margin_mode="naked", use_price_tier_sizing=False, max_position_pct=0.05)
    r = positions.size_position(strike=280.0, underlying_price=300.0, premium=3.0, cash_available=100_000.0, account_equity=100_000.0, settings=s)
    assert r is not None
    assert r.quantity == 1 and r.collateral == 4300.0


def test_el_cash_se_ajusta_con_un_delta_atomico_no_con_un_absoluto(conn):
    """Regresion (auditoria 2026-08-22): leer-calcular-escribir perdia movimientos.

    El patron viejo era SELECT cash -> calcular en Python -> UPDATE con un absoluto. Entre las dos
    puntas hay un commit implicito, asi que dos escritores se pisaban y ganaba el ultimo: si uno
    abria una posicion (reservando colateral) mientras el otro cerraba otra (devolviendolo), el
    colateral del primero se evaporaba del cash y ninguna reconciliacion lo detectaba.

    Este test simula ese entrelazado: se lee el cash ANTES de los dos movimientos y despues se
    aplican los dos. Con deltas el resultado es correcto; con absolutos calculados sobre esa
    lectura vieja, uno de los dos se perdia."""
    from options_advisor.storage import repository as repo

    repo.update_simulated_account_cash(conn, 10_000.0)
    leido_por_los_dos = repo.get_simulated_account(conn)["cash"]
    assert leido_por_los_dos == 10_000.0

    # Escritor A reserva 2.500 de colateral. Escritor B devuelve 1.200 de un cierre.
    repo.ajustar_cash_simulado(conn, -2_500.0)
    repo.ajustar_cash_simulado(conn, +1_200.0)

    final = repo.get_simulated_account(conn)["cash"]
    assert final == 8_700.0, "los dos movimientos tienen que quedar aplicados"
    # Con el patron viejo, B habria escrito 10.000 + 1.200 = 11.200 pisando a A.
    assert final != 11_200.0, "el colateral reservado no puede evaporarse"
