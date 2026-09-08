"""Solo UNA computadora puede operar con plata real, y esta escrita en el config.

Historia real (2026-09-08). Durante la mudanza al servidor conviven dos robots: el de la Mac, que
opera, y el del servidor, que solo mira. El 07/09 la mudanza avanzo y el servidor quedo en MODO
REAL — pero el robot de la Mac nunca se apago, que era el primer paso de la guia. Los dos quedaron
en real y asi estuvieron mas de un dia, sin una sola linea de error.

No paso nada de casualidad: el servidor no estaba armado. Hasta que el 08/09 el usuario dio START
en el dashboard del servidor creyendo que era el de la Mac —son identicos— y quedo habilitado para
operar. Se freno a los minutos, sin ordenes enviadas.

Lo que habria pasado: cada robot lleva sus topes diarios en SU PROPIA base y ninguno ve la del
otro. "1 condor por dia" son dos condors. "2 ordenes por dia" son cuatro.
"""

from __future__ import annotations

import pytest

from options_advisor.scheduler import maquina_real


def test_la_maquina_designada_puede_operar():
    assert maquina_real.es_la_maquina_real("MacBook-Pro-8", "MacBook-Pro-8") is True


def test_cualquier_otra_maquina_no():
    """El caso del 07/09: el servidor en modo real mientras la Mac tambien lo estaba."""
    assert maquina_real.es_la_maquina_real("MacBook-Pro-8", "lokshn") is False
    with pytest.raises(maquina_real.MaquinaEquivocada):
        maquina_real.exigir_maquina_real("MacBook-Pro-8", "lokshn")


@pytest.mark.parametrize("nombre", [
    "MacBook-Pro-8.local",   # macOS agrega y saca el .local segun como este la red
    "macbook-pro-8",         # y el nombre aparece en distintas capitalizaciones
    "MACBOOK-PRO-8.lan",
])
def test_el_nombre_se_normaliza(nombre):
    """Que el robot opere o no NO puede depender de un sufijo de red ni de una mayuscula."""
    assert maquina_real.es_la_maquina_real("MacBook-Pro-8", nombre) is True


def test_sin_configurar_el_candado_esta_apagado():
    """Nace apagado: nadie que no lo configure ve un cambio de comportamiento."""
    assert maquina_real.es_la_maquina_real("", "cualquier-maquina") is True
    assert maquina_real.es_la_maquina_real(None, "cualquier-maquina") is True
    maquina_real.exigir_maquina_real("", "cualquier-maquina")   # no lanza


def test_el_mensaje_dice_los_dos_caminos():
    """Quien lee esto esta en medio de una mudanza y tiene que DECIDIR cual maquina opera. El
    mensaje tiene que traer las dos salidas, no mandarlo a buscar que archivo tocar."""
    with pytest.raises(maquina_real.MaquinaEquivocada) as e:
        maquina_real.exigir_maquina_real("MacBook-Pro-8", "lokshn")
    texto = str(e.value)
    assert "lokshn" in texto and "macbook-pro-8" in texto
    assert "modo.py prueba" in texto, "el camino a) ponerla a mirar"
    assert "real_machine_hostname" in texto, "el camino b) que esta pase a operar"
    assert "APAGÁ la otra" in texto, "el orden importa: primero apagar la otra"


def test_mudarse_es_cambiar_una_linea():
    """La propiedad que hace que esto sirva: el mismo valor que habilita una maquina deshabilita la
    otra. No hay estado intermedio donde las dos puedan operar."""
    for designada, otra in (("MacBook-Pro-8", "lokshn"), ("lokshn", "MacBook-Pro-8")):
        assert maquina_real.es_la_maquina_real(designada, designada) is True
        assert maquina_real.es_la_maquina_real(designada, otra) is False
