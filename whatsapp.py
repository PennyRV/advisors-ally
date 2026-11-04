"""Preparación local de WhatsApp. No recibe webhooks ni envía mensajes."""
from calendar import isleap
from datetime import date, datetime
import re
from zoneinfo import ZoneInfo

from almacen_documentos import usuario_validado, ErrorDocumentos
from clientes import AgendaClientes, ErrorClientes
from consulta_automatica import (
    catalogo_guardado, resolver_ambito, recuperar_paginas, ErrorConsulta,
)
from ia_documentos import buscar_fragmentos, responder_documentos

ZONA = ZoneInfo('America/Mexico_City')


def telefono_canonico(valor):
    """Comparación para pruebas; no acredita la identidad de quien escribe."""
    valor = str(valor or '').strip()
    if not valor or re.search(r'[^0-9+() .-]', valor) or '+' in valor[1:]:
        return None
    digitos = re.sub(r'\D', '', valor)
    if len(digitos) == 10 and not valor.startswith('+'):
        digitos = '52' + digitos
    if len(digitos) == 13 and digitos.startswith('521'):
        digitos = '52' + digitos[3:]
    if not 8 <= len(digitos) <= 15 or digitos.startswith('0'):
        return None
    return digitos


def cliente_del_telefono(telefono, clientes):
    numero = telefono_canonico(telefono)
    if not numero:
        raise ErrorConsulta('Escribe un teléfono válido con código de país; se asume México para 10 dígitos sin +.')
    coincidencias = [c for c in clientes if telefono_canonico(c.get('telefono')) == numero]
    if len(coincidencias) != 1:
        raise ErrorConsulta('El teléfono debe coincidir con un único cliente. Revisa teléfonos vacíos, duplicados o no registrados en Clientes.')
    return coincidencias[0]


def documentos_del_cliente(pregunta, titular, polizas, archivos):
    propias = [p for p in polizas if p['cliente_id'] == titular['id']]
    ids = {p['id'] for p in propias}
    adjuntos = [a for a in archivos if a.get('poliza_id') in ids]
    if not adjuntos:
        raise ErrorConsulta('Este cliente no tiene PDF vinculados a sus pólizas. Súbelos desde Clientes.')
    # El catálogo se limita ANTES de buscar: nunca incluye PDF generales u otro cliente.
    elegidos, ambito = resolver_ambito(pregunta, [titular], propias, adjuntos)
    return elegidos, ambito


def fecha(valor):
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def borradores_del_dia(clientes, polizas, dia, anticipacion, firma):
    """Previsualización determinista; no persiste colas ni registra consentimiento."""
    if not isinstance(dia, date) or isinstance(dia, datetime):
        raise ValueError('Selecciona una fecha válida.')
    if not isinstance(anticipacion, int) or not 1 <= anticipacion <= 365:
        raise ValueError('La anticipación debe estar entre 1 y 365 días.')
    firma = str(firma or '').strip()[:200] or 'Tu agente de seguros'
    por_id = {c['id']: c for c in clientes}
    resultado = []
    for c in clientes:
        nacimiento = fecha(c.get('fecha_nacimiento'))
        if not nacimiento or nacimiento > dia:
            continue
        mes, numero = nacimiento.month, nacimiento.day
        if mes == 2 and numero == 29 and not isleap(dia.year):
            numero = 28
        if (mes, numero) == (dia.month, dia.day):
            resultado.append(dict(
                clave=f"cumpleanos:{c['id']}:{dia.year}", tipo='Cumpleaños',
                cliente=c['nombre'], telefono=c.get('telefono') or 'Sin teléfono',
                mensaje=f"¡Feliz cumpleaños, {c['nombre']}! Te deseamos un día muy especial. Un saludo de {firma}."))
    for p in polizas:
        vencimiento = fecha(p.get('fecha_vencimiento'))
        c = por_id.get(p['cliente_id'])
        if not c or not vencimiento or (vencimiento - dia).days != anticipacion:
            continue
        resultado.append(dict(
            clave=f"renovacion:{p['id']}:{vencimiento.isoformat()}:{anticipacion}",
            tipo='Renovación', cliente=c['nombre'], telefono=c.get('telefono') or 'Sin teléfono',
            mensaje=(f"Hola, {c['nombre']}. Tu póliza {p['numero']} de {p['compania']} "
                     f"tiene fecha de vencimiento el {vencimiento:%d/%m/%Y}, dentro de {anticipacion} días. "
                     f"Contacta a {firma} para revisar las opciones de renovación.")))
    return resultado


