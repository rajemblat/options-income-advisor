from __future__ import annotations

from datetime import date, datetime, timedelta

from options_advisor.broker.models import Greeks, IntradayBar, OptionChain, OptionContract
from options_advisor.config import IntradayCondorSettings
from options_advisor.simulator import iron_condor

SPOT = 7600.0
EXP = date(2026, 8, 5)


def _settings(**over) -> IntradayCondorSettings:
    d = dict(short_delta_max=0.15, wing_width=10.0, profit_target_pct=0.60, stop_loss_dollars=100.0,
             calm_range_pct=0.004, entry_window_start="10:00", entry_window_end="14:00", max_collateral=1000.0)
    d.update(over)
    return IntradayCondorSettings(**d)


def _bars(closes: list[float], hour: int = 10, minute: int = 30) -> list[IntradayBar]:
    start = datetime(2026, 8, 5, hour, minute)
    return [
        IntradayBar(symbol="SPX", timestamp=start + timedelta(minutes=i), open=closes[0], high=max(closes[: i + 1]),
                    low=min(closes[: i + 1]), close=c, volume=100)
        for i, c in enumerate(closes)
    ]


def _opt(otype: str, strike: float, mid: float, delta: float) -> OptionContract:
    return OptionContract(
        symbol="SPX", option_type=otype, strike=strike, expiration=EXP,
        bid=round(mid - 0.05, 2), ask=round(mid + 0.05, 2), last_price=mid,
        implied_volatility=0.12, open_interest=1000, volume=500,
        greeks=Greeks(delta=delta, gamma=0.01, theta=-0.5, vega=0.1, rho=0.01, source="broker"),
    )


def _chain() -> OptionChain:
    contracts = [
        # puts (delta negativo; más cerca del spot = más delta)
        _opt("put", 7540, 2.0, -0.10),
        _opt("put", 7550, 3.0, -0.14),   # mejor prima con delta<=0.15 -> short put
        _opt("put", 7560, 4.0, -0.20),
        _opt("put", 7570, 5.0, -0.28),
        # calls (delta positivo; más cerca del spot = más delta)
        _opt("call", 7630, 5.0, 0.28),
        _opt("call", 7640, 4.0, 0.20),
        _opt("call", 7650, 3.0, 0.14),   # mejor prima con delta<=0.15 -> short call
        _opt("call", 7660, 2.0, 0.10),
    ]
    return OptionChain(symbol="SPX", as_of=EXP, underlying_price=SPOT, contracts=contracts)


# ---------------- Señal (día calmo + ventana) ----------------

def test_signal_calm_and_in_window():
    sig = iron_condor.evaluate_condor_signal(_bars([7600, 7601, 7599, 7600], hour=10, minute=30), _settings())
    assert sig.calm is True and sig.in_window is True


def test_signal_not_calm_when_big_range():
    # rango 7600->7660 = 0.79% > 0.4%
    sig = iron_condor.evaluate_condor_signal(_bars([7600, 7660, 7600], hour=10, minute=30), _settings())
    assert sig.calm is False


def test_signal_out_of_window_early():
    sig = iron_condor.evaluate_condor_signal(_bars([7600, 7601], hour=9, minute=45), _settings())
    assert sig.in_window is False


def test_vix_filter_looks_at_absolute_movement_not_direction():
    """Usuario 2026-08-14: "el vix no tiene que estar bajando, ni subiendo … sino que día lateral
    estable". El tope es de MOVIMIENTO: ±4% pasa, y tanto un salto de +9% como un derrumbe de −9%
    quedan afuera. Un VIX que se desploma no es un día tranquilo: suele ser un rally fuerte, y al
    condor lo mata que el SPX se mueva, para el lado que sea."""
    barras = _bars([7600, 7601, 7599, 7600], hour=10, minute=30)
    cfg = _settings(max_vix_change_pct=4.0)

    def vix_ok(chg):
        return iron_condor.evaluate_condor_signal(barras, cfg, vix_change_pct=chg).vix_ok

    assert vix_ok(1.5) is True and vix_ok(-1.5) is True, "un día lateral entra para los dos lados"
    assert vix_ok(4.0) is True and vix_ok(-4.0) is True, "justo en el tope todavía entra"
    assert vix_ok(9.0) is False, "VIX disparado = día movido"
    assert vix_ok(-9.0) is False, "VIX derrumbándose también es un día movido, no uno lateral"


def test_without_a_vix_ceiling_configured_the_filter_does_not_block():
    """Sin tope configurado (o sin dato de VIX) el condor sigue operando como siempre: el filtro
    nunca puede frenar por falta de información."""
    barras = _bars([7600, 7601, 7599, 7600], hour=10, minute=30)
    assert iron_condor.evaluate_condor_signal(barras, _settings(), vix_change_pct=-30.0).vix_ok is True
    assert iron_condor.evaluate_condor_signal(barras, _settings(max_vix_change_pct=4.0)).vix_ok is True


