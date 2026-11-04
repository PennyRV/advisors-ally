"""Agenda conectada al calendario principal de Google del agente local."""
from datetime import datetime, timedelta, time
from uuid import uuid4
from zoneinfo import ZoneInfo
from html import escape
from urllib.parse import urlparse
import streamlit as st
from almacen_documentos import usuario_validado, ErrorDocumentos
from clientes import AgendaClientes, ErrorClientes
from calendario_google import CalendarioGoogle, ErrorCalendario, crear_borrador, enlace_meet, ZONA
from calendario_web import ConexionGoogleWeb, CalendarioGoogleWeb, configuracion


def enlace_google(etiqueta, url):
    """Enlace normal: evita el módulo lazy LinkButton que falla en Cloud."""
    destino = urlparse(str(url))
    if (destino.scheme != 'https' or destino.netloc not in {
            'accounts.google.com', 'myaccount.google.com', 'meet.google.com',
            'calendar.google.com', 'www.google.com'} or destino.username):
        st.warning('No se pudo mostrar el enlace de Google. Prepara nuevamente la conexión.')
        return
    st.markdown(
        '<a href="' + escape(str(url), quote=True) + '" target="_blank" '
        'rel="noopener noreferrer" style="display:inline-block;background:#245C50;'
        'color:white;padding:0.65rem 1rem;border-radius:0.75rem;text-decoration:none;'
        'font-weight:600;margin:0.25rem 0">' + escape(etiqueta) + '</a>',
        unsafe_allow_html=True,
    )