def mostrar_whatsapp(cliente, modelo):
    import streamlit as st
    st.header('WhatsApp')
    st.info('Modo preparación. Esta sección no recibe ni envía mensajes de WhatsApp; los envíos están desactivados.')
    if cliente is None:
        st.write('Inicia sesión para usar tus clientes y documentos guardados.')
        return
    try:
        uid = str(usuario_validado(cliente).id)
    except ErrorDocumentos as exc:
        st.warning(str(exc))
        return
    # Los resultados duran un solo render: no quedan respuestas de otro teléfono/usuario.
    prueba, recordatorios, conexion = st.tabs(['Probar preguntas', 'Cumpleaños y renovaciones', 'Conexión pendiente'])
    with prueba:
        st.write('Simula una pregunta de un cliente usando su teléfono registrado. No tendrás que seleccionar documentos.')
        st.caption('Esta prueba la realiza el agente dentro de la app. Conocer un teléfono no verifica la identidad de un cliente ni autoriza a divulgar documentos.')
        with st.form('wa_prueba'):
            numero = st.text_input('Teléfono del cliente de prueba', max_chars=40)
            pregunta = st.text_input('Pregunta del cliente', max_chars=2000, placeholder='¿Qué deducible tiene mi póliza DEMO-001?')
            permiso = st.checkbox('Autorizo enviar la pregunta y los fragmentos a Gemini. Uso datos ficticios o documentos sin información personal ni confidencial.')
            consultar = st.form_submit_button('Probar respuesta', type='primary')
        if consultar:
            try:
                if not pregunta.strip():
                    raise ErrorConsulta('Escribe una pregunta.')
                with st.spinner('Buscando en las pólizas del cliente...'):
                    clientes, polizas, archivos, _ = catalogo_guardado(cliente)
                    titular = cliente_del_telefono(numero, clientes)
                    elegidos, ambito = documentos_del_cliente(pregunta, titular, polizas, archivos)
                    paginas, avisos = recuperar_paginas(cliente, elegidos, {})
                    fuentes = buscar_fragmentos(pregunta, paginas, k=8)
                st.info(f"Cliente de prueba: {titular['nombre']}. {ambito}")
                for aviso in avisos:
                    st.warning(aviso)
                if not fuentes:
                    st.warning('No encontré información suficiente en sus PDF. No se llamó a Gemini.')
                else:
                    if permiso:
                        try:
                            clave = st.secrets['GEMINI_API_KEY'].strip()
                        except (KeyError, FileNotFoundError):
                            clave = ''
                        if not clave or not modelo:
                            st.warning('Revisa la clave y el modelo de Gemini. Las fuentes están disponibles abajo.')
                        else:
                            with st.spinner('Preparando el borrador...'):
                                respuesta, error = responder_documentos(pregunta, fuentes, clave, modelo=modelo)
                            if error:
                                st.warning(error)
                            if respuesta:
                                st.subheader('Borrador para revisar')
                                st.markdown(respuesta)
                                st.caption('No se envió por WhatsApp. Revisa las citas y el alcance de la póliza antes de compartir.')
                    else:
                        st.caption('Solo búsqueda local; no se envió la pregunta ni los fragmentos a Gemini.')
                    with st.expander('Fuentes consultadas', expanded=not permiso):
                        for i, fuente in enumerate(fuentes, 1):
                            st.write(f"Fuente {i} · {fuente['documento']} · Página PDF {fuente['pagina']}")
                            st.text(fuente['texto'])
            except (ErrorConsulta, ErrorDocumentos, ErrorClientes) as exc:
                st.warning(str(exc))
            except Exception:
                st.error('No se pudo completar la prueba. Revisa tu conexión y vuelve a intentarlo.')
    with recordatorios:
        st.write('Previsualiza los mensajes usando las fechas que ya guardaste en Clientes.')
        with st.form('wa_borradores'):
            dia = st.date_input('Día que quieres revisar', value=datetime.now(ZONA).date())
            anticipacion = st.number_input('Días de anticipación para renovar', min_value=1, max_value=365, value=30, step=1)
            firma = st.text_input('Firma del mensaje', value=st.session_state.get('nombre_agente', '') or 'Advisor’s Ally', max_chars=200)
            ver = st.form_submit_button('Ver borradores', type='primary')
        st.caption('Fecha de referencia: Ciudad de México. Cumpleaños del 29 de febrero: se muestran el 28 en años no bisiestos. Renovaciones: fecha de vencimiento registrada, sin inferir pagos ni renovación automática.')
        if ver:
            try:
                agenda = AgendaClientes(cliente)
                clientes = agenda.listar('aa_clientes')
                polizas = agenda.listar('aa_polizas')
                borradores = borradores_del_dia(clientes, polizas, dia, int(anticipacion), firma)
                if not borradores:
                    st.info('No hay cumpleaños ni vencimientos con esa anticipación para el día elegido.')
                for b in borradores:
                    st.subheader(f"{b['tipo']} · {b['cliente']}")
                    st.caption('Teléfono registrado: ' + b['telefono'])
                    st.text(b['mensaje'])
                st.caption('Son borradores, incluidos los clientes sin teléfono o sin autorización registrada. No se guardan tareas, no se envían mensajes y no se programan envíos al cerrar la app.')
            except (ErrorClientes, ErrorDocumentos, ValueError) as exc:
                st.warning(str(exc))
            except Exception:
                st.error('No se pudieron consultar las fechas. Revisa tu conexión.')
    with conexion:
        st.write('La verificación de la empresa está en revisión según la información que enviaste a Meta.')
        st.write('Cuando Meta confirme el resultado, continuaremos con el número habilitado, la recepción de mensajes y el servicio que ejecutará los recordatorios aunque Streamlit esté cerrado.')
        st.write('Antes de activar envíos revisaremos las autorizaciones de los clientes, las plantillas y la verificación de identidad para consultar pólizas.')
        st.caption('Esta versión no solicita tokens de Meta. Conserva tus claves en secretos y no las compartas por chat ni en GitHub.')