def test_la_ventana_se_lee_en_hora_de_nueva_york():
    """CANDADO (cambiado 2026-08-27). La ventana del settings es HORA DE MERCADO, no UTC.

    Antes se comparaba contra la hora UTC de la barra tal cual viene de Schwab. Con "10:00-14:00"
    eso daba 06:00-10:00 ET y, como el mercado abre 09:30, la ventana efectiva eran los primeros
    30 minutos de la rueda. El usuario lo pidio explicito: "no quiero que solo opere la primera
    ventana... maximo hasta las 2 pm, horario de mercado".

    Este test tambien es el que desactiva la bomba del 2026-11-02: con la comparacion en hora del
    Este, el cambio de horario de verano se resuelve solo."""
    from datetime import timezone

    def _utc(h, m):
        start = datetime(2026, 8, 5, h, m, tzinfo=timezone.utc)
        return [
            IntradayBar(symbol="SPX", timestamp=start + timedelta(minutes=i), open=7600.0, high=7601.0,
                        low=7599.0, close=7600.0, volume=100)
            for i in range(3)
        ]

    cfg = _settings(entry_window_start="09:30", entry_window_end="14:00")

    # OJO: `_utc(h, m)` arma TRES barras (m, m+1, m+2) y la senal mira la ULTIMA. Los comentarios
    # de abajo son la hora de esa ultima barra, no la del argumento. (Este mismo detalle hizo fallar
    # la primera version del test: _utc(17,59) termina en 14:01 ET, un minuto FUERA de la ventana.)
    # VERANO (UTC-4)
    assert iron_condor.evaluate_condor_signal(_utc(13, 31), cfg).in_window is True    # 09:33 ET
    assert iron_condor.evaluate_condor_signal(_utc(16, 0), cfg).in_window is True     # 12:02 ET
    assert iron_condor.evaluate_condor_signal(_utc(17, 55), cfg).in_window is True    # 13:57 ET
    assert iron_condor.evaluate_condor_signal(_utc(17, 59), cfg).in_window is False   # 14:01 ET
    assert iron_condor.evaluate_condor_signal(_utc(18, 1), cfg).in_window is False    # 14:03 ET
    assert iron_condor.evaluate_condor_signal(_utc(13, 0), cfg).in_window is False    # 09:02 ET

    # EL MEDIODIA QUE ANTES QUEDABA AFUERA. Con la comparacion vieja en UTC, una barra de las
    # 16:02 UTC daba in_window=False y el condor no podia abrir al mediodia (12:02 ET) aunque el
    # dia siguiera calmo. Es exactamente lo que el usuario pidio destrabar.
    assert iron_condor.evaluate_condor_signal(_utc(16, 0), cfg).in_window is True


def test_el_cambio_de_horario_de_noviembre_ya_no_apaga_el_condor():
    """En INVIERNO (UTC-5) el mercado abre 14:30 UTC. Con la ventana vieja en UTC, que terminaba a
    las 14:00, la apertura misma ya caia afuera: el condor dejaba de abrir para siempre desde el
    2026-11-02, en silencio y sin error. Ahora entra."""
    from datetime import timezone

    def _utc_invierno(h, m):
        start = datetime(2026, 11, 3, h, m, tzinfo=timezone.utc)   # martes, ya en horario estandar
        return [
            IntradayBar(symbol="SPX", timestamp=start + timedelta(minutes=i), open=7600.0, high=7601.0,
                        low=7599.0, close=7600.0, volume=100)
            for i in range(3)
        ]

    cfg = _settings(entry_window_start="09:30", entry_window_end="14:00")
    assert iron_condor.evaluate_condor_signal(_utc_invierno(14, 30), cfg).in_window is True   # 09:30 ET
    assert iron_condor.evaluate_condor_signal(_utc_invierno(18, 0), cfg).in_window is True    # 13:00 ET
    assert iron_condor.evaluate_condor_signal(_utc_invierno(19, 1), cfg).in_window is False   # 14:01 ET


def test_una_barra_sin_huso_se_toma_como_hora_de_mercado():
    """Fixtures y tests arman barras sin huso; inventarles UTC las correria 4 horas."""
    cfg = _settings(entry_window_start="09:30", entry_window_end="14:00")
    assert iron_condor.evaluate_condor_signal(_bars([7600, 7601, 7599], hour=10, minute=30), cfg).in_window is True
    assert iron_condor.evaluate_condor_signal(_bars([7600, 7601, 7599], hour=15, minute=0), cfg).in_window is False


