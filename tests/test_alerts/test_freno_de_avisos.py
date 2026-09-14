"""El freno de avisos: un problema manda UN mail, no mil (usuario 2026-09-14).

El caso real: el viernes 11 el robot entró en bucle —se caía, el healthcheck lo levantaba, se volvía
a caer— y cada vuelta mandaba un mail. El lunes el usuario abrió el correo con miles de mails
idénticos: "tampoco quiero que me lleguen más estos emails, me llegan miles".

Lo que se protege acá, en orden de importancia:
  · el PRIMER aviso NUNCA se frena — el freno no puede hacer que un robot caído pase inadvertido;
  · las repeticiones se callan pero se CUENTAN, y esa cuenta viaja en el próximo mail;
  · pasada la ventana sale un recordatorio, para que un problema que dura no se olvide;
  · dos problemas distintos no se tapan entre sí;
  · resolver limpia el estado, así el próximo episodio vuelve a avisar al instante;
  · un estado corrupto avisa (lado seguro del error).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from options_advisor.alerts import freno_de_avisos as freno

T0 = datetime(2026, 9, 11, 16, 40, 0)
CLAVE = "scheduler.colgado"


def test_el_primer_aviso_sale_siempre():
    estado = {}
    d = freno.decidir(estado, CLAVE, T0)
    assert d.avisar is True
    assert d.calladas == 0


def test_las_repeticiones_dentro_de_la_ventana_se_callan():
    """Seis horas de bucle a un chequeo por minuto: 359 repeticiones, cero mails."""
    estado = {}
    freno.decidir(estado, CLAVE, T0)
    for i in range(1, 360):                     # hasta 5h59m — todavía dentro de la ventana
        d = freno.decidir(estado, CLAVE, T0 + timedelta(minutes=i))
        assert d.avisar is False, f"la repetición del minuto {i} no debería mandar mail"
    assert estado[CLAVE]["calladas"] == 359


def test_el_bucle_del_11_de_septiembre_manda_dos_mails_y_no_ciento_cuarenta():
    """Reconstrucción del caso real: el healthcheck corre cada 5 minutos durante 12 horas con el
    robot cayéndose siempre. Antes era un mail por corrida —144—; ahora son 2: el primero al
    instante y el recordatorio de las 6 h."""
    estado = {}
    mails = sum(1 for i in range(144)          # 144 corridas × 5 min = 12 horas
                if freno.decidir(estado, CLAVE, T0 + timedelta(minutes=5 * i)).avisar)
    assert mails == 2, f"mandó {mails} mails en 12 horas de bucle"
    assert estado[CLAVE]["total"] == 144       # pero los contó a todos


def test_el_recordatorio_cuenta_cuantas_se_callo():
    estado = {}
    freno.decidir(estado, CLAVE, T0)
    for i in range(1, 50):
        freno.decidir(estado, CLAVE, T0 + timedelta(minutes=i))
    d = freno.decidir(estado, CLAVE, T0 + timedelta(hours=6, minutes=1))
    assert d.avisar is True
    assert d.calladas == 49                     # la cuenta es la información valiosa del recordatorio
    assert "49" in d.texto_de_repeticiones


def test_despues_del_recordatorio_vuelve_a_callarse():
    estado = {}
    freno.decidir(estado, CLAVE, T0)
    freno.decidir(estado, CLAVE, T0 + timedelta(hours=6, minutes=1))
    d = freno.decidir(estado, CLAVE, T0 + timedelta(hours=6, minutes=6))
    assert d.avisar is False


def test_dos_problemas_distintos_no_se_tapan():
    """Que el robot se esté colgando no puede silenciar el 'no lo puedo levantar', que es más grave."""
    estado = {}
    assert freno.decidir(estado, "scheduler.colgado", T0).avisar is True
    assert freno.decidir(estado, "scheduler.no_arranca", T0).avisar is True


def test_resolver_hace_que_el_proximo_episodio_avise_al_instante():
    estado = {}
    freno.decidir(estado, CLAVE, T0)
    for i in range(1, 10):
        freno.decidir(estado, CLAVE, T0 + timedelta(minutes=i))
    # Estuvo sano una ventana entera (el último evento fue en T0+9min): recién ahí se resuelve.
    assert freno.marcar_resuelto(estado, CLAVE, T0 + timedelta(hours=7)) == 10      # 1 + 9
    assert CLAVE not in estado
    # Y cuando vuelve a pasar, avisa YA: no quedó tapado por el freno del episodio anterior.
    assert freno.decidir(estado, CLAVE, T0 + timedelta(hours=8)).avisar is True


def test_un_respiro_corto_NO_cuenta_como_resuelto():
    """EL BUG DEL 14/09. El robot se caía, el healthcheck lo reiniciaba bien, la corrida siguiente
    lo encontraba sano y se limpiaba el freno — así que la próxima caída volvía a mandar mail. El
    usuario lo recibió cada dos horas. Estar sano un rato no es haberse resuelto."""
    estado = {}
    freno.decidir(estado, CLAVE, T0)
    assert freno.marcar_resuelto(estado, CLAVE, T0 + timedelta(minutes=10)) == 0
    assert CLAVE in estado, "el freno no puede borrarse por un respiro de 10 minutos"
    # Se vuelve a caer dentro de la ventana: NO debe mandar otro mail.
    assert freno.decidir(estado, CLAVE, T0 + timedelta(hours=2)).avisar is False


def test_el_bucle_de_caidas_con_reinicios_exitosos_manda_un_solo_mail():
    """El caso real completo: cae, se reinicia bien, queda sano un rato, vuelve a caer. Doce horas
    de eso a chequeo cada 5 min. Antes era un mail por caída; ahora es el primero y el de las 6 h."""
    estado = {}
    mails = 0
    for i in range(144):
        t = T0 + timedelta(minutes=5 * i)
        if i % 24 == 0:                                   # se cae cada 2 horas
            if freno.decidir(estado, CLAVE, t).avisar:
                mails += 1
        else:                                             # el resto del tiempo está sano
            freno.marcar_resuelto(estado, CLAVE, t)
    assert mails == 2, f"mandó {mails} mails en 12 horas de caídas cada 2 h"


def test_resolver_algo_que_nunca_paso_no_rompe():
    assert freno.marcar_resuelto({}, CLAVE, T0) == 0


def test_un_estado_corrupto_avisa_igual():
    """Lado seguro del error: preferimos un mail de más antes que perder el aviso de un robot caído."""
    estado = {CLAVE: {"ultimo_aviso": "no-es-una-fecha", "calladas": 3, "total": 3}}
    assert freno.decidir(estado, CLAVE, T0).avisar is True


def test_sin_texto_de_repeticiones_cuando_no_hubo():
    assert freno.Decision(avisar=True, calladas=0, total=1).texto_de_repeticiones == ""


# --- Persistencia ---

def test_guarda_y_carga(tmp_path):
    ruta = tmp_path / "sub" / "avisos.json"
    estado = {}
    freno.decidir(estado, CLAVE, T0)
    freno.guardar(ruta, estado)
    assert freno.cargar(ruta) == estado


def test_un_archivo_ausente_o_roto_devuelve_estado_vacio(tmp_path):
    assert freno.cargar(tmp_path / "no-existe.json") == {}
    roto = tmp_path / "roto.json"
    roto.write_text("{esto no es json")
    assert freno.cargar(roto) == {}
