"""Google Calendar para el servidor LOCAL del agente; no usa credenciales globales."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID
import hashlib
import json
import os
import re
import tempfile
from zoneinfo import ZoneInfo
import requests

SCOPES = ['https://www.googleapis.com/auth/calendar.events.owned',
          'https://www.googleapis.com/auth/calendar.calendars.readonly']
ZONA = 'America/Mexico_City'

class ErrorCalendario(Exception):
    pass


def ruta_token(uid):
    uid = str(UUID(str(uid)))
    base = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / '.local' / 'share'))
    return base / 'AdvisorsAlly' / 'calendarios' / (uid + '.json')


def guardar_token(uid, contenido):
    destino = ruta_token(uid)
    destino.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporal = tempfile.mkstemp(dir=destino.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(dict(agente_id=str(UUID(str(uid))), credenciales=contenido), f)
        os.chmod(temporal, 0o600)
        os.replace(temporal, destino)
    finally:
        if os.path.exists(temporal):
            os.unlink(temporal)


def credenciales(uid):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    try:
        datos = json.loads(ruta_token(uid).read_text(encoding='utf-8'))
        info = datos['credenciales']
        if datos['agente_id'] != str(UUID(str(uid))):
            raise ValueError()
        if info.get('token_uri') != 'https://oauth2.googleapis.com/token':
            raise ValueError()
        if not set(SCOPES).issubset(set(info.get('scopes') or [])):
            raise ValueError()
        creds = Credentials.from_authorized_user_info(info, SCOPES)
        if not creds.valid:
            if not creds.refresh_token:
                raise ValueError()
            transporte = Request()
            def pedir(*args, **kwargs):
                kwargs['timeout'] = 25
                return transporte(*args, **kwargs)
            creds.refresh(pedir)
            guardar_token(uid, json.loads(creds.to_json()))
        return creds
    except FileNotFoundError as exc:
        raise ErrorCalendario('Primero conecta tu cuenta de Google siguiendo el LEEME.') from exc
    except Exception as exc:
        raise ErrorCalendario('No se pudo renovar el acceso a Google. Vuelve a ejecutar conectar_calendario.py para esta cuenta de agente.') from exc


def crear_borrador(titulo, dia, hora, duracion, zona, correo, meet, aviso, nota, uid, nonce):
    titulo = str(titulo).strip()
    if not titulo or len(titulo) > 200 or not 5 <= int(duracion) <= 480:
        raise ErrorCalendario('Completa el título y una duración entre 5 y 480 minutos.')
    try:
        tz = ZoneInfo(zona)
        inicio = datetime.combine(dia, hora).replace(tzinfo=tz)
        # Detecta horas inexistentes o ambiguas durante cambios de horario.
        if inicio.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != inicio.replace(tzinfo=None):
            raise ValueError()
        if inicio.replace(fold=0).utcoffset() != inicio.replace(fold=1).utcoffset():
            raise ValueError()
    except (ValueError, KeyError) as exc:
        raise ErrorCalendario('La hora no es válida o es ambigua en esa zona. Elige otra hora.') from exc
    if inicio <= datetime.now(timezone.utc):
        raise ErrorCalendario('La cita debe comenzar en el futuro.')
    correo = str(correo or '').strip()
    if correo and not re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+', correo):
        raise ErrorCalendario('Revisa el correo del invitado; se admite un destinatario por cita.')
    if int(aviso) not in [0, 10, 15, 30, 60, 1440]:
        raise ErrorCalendario('Elige un recordatorio admitido.')
    event_id = hashlib.sha256((str(UUID(str(uid))) + ':' + str(nonce)).encode()).hexdigest()
    # La duración representa minutos transcurridos, incluso al cruzar cambios horarios.
    fin = (inicio.astimezone(timezone.utc) + timedelta(minutes=int(duracion))).astimezone(tz)
    body = dict(id=event_id, summary=titulo, description=str(nota or '').strip()[:2000],
                start=dict(dateTime=inicio.isoformat(), timeZone=zona),
                end=dict(dateTime=fin.isoformat(), timeZone=zona),
                reminders=dict(useDefault=False, overrides=[dict(method='popup', minutes=int(aviso))]),
                guestsCanModify=False, guestsCanInviteOthers=False)
    if correo:
        body['attendees'] = [dict(email=correo)]
    if meet:
        body['conferenceData'] = dict(createRequest=dict(requestId=event_id,
                                  conferenceSolutionKey=dict(type='hangoutsMeet')))
    return body


class CalendarioGoogle:
    def __init__(self, uid):
        self.uid = str(UUID(str(uid)))
        self.creds = credenciales(self.uid)

    def pedir(self, metodo, ruta, *, params=None, body=None, admitir_404=False):
        # Solo rutas internas constantes; nunca una URL recibida de un evento.
        try:
            r = requests.request(metodo, 'https://www.googleapis.com/calendar/v3/' + ruta,
                                 headers={'Authorization': 'Bearer ' + self.creds.token},
                                 params=params, json=body, timeout=25)
        except requests.RequestException as exc:
            raise ErrorCalendario('Google no respondió. Si estabas creando una cita, conserva el borrador y usa Reintentar; no prepares otra copia.') from exc
        if r.status_code == 404 and admitir_404:
            return None
        if r.status_code not in (200, 201):
            mensajes = {401:'El acceso caducó. Vuelve a conectar Google.',
                        403:'Google no permitió la operación. Revisa API habilitada, permisos y disponibilidad de Meet.',
                        409:'Este identificador ya existe. Pulsa Reintentar para recuperar la cita existente.',
                        429:'Google limitó las solicitudes. Espera un momento y reintenta el mismo borrador.'}
            raise ErrorCalendario(mensajes.get(r.status_code, 'No se confirmó la operación en Google. Reintenta el mismo borrador para evitar duplicados.'))
        return r.json()

    def calendario(self):
        return self.pedir('GET', 'calendars/primary')

    def evento(self, event_id):
        if not re.fullmatch('[a-f0-9]{64}', event_id):
            raise ErrorCalendario('Identificador de cita no válido.')
        return self.pedir('GET', 'calendars/primary/events/' + event_id, admitir_404=True)

    def listar(self, inicio, fin):
        resultado, token = [], None
        for _ in range(10):
            params = dict(timeMin=inicio.isoformat(), timeMax=fin.isoformat(), singleEvents='true',
                          orderBy='startTime', maxResults=250)
            if token:
                params['pageToken'] = token
            pagina = self.pedir('GET', 'calendars/primary/events', params=params)
            resultado.extend(e for e in pagina.get('items', []) if e.get('status') != 'cancelled')
            token = pagina.get('nextPageToken')
            if not token:
                return resultado
        raise ErrorCalendario('Hay demasiadas citas para revisar ese intervalo. Reduce el rango de fechas.')

    def crear(self, body):
        # Reintentar el mismo borrador recupera el evento antes de repetir un envío.
        existente = self.evento(body['id'])
        if existente:
            if existente.get('status') == 'cancelled':
                raise ErrorCalendario('La cita de este borrador fue cancelada en Google. Prepara otra cita si necesitas volver a crearla.')
            return existente
        if datetime.fromisoformat(body['start']['dateTime']) <= datetime.now(timezone.utc):
            raise ErrorCalendario('La hora del borrador ya pasó. Descártalo y prepara una nueva cita.')
        if self.listar(datetime.fromisoformat(body['start']['dateTime']), datetime.fromisoformat(body['end']['dateTime'])):
            raise ErrorCalendario('Ya hay otra cita en ese horario. Revisa la agenda y prepara un horario distinto. También se consideran los eventos de día completo.')
        return self.pedir('POST', 'calendars/primary/events',
                          params=dict(conferenceDataVersion=1, sendUpdates='all'), body=body)


def enlace_meet(evento):
    enlace = evento.get('hangoutLink', '')
    if enlace.startswith('https://meet.google.com/'):
        return enlace
    for entrada in evento.get('conferenceData', {}).get('entryPoints', []):
        if entrada.get('entryPointType') == 'video' and entrada.get('uri', '').startswith('https://meet.google.com/'):
            return entrada['uri']
    return None