def mostrar_calendario(cliente):
    st.header('Calendario del agente')
    st.caption('Google Calendar · citas, recordatorios e invitaciones con Google Meet')
    if cliente is None:
        st.info('Inicia sesión para acceder al calendario de tu cuenta de agente.')
        return
    try:
        uid = str(usuario_validado(cliente).id)
    except ErrorDocumentos as exc:
        st.warning(str(exc))
        return
    if st.session_state.get('cal_uid') != uid:
        for k in list(st.session_state):
            if k.startswith('cal_'):
                del st.session_state[k]
        st.session_state['cal_uid'] = uid
    try:
        habilitado = st.secrets.get('calendario', {}).get('modo_local', False) is True
    except FileNotFoundError:
        habilitado = False
    if habilitado:
        with st.expander('Conectar o cambiar cuenta de Google'):
            st.write('Ejecuta este comando en otra terminal de Visual Studio Code, dentro de tu proyecto:')
            st.code(f'.\\.venv\\Scripts\\python.exe conectar_calendario.py --agente-id {uid}', language='powershell')
            st.caption('La autorización se hace en Google. No introduzcas tu contraseña de Google en la app. Los accesos se guardan por ID de agente en el perfil local de Windows.')
            st.button('Actualizar conexión', key='cal_actualizar')
            enlace_google('Administrar permisos en Google', 'https://myaccount.google.com/connections')
    else:
        st.caption('Conecta tu propia cuenta. Google pedirá permiso para gestionar sus eventos; las invitaciones solo se solicitan al confirmar una cita.')
        try:
            configuracion()
            if st.button('Preparar conexión con Google', key='cal_web_preparar', type='primary'):
                st.session_state['cal_web_url'] = ConexionGoogleWeb(cliente).iniciar()
            if st.session_state.get('cal_web_url'):
                enlace_google('Conectar con Google', st.session_state['cal_web_url'])
                st.caption('La autorización abre otra pestaña. Si Google te pide volver a iniciar sesión en Advisor’s Ally, usa la misma cuenta de agente y después vuelve a Calendario. El enlace dura 10 minutos.')
            st.button('Actualizar conexión', key='cal_web_actualizar')
        except (ErrorCalendario, ErrorDocumentos) as exc:
            st.warning(str(exc))
            return
    try:
        google = CalendarioGoogle(uid) if habilitado else CalendarioGoogleWeb(cliente)
        cuenta = google.calendario()
    except ErrorCalendario as exc:
        st.warning(str(exc))
        return
    cuenta_id = cuenta.get('id', '')
    if st.session_state.get('cal_cuenta') != cuenta_id:
        st.session_state.pop('cal_borrador', None)
        st.session_state.pop('cal_creada', None)
        st.session_state['cal_cuenta'] = cuenta_id
    st.success('Calendario conectado: ' + (cuenta.get('summary') or cuenta_id))
    st.caption('Cuenta/calendario principal: ' + cuenta_id)
    agenda, crear = st.tabs(['Mi agenda', 'Crear cita'])
    with agenda:
        hoy = datetime.now(ZoneInfo(ZONA)).date()
        with st.form('cal_rango'):
            desde = st.date_input('Desde', value=hoy)
            hasta = st.date_input('Hasta', value=hoy + timedelta(days=7))
            consultar = st.form_submit_button('Consultar agenda', type='primary')
        if consultar:
            try:
                if hasta < desde or (hasta - desde).days > 90:
                    raise ErrorCalendario('Selecciona un intervalo de hasta 90 días, con el final posterior al inicio.')
                tz = ZoneInfo(ZONA)
                eventos = google.listar(datetime.combine(desde, time.min, tz), datetime.combine(hasta + timedelta(days=1), time.min, tz))
                if not eventos:
                    st.info('No hay citas en ese intervalo.')
                for numero_evento, e in enumerate(eventos, 1):
                    st.write(e.get('summary') or 'Sin título')
                    inicio = e.get('start', {})
                    if inicio.get('dateTime'):
                        valor = datetime.fromisoformat(inicio['dateTime']).astimezone(tz).strftime('%d/%m/%Y %H:%M')
                    else:
                        valor = inicio.get('date', '') + ' · todo el día'
                    st.caption(valor + ' · Ciudad de México')
                    if enlace_meet(e):
                        enlace_google(f'Abrir Meet · {numero_evento}', enlace_meet(e))
            except ErrorCalendario as exc:
                st.warning(str(exc))
    with crear:
        st.caption('Primero prepara la cita. Después revisa el destinatario y pulsa Crear cita y enviar invitación. No se adjuntan pólizas ni se usa IA.')
        try:
            clientes = AgendaClientes(cliente).listar('aa_clientes')
        except (ErrorClientes, ErrorDocumentos) as exc:
            st.warning(str(exc))
            clientes = []
        opciones = {'': None}
        opciones.update({c['id']: c for c in clientes})
        seleccionado = st.selectbox('Cliente (opcional)', list(opciones), format_func=lambda k: 'Cita personal / otro contacto' if not k else opciones[k]['nombre'], key='cal_cliente')
        c = opciones[seleccionado]
        correo = st.text_input('Correo del invitado (revisa antes de enviar)', value=(c or {}).get('correo', ''), key='cal_correo_' + (seleccionado or 'personal'), max_chars=254)
        meet_disponible = 'hangoutsMeet' in cuenta.get('conferenceProperties', {}).get('allowedConferenceSolutionTypes', [])
        with st.form('cal_nueva'):
            titulo = st.text_input('Título de la cita', max_chars=200)
            dia = st.date_input('Fecha', value=hoy, min_value=hoy)
            hora = st.time_input('Hora de inicio', value=time(10, 0))
            zona = st.selectbox('Zona horaria', [ZONA, 'America/Cancun', 'America/Tijuana', 'Europe/Brussels'])
            duracion = st.number_input('Duración en minutos', min_value=5, max_value=480, value=30, step=5)
            aviso = st.selectbox('Aviso al agente (minutos antes)', [0, 10, 15, 30, 60, 1440], index=2)
            nota = st.text_area('Descripción visible para el invitado', max_chars=2000)
            meet = st.checkbox('Crear enlace único de Google Meet', value=meet_disponible, disabled=not meet_disponible)
            if not meet_disponible:
                st.caption('Este calendario no anuncia disponibilidad de Google Meet. Puedes crear la cita sin videollamada.')
            preparar = st.form_submit_button('Preparar cita', type='primary', disabled=bool(st.session_state.get('cal_borrador')))
        if preparar:
            try:
                body = crear_borrador(titulo, dia, hora, duracion, zona, correo, meet, aviso, nota, uid, uuid4().hex)
                st.session_state['cal_borrador'] = dict(body=body, cuenta=cuenta_id)
                st.session_state.pop('cal_creada', None)
            except ErrorCalendario as exc:
                st.warning(str(exc))
        borrador = st.session_state.get('cal_borrador')
        if borrador:
            b = borrador['body']
            st.subheader('Revisa esta cita')
            st.write(b['summary'])
            st.write('Inicio: ' + b['start']['dateTime'] + ' · ' + b['start']['timeZone'])
            st.write('Fin: ' + b['end']['dateTime'])
            destinatario = next(iter(b.get('attendees', [])), {}).get('email')
            st.write('Invitado: ' + (destinatario or 'Ninguno; solo calendario del agente'))
            st.write('Google Meet: ' + ('Sí' if 'conferenceData' in b else 'No'))
            st.write('Recordatorio al agente: ' + str(b['reminders']['overrides'][0]['minutes']) + ' minutos antes')
            if b['description']:
                st.text(b['description'])
            confirmado = st.checkbox('Revisé la fecha, el horario y el correo; autorizo crear esta cita y enviar la invitación al destinatario mostrado.', key='cal_confirmar_' + b['id'])
            if st.button('Crear cita y enviar invitación' if destinatario else 'Crear cita personal', disabled=not confirmado, key='cal_enviar_' + b['id'], type='primary'):
                try:
                    if borrador['cuenta'] != cuenta_id:
                        raise ErrorCalendario('Cambió la cuenta de Google. Prepara otra cita.')
                    creado = google.crear(b)
                    st.session_state['cal_creada'] = creado
                    st.session_state.pop('cal_borrador', None)
                    st.rerun()
                except ErrorCalendario as exc:
                    st.warning(str(exc))
                    st.caption('Conserva este borrador para reintentar la misma operación; así no se solicita una cita nueva si Google ya la creó.')
            if st.button('Descartar borrador', key='cal_descartar'):
                st.session_state.pop('cal_borrador', None)
                st.rerun()
        creado = st.session_state.get('cal_creada')
        if creado:
            st.success('Cita creada en Google Calendar. Si añadiste un invitado, se solicitó el envío de la invitación por correo.')
            st.write(creado.get('summary', ''))
            enlace = enlace_meet(creado)
            if enlace:
                enlace_google('Abrir Google Meet', enlace)
            elif creado.get('conferenceData'):
                estado = creado['conferenceData'].get('createRequest', {}).get('status', {}).get('statusCode')
                st.warning('Google aún no entregó el enlace de Meet.' if estado != 'failure' else 'La cita se creó, pero Google no pudo generar el Meet. Abre el evento en Google Calendar para revisarlo.')
                if st.button('Actualizar enlace de Meet', key='cal_meet_actualizar'):
                    try:
                        st.session_state['cal_creada'] = google.evento(creado['id'])
                        st.rerun()
                    except ErrorCalendario as exc:
                        st.warning(str(exc))
            link = creado.get('htmlLink', '')
            if link.startswith('https://www.google.com/calendar/') or link.startswith('https://calendar.google.com/'):
                enlace_google('Abrir cita en Google Calendar', link)
    st.caption('Los avisos de esta cita los gestiona Google Calendar para el agente, según sus ajustes y permisos de notificación. No se envían recordatorios por WhatsApp. Los invitados gestionan sus propios avisos.')
