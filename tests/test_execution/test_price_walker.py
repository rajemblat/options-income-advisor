"""Tests de la negociación del precio ("caminar el precio"). Propiedad central: nunca cruza el mid, y
arranca en el extremo favorable (usuario 2026-08-09)."""

from __future__ import annotations

from options_advisor.execution import price_walker as pw
from options_advisor.execution.price_walker import SIDE_BUY, SIDE_SELL


def test_mid_price():
    assert pw.mid_price(1.30, 1.70) == 1.50
    assert pw.mid_price(0.66, 0.74) == 0.70


# --------------------------- paso adaptativo ---------------------------

def test_adaptive_step_normal_spread_uses_base():
    # spread 0.04 → 0.25*0.04=0.01, pero base_step 0.02 manda → 0.02
    assert pw.adaptive_step(1.68, 1.72) == 0.02


def test_adaptive_step_wide_spread_grows():
    # spread 0.40 → 0.25*0.40 = 0.10 (llega al cap)
    assert pw.adaptive_step(1.30, 1.70) == 0.10


def test_adaptive_step_caps_at_max():
    # spread 2.00 → 0.50 sin cap, pero max_step 0.10 lo frena
    assert pw.adaptive_step(1.0, 3.0) == 0.10


def test_adaptive_step_never_below_min():
    assert pw.adaptive_step(0.50, 0.50, base_step=0.0) == 0.01   # spread 0 → min 1 centavo


# --------------------------- caminar VENDIENDO ---------------------------

def test_sell_starts_below_ask():
    # ask 1.70, step 0.02 → primer intento 1.68
    p = pw.next_limit_price(SIDE_SELL, 1.30, 1.70, step=0.02, current=None)
    assert p == 1.68


def test_sell_walks_down_toward_mid():
    p = pw.next_limit_price(SIDE_SELL, 1.30, 1.70, step=0.02, current=1.54)
    assert p == 1.52


def test_sell_never_crosses_mid():
    # bajando desde 1.51 con paso 0.02 daría 1.49, pero el mid es 1.50 → clamp a 1.50
    p = pw.next_limit_price(SIDE_SELL, 1.30, 1.70, step=0.02, current=1.51)
    assert p == 1.50


# --------------------------- caminar RECOMPRANDO ---------------------------

def test_buy_starts_above_bid():
    # bid 1.30, step 0.02 → primer intento 1.32
    p = pw.next_limit_price(SIDE_BUY, 1.30, 1.70, step=0.02, current=None)
    assert p == 1.32


def test_buy_walks_up_toward_mid():
    p = pw.next_limit_price(SIDE_BUY, 1.30, 1.70, step=0.02, current=1.46)
    assert p == 1.48


def test_buy_never_crosses_mid():
    p = pw.next_limit_price(SIDE_BUY, 1.30, 1.70, step=0.02, current=1.49)
    assert p == 1.50   # no sube por encima del mid


# --------------------------- reached_mid + escalera ---------------------------

def test_reached_mid():
    assert pw.reached_mid(SIDE_SELL, 1.50, 1.30, 1.70) is True
    assert pw.reached_mid(SIDE_SELL, 1.52, 1.30, 1.70) is False
    assert pw.reached_mid(SIDE_BUY, 1.50, 1.30, 1.70) is True
    assert pw.reached_mid(SIDE_BUY, 1.48, 1.30, 1.70) is False


def test_ladder_sell_ends_at_mid_and_is_descending():
    ladder = pw.build_price_ladder(SIDE_SELL, 1.30, 1.70, base_step=0.02, max_step=0.02)
    assert ladder[0] == 1.68
    assert ladder[-1] == 1.50            # termina en el mid
    assert ladder == sorted(ladder, reverse=True)   # va bajando
    assert all(p >= 1.50 for p in ladder)           # nunca por debajo del mid


def test_ladder_buy_ends_at_mid_and_is_ascending():
    ladder = pw.build_price_ladder(SIDE_BUY, 1.30, 1.70, base_step=0.02, max_step=0.02)
    assert ladder[0] == 1.32
    assert ladder[-1] == 1.50
    assert ladder == sorted(ladder)
    assert all(p <= 1.50 for p in ladder)


def test_spread_based_step():
    # Spread grande (>0.50) → $5/contrato (0.05); chico → $2/contrato (0.02).
    assert pw.spread_based_step(11.50, 12.50) == 0.05   # spread 1.00 → grande
    assert pw.spread_based_step(5.50, 5.80) == 0.02      # spread 0.30 → chico
    assert pw.spread_based_step(0.36, 0.45) == 0.02      # spread 0.09 → chico
    assert pw.spread_based_step(1.00, 1.50) == 0.02      # spread 0.50 exacto → NO es grande
    assert pw.spread_based_step(1.00, 1.52) == 0.05      # spread 0.52 → grande


def test_wide_spread_ladder_strike_132():
    # Strike 132 bid 11.50 / ask 12.50 (spread 1.00, grande) → pasos de $5/contrato.
    step = pw.spread_based_step(11.50, 12.50)
    sell = pw.build_price_ladder(SIDE_SELL, 11.50, 12.50, step=step)
    assert sell[0] == 12.45 and sell[1] == 12.40 and sell[-1] == 12.00   # de 5 en 5 hasta el mid
    assert len(sell) == 10                                              # 9 pasos, no 25
    buy = pw.build_price_ladder(SIDE_BUY, 11.50, 12.50, step=step)
    assert buy[0] == 11.55 and buy[-1] == 12.00                         # sube de 5 en 5 hasta el mid


def test_fixed_step_ladder_tsla_example_user():
    # Ejemplo del usuario (2026-08-09): TSLA put 5.50–5.80, paso fijo 2¢.
    sell = pw.build_price_ladder(SIDE_SELL, 5.50, 5.80, step=0.02)
    assert sell[0] == 5.78 and sell[1] == 5.76 and sell[-1] == 5.65   # baja de 2 en 2 hasta el mid
    buy = pw.build_price_ladder(SIDE_BUY, 5.50, 5.80, step=0.02)
    assert buy[0] == 5.52 and buy[1] == 5.54 and buy[-1] == 5.65      # sube de 2 en 2 hasta el mid


def test_fixed_step_ladder_aal_cents_example_user():
    # Ejemplo del usuario: AAL put 0.70–0.76, paso fijo 2¢.
    sell = pw.build_price_ladder(SIDE_SELL, 0.70, 0.76, step=0.02)
    assert sell[0] == 0.74 and sell[-1] == 0.73                       # 0.74 → mid 0.73
    buy = pw.build_price_ladder(SIDE_BUY, 0.70, 0.76, step=0.02)
    assert buy[0] == 0.72 and buy[-1] == 0.73                         # 0.72 → mid 0.73


def test_ladder_cheap_option_small_steps():
    # Opción barata, spread finito 0.66-0.74 (8 centavos): paso adaptativo pequeño, escalera corta.
    ladder = pw.build_price_ladder(SIDE_SELL, 0.66, 0.74)
    assert ladder[0] < 0.74                # arranca bajo el ask
    assert ladder[-1] == 0.70              # mid
    assert all(p >= 0.70 for p in ladder)
