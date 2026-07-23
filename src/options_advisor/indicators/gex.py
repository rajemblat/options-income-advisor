from __future__ import annotations

from options_advisor.broker.models import OptionChain

_CONTRACT_MULTIPLIER = 100
_PCT_MOVE = 0.01  # GEX convencional: exposición de gamma a un movimiento del 1% del subyacente


def compute_net_gex(chain: OptionChain) -> float:
    """Net Gamma Exposure agregado de TODA la cadena (no solo los candidatos elegidos):
    gamma × open interest × 100 × precio² × 1% por contrato, signo +1 para calls / -1 para
    puts (convención estándar retail: "dealers netos cortos" — SpotGamma/tastylive). Positivo
    sugiere que los dealers compran en bajas/venden en subas (amortigua movimiento); negativo,
    lo contrario (amplifica movimiento). Explícitamente Net GEX agregado por símbolo, no un
    perfil por strike (alcance acordado)."""
    price = chain.underlying_price
    total = 0.0
    for ct in chain.contracts:
        sign = 1 if ct.option_type == "call" else -1
        total += sign * ct.greeks.gamma * ct.open_interest * _CONTRACT_MULTIPLIER * (price**2) * _PCT_MOVE
    return round(total, 2)
