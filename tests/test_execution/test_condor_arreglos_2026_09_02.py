"""Los cuatro agujeros del Iron Condor real que se vieron el 2026-09-02, con dinero de verdad.

Ese dia el robot mando DOS condors de SPX y los dos salieron mal, cada uno por un motivo distinto:

  1. 09:31:53 — mando el condor 7595/7670 a $1.82 de credito neto. Schwab lo RECHAZO:
     "Spread orders for SPX options must be priced in 5-cent increments". El robot dio la fila por
     no llenada y la borro a las 09:32:22. La posicion quedo VIVA igual en la cuenta: sin stop, sin
     objetivo, sin nadie mirandola. La cerro el usuario a mano a las 10:28, pagando $4.80 de debito.

  2. 09:30:51 — el condor 7580/7685 quedo PUESTO al mid y no lleno hasta las 10:08:11. Treinta y
     ocho minutos. Cuando lleno, el mercado ya no era el que habia justificado esa entrada.

  3. Ese mismo condor entro cobrando $95 contra $905 de riesgo maximo: 1 a 9.5, cuando los que el
     robot venia armando pagaban entre $165 y $195. `min_credit` estaba en 0. Usuario: "tampoco
     puede abrir con esa prima de .95".

  4. A las 10:22:30 cerro por stop loss con una perdida REALIZADA de $125, con el stop puesto en
     $100. Usuario: "deje que el stop loss es de 100 maximo 110, y cerro asi la perdida de hoy".
     No fue un error de contabilidad —el credito de $95 y el debito de $220 estaban bien anotados—:
     el robot MEDIA la posicion al mid y SALIA al precio de verdad.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from options_advisor.execution import live_condor_engine as lce
from options_advisor.execution import schwab_orders as so
from options_advisor.simulator import iron_condor
from options_advisor.storage import repository as repo

SP, LP = "SPXW  260902P07595000", "SPXW  260902P07585000"
SC, LC = "SPXW  260902C07670000", "SPXW  260902C07680000"


@pytest.fixture
def conn():
    from options_advisor.storage import db
    return db.connect(":memory:")


@pytest.fixture
def mails(monkeypatch):
    enviados = []
    from options_advisor.alerts import notifier
    monkeypatch.setattr(notifier, "send_email_robot_real",
                        lambda a, c: enviados.append((a, c)) or True)
    return enviados


class _Contrato:
    def __init__(self, otype, strike, bid, ask):
        self.option_type, self.strike, self.bid, self.ask = otype, strike, bid, ask

    @property
    def mid_price(self):
        return round((self.bid + self.ask) / 2, 4)


class _Cadena:
    def __init__(self, contratos):
        self.contracts = contratos


# ══════════════════════════════════════════════════════════════════════════════════════════
# 1. La grilla de 5 centavos de SPX
# ══════════════════════════════════════════════════════════════════════════════════════════

def test_el_credito_de_apertura_baja_a_la_grilla_de_5_centavos():
    """El $1.82 que Schwab rechazo tiene que salir como $1.80: hacia ABAJO, nunca a favor nuestro."""
    orden = so.build_iron_condor_open(SP, LP, SC, LC, quantity=1, net_credit_limit=1.82)
    assert orden["price"] == "1.80"


def test_el_debito_de_cierre_sube_a_la_grilla_de_5_centavos():
    """Al SALIR se redondea hacia ARRIBA: pagar 2 centavos de mas es infinitamente mas barato que
    una recompra rechazada con la posicion todavia abierta."""
    orden = so.build_iron_condor_close(SP, LP, SC, LC, quantity=1, net_debit_limit=2.13)
    assert orden["price"] == "2.15"


@pytest.mark.parametrize("precio", [0.95, 1.80, 1.82, 1.825, 2.13, 2.20, 4.87, 11.03])
def test_ningun_precio_de_condor_sale_fuera_de_la_grilla(precio):
    """La propiedad que importa: pase lo que pase, el precio que sale a Schwab es multiplo de 5
    centavos. Es el unico lugar por el que pasan TODAS las ordenes, reemplazos incluidos."""
    for orden in (so.build_iron_condor_open(SP, LP, SC, LC, quantity=1, net_credit_limit=precio),
                  so.build_iron_condor_close(SP, LP, SC, LC, quantity=1, net_debit_limit=precio)):
        centavos = round(float(orden["price"]) * 100)
        assert centavos % 5 == 0, f"{orden['price']} no cae en la grilla"


def test_un_credito_que_redondea_a_cero_no_sale():
    """$0.03 de credito redondea a $0.00. Eso no es una orden, es un error: tiene que morir aca."""
    with pytest.raises(ValueError):
        so.build_iron_condor_open(SP, LP, SC, LC, quantity=1, net_credit_limit=0.03)


def test_las_escaleras_del_motor_ya_vienen_en_la_grilla():
    """La escalera camina de 5 en 5 pero el ULTIMO peldano se clava en el mid exacto, que casi nunca
    cae en la grilla: de ahi salio el $1.82. Ahora el precio que el robot GUARDA es el que manda."""
    sp = _Contrato("put", 7595, 1.71, 1.79)
    sc = _Contrato("call", 7670, 2.12, 2.23)
    lp = _Contrato("put", 7585, 1.11, 1.19)
    lc = _Contrato("call", 7680, 1.53, 1.61)
    for escalera in (lce._open_credit_ladder(sp, sc, lp, lc),
                     lce._close_debit_ladder(sp, sc, lp, lc)):
        assert escalera
        for precio in escalera:
            assert round(precio * 100) % 5 == 0, f"{precio} no cae en la grilla"


# ══════════════════════════════════════════════════════════════════════════════════════════
# 2. El stop se mide al precio REAL de salida
# ══════════════════════════════════════════════════════════════════════════════════════════

def test_el_valor_de_salida_usa_ask_de_los_cortos_y_bid_de_las_alas():
    """Salir YA cuesta: recomprar los cortos al ASK y vender las alas al BID. Siempre peor que el mid."""
    cadena = _Cadena([
        _Contrato("put", 7595, 0.30, 0.44), _Contrato("put", 7585, 0.20, 0.34),
        _Contrato("call", 7670, 4.10, 4.44), _Contrato("call", 7680, 2.05, 2.29),
    ])
    salida = iron_condor.condor_exit_value(cadena, 7595, 7670, 7585, 7680)
    mid = iron_condor.condor_close_value(cadena, 7595, 7670, 7585, 7680)
    # (0.44 + 4.44) - (0.20 + 2.05) = 2.63
    assert salida == pytest.approx(263.0)
    assert salida > mid, "salir de verdad SIEMPRE cuesta mas que el mid"


def test_sin_puntas_utiles_no_se_inventa_un_precio_de_salida():
    """Un ask en 0 es un hueco de datos, no un regalo. Sin precio real no se devuelve ninguno."""
    cadena = _Cadena([
        _Contrato("put", 7595, 0.30, 0.0), _Contrato("put", 7585, 0.20, 0.34),
        _Contrato("call", 7670, 4.10, 4.44), _Contrato("call", 7680, 2.05, 2.29),
    ])
    assert iron_condor.condor_exit_value(cadena, 7595, 7670, 7585, 7680) is None


def test_el_stop_dispara_con_la_perdida_de_salida_no_con_la_del_mid():
    """EL test del dia. Al mid la posicion marca -$92: el stop de $100 no dispararia. Pero salir de
    verdad cuesta -$118. Si esperamos a que el MID diga -$100, cuando la orden llene la perdida ya
    va a ser de $125 — que es exactamente lo que paso."""
    from options_advisor.config import load_settings
    cfg = load_settings().intraday_condor.model_copy(update={"stop_loss_dollars": 100.0})

    cerrar_mid, _ = iron_condor.should_close_condor(-92.0, 95.0, False, cfg, age_minutes=14.0)
    assert cerrar_mid is False, "al mid todavia no llega al stop"

    cerrar, motivo = iron_condor.should_close_condor(-92.0, 95.0, False, cfg, age_minutes=14.0,
                                                    unrealized_de_salida=-118.0)
    assert (cerrar, motivo) == (True, "stop_loss")


def test_la_ganancia_TAMBIEN_se_mide_al_precio_de_salida():
    """CAMBIADO el 2026-09-07. Antes este test fijaba lo contrario: que la ganancia se midiera al
    mid, con el argumento de que "para cobrar no hay apuro, la orden queda puesta y espera".

    Ese argumento era falso. `_close_debit_ladder` devuelve UN solo peldaño —el precio ejecutable—,
    así que la orden de cierre sale y llena al instante: no espera nada. El 2026-09-04, con dinero
    real, el condor cobró $175, el objetivo disparó con el MID marcando +$35, y salir de verdad
    costó $150: +$25 cobrados. Usuario: "el objetivo dijo 35 y cobré 25".

    Ahora las dos salidas miran el mismo precio, el de salida. Al mid esto marcaría +$70 sobre $190
    (36%, por encima del 35%) y cerraría; medido a lo que de verdad se cobra son +$10, así que
    espera. Cuando diga 35%, van a ser 35% de lo que entra en la cuenta."""
    from options_advisor.config import load_settings
    cfg = load_settings().intraday_condor.model_copy(
        update={"profit_target_pct": 0.35, "profit_target_early_pct": 0.20,
                "early_window_minutes": 30.0, "stop_loss_dollars": 100.0})
    cerrar, motivo = iron_condor.should_close_condor(70.0, 190.0, False, cfg, age_minutes=60.0,
                                                    unrealized_de_salida=10.0)
    assert (cerrar, motivo) == (False, None), "Cobró al mid otra vez: son 25 en vez de 35"

    # Con la salida real ya en el objetivo, sí cierra.
    cerrar, motivo = iron_condor.should_close_condor(90.0, 190.0, False, cfg, age_minutes=60.0,
                                                    unrealized_de_salida=67.0)   # 67 >= 0.35*190
    assert (cerrar, motivo) == (True, "profit_target")


def test_el_caso_real_del_04_09_ahora_espera():
    """El condor de ese día, con los números exactos: crédito $175, objetivo temprano del 20%.
    Al mid marcaba +$35 (justo el 20%) pero salir costaba $150, o sea +$25 reales."""
    from options_advisor.config import load_settings
    cfg = load_settings().intraday_condor.model_copy(
        update={"profit_target_early_pct": 0.20, "early_window_minutes": 30.0,
                "stop_loss_dollars": 100.0})
    cerrar, _ = iron_condor.should_close_condor(35.0, 175.0, False, cfg, age_minutes=12.0,
                                               unrealized_de_salida=25.0)
    assert cerrar is False, "Volvió a cerrar cobrando $25 cuando el objetivo pedía $35"


def test_sin_medicion_estricta_el_stop_se_comporta_como_siempre():
    """El papel no tiene puntas de cadena viva en todos los casos. Sin el dato, misma regla de antes:
    ningun test viejo cambia de significado por este arreglo."""
    from options_advisor.config import load_settings
    cfg = load_settings().intraday_condor.model_copy(update={"stop_loss_dollars": 100.0})
    assert iron_condor.should_close_condor(-105.0, 95.0, False, cfg) == (True, "stop_loss")
    assert iron_condor.should_close_condor(-99.0, 95.0, False, cfg) == (False, None)


# ══════════════════════════════════════════════════════════════════════════════════════════
# 3. Una apertura no se queda colgada 38 minutos
# ══════════════════════════════════════════════════════════════════════════════════════════

def _fila_puesta(conn):
    pid = repo.insert_real_condor_position(
        conn, underlying="$SPX", entry_date=date(2026, 9, 2), expiration_date=date(2026, 9, 2),
        short_put_strike=7595, short_call_strike=7670, long_put_strike=7585, long_call_strike=7680,
        short_put_symbol=SP, long_put_symbol=LP, short_call_symbol=SC, long_call_symbol=LC,
        quantity=1, entry_net_credit=180.0, max_loss=820.0, max_profit=180.0,
        lower_breakeven=7593.2, upper_breakeven=7671.8, entry_spot=7638.0,
        open_schwab_order_id="ORD-1", status="working")
    return conn.execute("SELECT * FROM real_condor_positions WHERE id=?", (pid,)).fetchone()


class _BrokerPuesta:
    def __init__(self, minutos_puesta, estado="WORKING"):
        entrada = datetime.now(timezone.utc) - timedelta(minutes=minutos_puesta)
        self._entrada = entrada.strftime("%Y-%m-%dT%H:%M:%S+0000")   # el formato de Schwab, sin ":"
        self._estado = estado
        self.cancelados = []

    def get_order(self, account_hash, order_id):
        return {"status": self._estado, "enteredTime": self._entrada}

    def cancel_order(self, account_hash, order_id):
        self.cancelados.append(order_id)

    def get_recent_filled_orders(self, desde):
        return []


def _cfg_con_tope(minutos):
    from options_advisor.config import load_settings
    return load_settings().intraday_condor.model_copy(update={"open_working_max_minutes": minutos})


def test_una_apertura_de_38_minutos_se_cancela(conn, mails):
    """09:30 -> 10:08. Cuando llego a llenar, los precios que la habian justificado ya no existian."""
    fila = _fila_puesta(conn)
    broker = _BrokerPuesta(minutos_puesta=38)
    lce._reconcile_working_open(conn, broker, "HASH", fila, _cfg_con_tope(5.0))
    assert broker.cancelados == ["ORD-1"]
    assert any("cancel" in a.lower() for a, _ in mails)


def test_una_apertura_reciente_se_deja_trabajar(conn, mails):
    """Dos minutos es una orden negociandose, no una decision vieja. No se toca."""
    fila = _fila_puesta(conn)
    broker = _BrokerPuesta(minutos_puesta=2)
    lce._reconcile_working_open(conn, broker, "HASH", fila, _cfg_con_tope(5.0))
    assert broker.cancelados == []


def test_la_apertura_vieja_no_se_cierra_en_la_base_al_cancelarla(conn, mails):
    """Se cancela la ORDEN, no se cierra la FILA. El proximo tick va a leer CANCELED y va a pasar por
    la verificacion contra Schwab antes de descartar nada — que es la unica ruta que sabe distinguir
    'no llenO' de 'llenO justo mientras salia el cancel'."""
    fila = _fila_puesta(conn)
    lce._reconcile_working_open(conn, _BrokerPuesta(minutos_puesta=38), "HASH", fila, _cfg_con_tope(5.0))
    despues = conn.execute("SELECT status FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    assert despues["status"] == "working"


def test_sin_hora_de_entrada_no_se_cancela_nada(conn, mails):
    """Si Schwab no dice cuando recibio la orden, no se adivina: cancelar a ciegas es peor."""
    fila = _fila_puesta(conn)

    class _SinHora(_BrokerPuesta):
        def get_order(self, account_hash, order_id):
            return {"status": "WORKING"}

    broker = _SinHora(minutos_puesta=38)
    lce._reconcile_working_open(conn, broker, "HASH", fila, _cfg_con_tope(5.0))
    assert broker.cancelados == []


def test_una_apertura_que_ya_lleno_no_se_cancela(conn, mails):
    """Llego tarde pero llego: gana el fill, no el reloj."""
    fila = _fila_puesta(conn)
    broker = _BrokerPuesta(minutos_puesta=38, estado="FILLED")
    lce._reconcile_working_open(conn, broker, "HASH", fila, _cfg_con_tope(5.0))
    assert broker.cancelados == []
    despues = conn.execute("SELECT status FROM real_condor_positions WHERE id=?", (fila["id"],)).fetchone()
    assert despues["status"] == "open"


# ══════════════════════════════════════════════════════════════════════════════════════════
# 4. El piso de credito que el aprendizaje no puede bajar
# ══════════════════════════════════════════════════════════════════════════════════════════

def test_el_piso_de_credito_no_lo_puede_bajar_el_aprendizaje():
    """`min_credit` es una perilla del aprendizaje y puede llegar a 0 sola. `live_min_credit` es del
    usuario y vive aparte, justamente para que eso no pueda pasar con dinero real."""
    from options_advisor.config import load_settings
    from options_advisor.simulator import learning
    cfg = load_settings().intraday_condor
    assert cfg.live_min_credit >= 150.0
    campos_que_mueve = {campo for (campo, *_) in learning._CD_BOUNDS.values()}
    assert "min_credit" in campos_que_mueve, "min_credit SI es una perilla del aprendizaje"
    assert "live_min_credit" not in campos_que_mueve, "el piso del real NO lo toca el aprendizaje"


def test_el_aprendizaje_no_puede_borrar_el_piso_del_condor_real():
    """`effective_condor` aplica lo aprendido encima del config. Aunque el aprendizaje ponga
    min_credit en 0, el piso duro del real tiene que seguir en pie."""
    from options_advisor.config import load_settings
    from options_advisor.simulator import learning
    from options_advisor.storage import db
    c = db.connect(":memory:")
    base = load_settings().intraday_condor
    c.execute("INSERT OR REPLACE INTO learning_state (key, value) VALUES (?, ?)",
              (learning._CD_CREDIT_KEY, 0.0))
    efectivo = learning.effective_condor(c, base)
    assert efectivo.live_min_credit == base.live_min_credit >= 150.0


def test_el_config_no_permite_otro_condor_de_95_centavos():
    """El condor del 2026-09-02 cobro $95 contra $905 de riesgo. Con el config de hoy no pasa: ni por
    `min_credit` (papel y real) ni por el piso duro del real."""
    from options_advisor.config import load_settings
    cfg = load_settings().intraday_condor
    assert cfg.min_credit >= 150.0
    assert 95.0 < cfg.live_min_credit


# ══════════════════════════════════════════════════════════════════════════════════════════
# 5. La ultima red: posiciones de SPX que el robot no conoce
# ══════════════════════════════════════════════════════════════════════════════════════════

class _Posicion:
    def __init__(self, symbol, quantity=-1.0, asset_type="OPTION", underlying="SPXW", pnl=-298.0):
        self.symbol, self.quantity, self.asset_type = symbol, quantity, asset_type
        self.underlying_symbol, self.unrealized_pnl = underlying, pnl


class _BrokerPosiciones:
    def __init__(self, posiciones):
        self._posiciones = posiciones

    def get_all_positions(self):
        return self._posiciones


def _fila_cerrada_hoy(conn, status="closed"):
    """Un condor que el robot armó HOY y anotó como cerrado — el escenario del 02/09."""
    pid = repo.insert_real_condor_position(
        conn, underlying="$SPX", entry_date=date(2026, 9, 2), expiration_date=date(2026, 9, 2),
        short_put_strike=7595, short_call_strike=7670, long_put_strike=7585, long_call_strike=7680,
        short_put_symbol=SP, long_put_symbol=LP, short_call_symbol=SC, long_call_symbol=LC,
        quantity=1, entry_net_credit=182.5, max_loss=817.5, max_profit=182.5,
        lower_breakeven=7593.2, upper_breakeven=7671.8, entry_spot=7638.0,
        open_schwab_order_id="ORD-X", status=status)
    if status == "closed":
        repo.close_real_condor_position(conn, pid, date(2026, 9, 2), close_value=None,
                                        close_reason="apertura_no_llenó", realized_pnl=None)
    return pid


def test_avisa_cuando_un_condor_que_dio_por_cerrado_sigue_vivo(conn, mails):
    """El agujero del 2026-09-02: Schwab dijo REJECTED, el robot cerró la fila, y la posición siguió
    viva casi una hora sin stop."""
    _fila_cerrada_hoy(conn)
    n = lce._barrer_posiciones_huerfanas(
        conn, _BrokerPosiciones([_Posicion(SC)]), date(2026, 9, 2))
    assert n == 1
    assert mails, "tiene que avisar: esa posición no tiene stop"
    asunto, cuerpo = mails[0]
    assert SC in cuerpo


def test_NO_avisa_por_las_posiciones_que_abrio_el_usuario(conn, mails):
    """Falso positivo real del 2026-09-08: el robot mandó un mail por ocho patas de SPXW que eran
    spreads que el usuario había abierto a mano el 24/08, el 27/08 y el 02/09 y que vencían ese día.
    Encima el freno le impedía abrir su propio condor. Usuario: "las posiciones que no las hace el
    robot no debe preocuparse, las trabajo yo".

    El robot solo mira lo SUYO: si nunca armó un condor con esas patas, no es asunto suyo."""
    _fila_cerrada_hoy(conn)
    del_usuario = ["SPXW  260908C07765000", "SPXW  260908C07755000",
                   "SPXW  260908P07450000", "SPXW  260908P07460000",
                   "SPXW  260908C07840000", "SPXW  260908C07830000",
                   "SPXW  260908P07470000", "SPXW  260908P07480000"]
    n = lce._barrer_posiciones_huerfanas(
        conn, _BrokerPosiciones([_Posicion(s) for s in del_usuario]), date(2026, 9, 2))
    assert n == 0
    assert mails == []


def test_no_avisa_por_un_condor_que_el_robot_SI_esta_gestionando(conn, mails):
    """Una posición abierta y bajo gestión no es un zombi: tiene stop y objetivo."""
    _fila_cerrada_hoy(conn, status="open")
    n = lce._barrer_posiciones_huerfanas(
        conn, _BrokerPosiciones([_Posicion(s) for s in (SP, LP, SC, LC)]), date(2026, 9, 2))
    assert n == 0
    assert mails == []


def test_sin_condors_cerrados_hoy_ni_se_consulta_al_broker(conn, mails):
    """Si el robot no cerró nada hoy no hay con qué desincronizarse, y no gasta una llamada."""
    class _Explota:
        def get_all_positions(self):
            raise AssertionError("no tendría que preguntar")

    assert lce._barrer_posiciones_huerfanas(conn, _Explota(), date(2026, 9, 2)) == 0


def test_no_avisa_dos_veces_el_mismo_dia(conn, mails):
    """La posición puede seguir ahí horas. Un mail por minuto no es una alerta, es ruido — pero el
    FRENO se mantiene en cada tick."""
    _fila_cerrada_hoy(conn)
    broker = _BrokerPosiciones([_Posicion(SC)])
    ns = [lce._barrer_posiciones_huerfanas(conn, broker, date(2026, 9, 2)) for _ in range(4)]
    assert ns == [1, 1, 1, 1], "el freno se evalúa siempre"
    assert len(mails) == 1, "el mail sale una sola vez"


def test_no_avisa_por_los_naked_de_acciones(conn, mails):
    """Los naked put del robot son de acciones y los lleva otro registro."""
    _fila_cerrada_hoy(conn)
    assert lce._barrer_posiciones_huerfanas(
        conn, _BrokerPosiciones([_Posicion("UAL   261016P00092500", underlying="UAL")]),
        date(2026, 9, 2)) == 0
    assert mails == []


# ══════════════════════════════════════════════════════════════════════════════════════════
# 6. El piso, enchufado de verdad al motor que manda la orden
# ══════════════════════════════════════════════════════════════════════════════════════════

class _ContratoOCC(_Contrato):
    def __init__(self, otype, strike, bid, ask, occ, expiration=date(2026, 9, 2)):
        super().__init__(otype, strike, bid, ask)
        self.occ_symbol, self.expiration = occ, expiration


class _Build:
    """Lo minimo que `_open_real_condor` le pide a un condor armado."""

    def __init__(self, patas, net_credit):
        sp, lp, sc, lc = patas
        self.legs = [("sell", "put", sp), ("buy", "put", lp),
                     ("sell", "call", sc), ("buy", "call", lc)]
        self.short_put_strike, self.long_put_strike = sp.strike, lp.strike
        self.short_call_strike, self.long_call_strike = sc.strike, lc.strike
        self.net_credit = net_credit
        self.max_loss, self.max_profit = 1000.0 - net_credit, net_credit
        self.lower_breakeven, self.upper_breakeven = 7593.0, 7672.0


class _BrokerQueAnota:
    def __init__(self):
        self.enviadas = []

    def place_order(self, account_hash, payload):
        self.enviadas.append(payload)
        return "ORD-NUEVA"

    def get_order(self, account_hash, order_id):
        return {"status": "WORKING"}


def _escena(credito_mid):
    """Una cadena donde el mid del condor da exactamente `credito_mid` dolares."""
    # Las alas valen $0.30 cada una; los cortos, lo que haga falta para que el neto de `credito_mid`.
    # Todas las patas con precio > 0: una punta en 0 es un hueco de datos y el motor no opera a ciegas.
    ala = 0.30
    corto = credito_mid / 100.0 / 2 + ala
    sp = _ContratoOCC("put", 7595, corto, corto, SP)
    sc = _ContratoOCC("call", 7670, corto, corto, SC)
    lp = _ContratoOCC("put", 7585, ala, ala, LP)
    lc = _ContratoOCC("call", 7680, ala, ala, LC)
    return _Cadena([sp, lp, sc, lc]), _Build((sp, lp, sc, lc), credito_mid)


def _abrir(conn, cfg, credito_mid, mails):
    cadena, build = _escena(credito_mid)
    broker = _BrokerQueAnota()
    lce._open_real_condor(conn, broker, "HASH", cadena, build, 7638.0, date(2026, 9, 2),
                          cfg, None, "$SPX", None)
    filas = conn.execute("SELECT * FROM real_condor_positions").fetchall()
    return broker, filas


def test_el_motor_NO_manda_la_orden_si_el_credito_no_llega_al_piso(conn, mails):
    """El condor de $95 del 2026-09-02. Con el piso puesto no llega ni a insertarse la fila."""
    from options_advisor.config import load_settings
    cfg = load_settings().intraday_condor.model_copy(update={"live_min_credit": 150.0})
    broker, filas = _abrir(conn, cfg, 95.0, mails)
    assert broker.enviadas == [], "no tenia que salir ninguna orden"
    assert filas == [], "ni siquiera se registra la intencion"


def test_el_motor_SI_manda_la_orden_cuando_el_credito_alcanza(conn, mails):
    """$180 es lo que venia cobrando. Ese pasa, y pasa con el precio en la grilla de 5 centavos."""
    from options_advisor.config import load_settings
    cfg = load_settings().intraday_condor.model_copy(update={"live_min_credit": 150.0})
    broker, filas = _abrir(conn, cfg, 180.0, mails)
    assert len(broker.enviadas) == 1
    assert round(float(broker.enviadas[0]["price"]) * 100) % 5 == 0
    assert len(filas) == 1


# ══════════════════════════════════════════════════════════════════════════════════════════
# 7. Ninguna orden del walk se pierde (la causa REAL de los -$295)
# ══════════════════════════════════════════════════════════════════════════════════════════

class _BrokerEscalera:
    """Reproduce lo que hizo Schwab el 2026-09-02.

    La escalera camina 1.95 -> 1.90 -> 1.85 -> 1.82 y cada reemplazo crea una orden NUEVA. El
    reemplazo a 1.82 sale REJECTED por la grilla de 5 centavos... y la orden de 1.85 sigue VIVA.
    """

    def __init__(self, estado_de_la_vieja="WORKING"):
        self.ids = []
        self.estado_de_la_vieja = estado_de_la_vieja
        self.cancelados = []

    def place_order(self, account_hash, payload):
        self.ids.append("ORD-1")
        return "ORD-1"

    def replace_order(self, account_hash, order_id, payload):
        nuevo = f"ORD-{len(self.ids) + 1}"
        self.ids.append(nuevo)
        return nuevo

    def get_order(self, account_hash, order_id):
        if order_id == self.ids[-1]:
            return {"status": "REJECTED",
                    "statusDescription": "Spread orders for SPX options must be priced in "
                                         "5-cent increments."}
        return {"status": self.estado_de_la_vieja}

    def cancel_order(self, account_hash, order_id):
        self.cancelados.append(order_id)


def _resultado_del_walk(broker, escalera):
    from options_advisor.execution import real_condor_sender as rcs
    legs = rcs.CondorLegs(short_put_symbol=SP, long_put_symbol=LP,
                          short_call_symbol=SC, long_call_symbol=LC)
    return rcs.execute_condor_walk(broker, "HASH", rcs.SIDE_OPEN, legs, 1, escalera,
                                   interval_seconds=0, poll_seconds=0, max_seconds=0,
                                   sleep=lambda _s: None, clock=lambda: 0.0)


def test_el_walk_guarda_todos_los_ids_que_creo():
    """Sin la lista de ids no hay forma de preguntar por las ordenes viejas."""
    broker = _BrokerEscalera()
    res = _resultado_del_walk(broker, [1.95, 1.90, 1.85, 1.80])
    assert len(res.order_ids) >= 1
    assert res.order_ids == broker.ids


def test_una_orden_vieja_todavia_viva_rescata_la_fila(conn, mails):
    """EL bug de los -$295. El ultimo id figura RECHAZADO pero el anterior sigue WORKING: esa pasa a
    ser la orden de la fila, y la posicion queda bajo vigilancia con stop en vez de descartarse."""
    from options_advisor.execution import real_condor_sender as rcs
    broker = _BrokerEscalera(estado_de_la_vieja="WORKING")
    res = rcs.CondorSendResult(ok=True, filled=False, order_id="ORD-4", status="REJECTED")
    res.order_ids = ["ORD-1", "ORD-2", "ORD-3", "ORD-4"]
    broker.ids = list(res.order_ids)

    rescate = lce._rescatar_orden_viva(broker, "HASH", res)
    assert rescate is not None
    oid, estado, _ = rescate
    assert oid == "ORD-3", "tiene que quedarse con la mas nueva de las que siguen vivas"
    assert estado == "WORKING"


def test_una_orden_vieja_que_ya_lleno_gana_siempre(conn, mails):
    """Si alguna de las viejas llenO, eso manda por encima de cualquier rechazo posterior."""
    from options_advisor.execution import real_condor_sender as rcs

    class _ConFill(_BrokerEscalera):
        def get_order(self, account_hash, order_id):
            if order_id == "ORD-2":
                return {"status": "FILLED"}
            if order_id == self.ids[-1]:
                return {"status": "REJECTED"}
            return {"status": "CANCELED"}

    broker = _ConFill()
    res = rcs.CondorSendResult(ok=True, filled=False, order_id="ORD-4", status="REJECTED")
    res.order_ids = ["ORD-1", "ORD-2", "ORD-3", "ORD-4"]
    broker.ids = list(res.order_ids)

    oid, estado, _ = lce._rescatar_orden_viva(broker, "HASH", res)
    assert (oid, estado) == ("ORD-2", "FILLED")


def test_si_estan_todas_muertas_si_se_descarta(conn, mails):
    """Cuando de verdad no quedO ninguna viva, se descarta. El arreglo no puede dejar filas zombis."""
    from options_advisor.execution import real_condor_sender as rcs

    class _TodasMuertas(_BrokerEscalera):
        def get_order(self, account_hash, order_id):
            return {"status": "CANCELED"}

    broker = _TodasMuertas()
    res = rcs.CondorSendResult(ok=True, filled=False, order_id="ORD-4", status="REJECTED")
    res.order_ids = ["ORD-1", "ORD-2", "ORD-3", "ORD-4"]
    broker.ids = list(res.order_ids)
    assert lce._rescatar_orden_viva(broker, "HASH", res) is None


def test_una_orden_que_no_contesta_no_se_da_por_muerta(conn, mails):
    """Sin respuesta del broker no se concluye nada: el 28/08 la red venia a los tumbos y una lectura
    de estado basura fue justamente lo que hizo tirar una posicion viva."""
    from options_advisor.execution import real_condor_sender as rcs

    class _Muda(_BrokerEscalera):
        def get_order(self, account_hash, order_id):
            if order_id == "ORD-4":
                return {"status": "REJECTED"}
            raise ConnectionError("la red se cayo")

    broker = _Muda()
    res = rcs.CondorSendResult(ok=True, filled=False, order_id="ORD-4", status="REJECTED")
    res.order_ids = ["ORD-1", "ORD-2", "ORD-3", "ORD-4"]
    broker.ids = list(res.order_ids)
    rescate = lce._rescatar_orden_viva(broker, "HASH", res)
    assert rescate is not None and rescate[1] == "DESCONOCIDA"


# ══════════════════════════════════════════════════════════════════════════════════════════
# 8. Si el stop no se puede garantizar, no abre
# ══════════════════════════════════════════════════════════════════════════════════════════

def test_el_barrido_devuelve_cuantas_patas_zombis_encontro(conn, mails):
    """El número es lo que después frena la apertura. Usuario 2026-09-02: "eso debe funcionar al
    100%, si no no debe abrir por seguridad"."""
    _fila_cerrada_hoy(conn)
    assert lce._barrer_posiciones_huerfanas(
        conn, _BrokerPosiciones([_Posicion(SC), _Posicion(SP)]), date(2026, 9, 2)) == 2
    assert lce._barrer_posiciones_huerfanas(
        conn, _BrokerPosiciones([]), date(2026, 9, 2)) == 0
