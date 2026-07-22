from options_advisor.broker.base import BrokerClient
from options_advisor.broker.mock_client import MockBrokerClient
from options_advisor.config import Settings


def get_broker_client(settings: Settings) -> BrokerClient:
    """Factory: decide qué implementación de BrokerClient inyectar según settings.broker.mode.

    Ningún módulo de negocio (indicators/strategy/alerts/dashboard) debe importar
    MockBrokerClient o SchwabBrokerClient directamente — siempre a través de acá,
    para mantener la arquitectura agnóstica de broker (Sección 7.2 de la hoja de ruta).
    """
    if settings.broker.mode == "mock":
        return MockBrokerClient(fixtures_dir=settings.broker.resolved_fixtures_dir())

    from options_advisor.broker.schwab_client import SchwabBrokerClient

    return SchwabBrokerClient.from_env()


__all__ = ["BrokerClient", "MockBrokerClient", "get_broker_client"]