# ---------------- Armado ----------------

def test_build_iron_condor_picks_best_paying_delta_and_wings():
    build = iron_condor.build_iron_condor(_chain(), SPOT, _settings())
    assert build is not None
    assert build.short_put_strike == 7550 and build.short_call_strike == 7650   # mejor prima con delta<=0.15
    assert build.long_put_strike == 7540 and build.long_call_strike == 7660     # alas de 10 pts
    # El crédito es el REALIZABLE, no el del mid (usuario 2026-09-02: "entro a la prima que me da
    # pero en limit, no espero el mid, porque el SPX maneja muy poca spread"). Se venden los cortos
    # al BID y se compran las alas al ASK: (2.9+2.9) - (2.1+2.1) = 1.8 -> $180. Al mid daban $200,
    # pero ese precio no lo paga nadie: el papel que lo usaba mostraba ganancias que no existían.
    assert build.net_credit == 180.0
    assert build.max_loss == 820.0        # 10*100 - 180
    assert len(build.legs) == 4
    # breakevens = short strikes ± crédito_ps (1.8)
    assert build.lower_breakeven == 7548.2 and build.upper_breakeven == 7651.8


def test_build_returns_none_when_max_loss_exceeds_cap():
    build = iron_condor.build_iron_condor(_chain(), SPOT, _settings(max_collateral=100.0))
    assert build is None   # pérdida máx 820 > 100


def test_build_returns_none_when_no_delta_candidates():
    # todos los deltas altos -> no hay strikes a delta<=0.15
    s = _settings(short_delta_max=0.05)
    assert iron_condor.build_iron_condor(_chain(), SPOT, s) is None


# ---------------- Marcado / salida ----------------

def test_close_value_and_unrealized():
    chain = OptionChain(symbol="SPX", as_of=EXP, underlying_price=SPOT, contracts=[
        _opt("put", 7550, 1.2, -0.10), _opt("call", 7650, 1.2, 0.10),
        _opt("put", 7540, 0.8, -0.06), _opt("call", 7660, 0.8, 0.06),
    ])
    cv = iron_condor.condor_close_value(chain, 7550, 7650, 7540, 7660)
    assert cv == round(((1.2 + 1.2) - (0.8 + 0.8)) * 100, 2)  # = 80.0
    assert iron_condor.condor_unrealized(200.0, cv) == 120.0


def test_should_close_profit_target_60pct_and_stop_100():
    s = _settings()
    # crédito $200; 60% = $120
    assert iron_condor.should_close_condor(120.0, 200.0, False, s) == (True, "profit_target")
    assert iron_condor.should_close_condor(119.0, 200.0, False, s) == (False, None)
    assert iron_condor.should_close_condor(-100.0, 200.0, False, s) == (True, "stop_loss")
    assert iron_condor.should_close_condor(-99.0, 200.0, False, s) == (False, None)
    assert iron_condor.should_close_condor(0.0, 200.0, True, s)[0] is True   # vencimiento


def test_should_close_early_window_tiered():
    # Usuario 2026-08-07: 40% en los primeros 20 min (para reentrar); 50% después.
    s = _settings(profit_target_pct=0.50, profit_target_early_pct=0.40, early_window_minutes=20.0)
    credit = 200.0  # 40% = $80, 50% = $100
    # Temprano (age 10 min): cierra a +$80 (40%), no necesita esperar al 50%.
    assert iron_condor.should_close_condor(80.0, credit, False, s, age_minutes=10.0) == (True, "profit_target")
    # Temprano pero solo +$70 (35%): todavía no.
    assert iron_condor.should_close_condor(70.0, credit, False, s, age_minutes=10.0) == (False, None)
    # Pasada la ventana (age 40 min): +$80 (40%) NO alcanza, necesita $100 (50%).
    assert iron_condor.should_close_condor(80.0, credit, False, s, age_minutes=40.0) == (False, None)
    assert iron_condor.should_close_condor(100.0, credit, False, s, age_minutes=40.0) == (True, "profit_target")
    # Sin edad (None) usa el objetivo tardío (50%).
    assert iron_condor.should_close_condor(80.0, credit, False, s, age_minutes=None) == (False, None)


def test_intrinsic_close_value_at_expiration():
    # spot dentro del rango (entre short put y short call) -> todas las patas 0 -> cierre 0 (ganancia total)
    assert iron_condor.condor_intrinsic_close_value(7600.0, 7550, 7650, 7540, 7660) == 0.0
    # spot muy arriba -> short call ITM 60, long call ITM 50 -> (60-50)*100 = 1000 de costo
    assert iron_condor.condor_intrinsic_close_value(7710.0, 7550, 7650, 7540, 7660) == 1000.0
