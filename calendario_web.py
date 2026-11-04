"""OAuth web de Google: PKCE, estado de un uso, tokens cifrados y RLS por agente."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import secrets
from types import SimpleNamespace
from urllib.parse import urlencode, urlparse
from cryptography.fernet import Fernet, InvalidToken
import requests
from almacen_documentos import usuario_validado, ErrorDocumentos
from calendario_google import CalendarioGoogle, ErrorCalendario, SCOPES

TOKEN_URL = 'https://oauth2.googleapis.com/token'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_TABLE = 'aa_google_tokens'
STATE_TABLE = 'aa_google_oauth'


def ahora():
    return datetime.now(timezone.utc)


def sha(valor):
    return hashlib.sha256(valor.encode()).hexdigest()


def configuracion():
    import streamlit as st
    try:
        cfg = dict(st.secrets['google_calendar'])
        required = ['client_id', 'client_secret', 'redirect_uri', 'token_key']
        if any(not isinstance(cfg.get(k), str) or not cfg[k].strip() for k in required):
            raise ValueError()
        for k in required:
            cfg[k] = cfg[k].strip()
        uri = urlparse(cfg['redirect_uri'])
        if uri.scheme != 'https' or not uri.netloc or uri.query or uri.fragment or uri.username:
            raise ValueError()
        if not cfg['client_id'].endswith('.apps.googleusercontent.com'):
            raise ValueError()
        Fernet(cfg['token_key'].encode())
        return cfg
    except (KeyError, FileNotFoundError, ValueError, TypeError) as exc:
        raise ErrorCalendario('Falta completar [google_calendar] en Secrets de Streamlit. Revisa el LEEME de calendario web.') from exc


class ConexionGoogleWeb:
    def __init__(self, cliente, cfg=None):
        self.cliente = cliente
        self.uid = str(usuario_validado(cliente).id)
        self.cfg = cfg if cfg is not None else configuracion()
        try:
            self.fernet = Fernet(self.cfg['token_key'].encode())
        except Exception as exc:
            raise ErrorCalendario('Revisa token_key en Secrets; no la regeneres si ya guardaste conexiones.') from exc

    def sellar(self, tipo, datos):
        sobre = dict(agente_id=self.uid, tipo=tipo, client_id=self.cfg['client_id'], datos=datos)
        return self.fernet.encrypt(json.dumps(sobre).encode()).decode()

    def abrir(self, tipo, cifrado):
        try:
            sobre = json.loads(self.fernet.decrypt(cifrado.encode()))
            if sobre['agente_id'] != self.uid or sobre['tipo'] != tipo or sobre['client_id'] != self.cfg['client_id']:
                raise ValueError()
            return sobre['datos']
        except (InvalidToken, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ErrorCalendario('No se pudo leer esta conexión. Comprueba la clave de cifrado original o vuelve a conectar Google.') from exc

    def ejecutar(self, consulta):
        try:
            return consulta.execute().data or []
        except Exception as exc:
            if getattr(exc, 'code', '') in ('42P01', 'PGRST205'):
                raise ErrorCalendario('Faltan las tablas del calendario web. Ejecuta calendario_web.sql en Supabase.') from exc
            raise ErrorCalendario('No se pudo guardar o consultar la conexión privada. Revisa la sesión y los permisos de las tablas.') from exc

    def iniciar(self):
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        expira = (ahora() + timedelta(minutes=10)).isoformat()
        fila = dict(agente_id=self.uid, estado_sha256=sha(state),
                    verificador_cifrado=self.sellar('oauth', dict(verifier=verifier, redirect_uri=self.cfg['redirect_uri'])),
                    expira_en=expira)
        self.ejecutar(self.cliente.table(STATE_TABLE).upsert(fila, on_conflict='agente_id'))
        return AUTH_URL + '?' + urlencode(dict(client_id=self.cfg['client_id'], redirect_uri=self.cfg['redirect_uri'],
            response_type='code', scope=' '.join(SCOPES), access_type='offline', prompt='consent select_account',
            state=state, code_challenge=challenge, code_challenge_method='S256'))

    def consumir_estado(self, state):
        if not isinstance(state, str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,100}', state):
            raise ErrorCalendario('La autorización no es válida. Vuelve a iniciar la conexión desde Calendario.')
        # DELETE RETURNING consume una vez incluso con dos retornos simultáneos.
        filas = self.ejecutar(self.cliente.table(STATE_TABLE).delete(returning='representation')
            .eq('agente_id', self.uid).eq('estado_sha256', sha(state)).gt('expira_en', ahora().isoformat()))
        if len(filas) != 1:
            raise ErrorCalendario('La autorización caducó, ya se utilizó o corresponde a otra cuenta de agente. Vuelve a conectar desde Calendario.')
        datos = self.abrir('oauth', filas[0]['verificador_cifrado'])
        if datos['redirect_uri'] != self.cfg['redirect_uri']:
            raise ErrorCalendario('Cambió la dirección de retorno. Inicia nuevamente la conexión.')
        return datos

    def pedir_token(self, datos):
        try:
            r = requests.post(TOKEN_URL, data=dict(datos, client_id=self.cfg['client_id'], client_secret=self.cfg['client_secret']), timeout=25)
            if r.status_code != 200:
                raise ValueError()
            token = r.json()
            if not token.get('access_token') or str(token.get('token_type', '')).lower() != 'bearer':
                raise ValueError()
            return token
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise ErrorCalendario('Google no confirmó el acceso. Vuelve a conectar y revisa el cliente web, su secreto y la dirección de retorno.') from exc

    def guardar(self, token):
        self.ejecutar(self.cliente.table(TOKEN_TABLE).upsert(dict(agente_id=self.uid,
            token_cifrado=self.sellar('token', token), actualizado_en=ahora().isoformat()), on_conflict='agente_id'))

    def terminar(self, state, code, error=None):
        datos = self.consumir_estado(state)
        if error:
            raise ErrorCalendario('No se autorizó Google Calendar. Puedes volver a conectar cuando quieras.')
        if not isinstance(code, str) or not 1 <= len(code) <= 4096:
            raise ErrorCalendario('Google no devolvió un código válido. Inicia nuevamente la conexión.')
        token = self.pedir_token(dict(grant_type='authorization_code', code=code, code_verifier=datos['verifier'], redirect_uri=datos['redirect_uri']))
        if not set(SCOPES).issubset(set(str(token.get('scope', '')).split())) or not token.get('refresh_token'):
            raise ErrorCalendario('Faltan permisos o el acceso para reconectar. Vuelve a conectar y concede los dos permisos del calendario.')
        try:
            expira = int(token['expires_in'])
            if not 0 < expira <= 86400:
                raise ValueError()
        except (KeyError, ValueError, TypeError) as exc:
            raise ErrorCalendario('Google devolvió una vigencia no válida. Inicia nuevamente la conexión.') from exc
        self.guardar(dict(access_token=token['access_token'], refresh_token=token['refresh_token'],
                          expires_at=(ahora() + timedelta(seconds=expira)).isoformat(), scope=token['scope']))

    def credencial(self):
        filas = self.ejecutar(self.cliente.table(TOKEN_TABLE).select('*').eq('agente_id', self.uid))
        if len(filas) != 1:
            raise ErrorCalendario('Conecta tu cuenta con el botón Conectar con Google.')
        token = self.abrir('token', filas[0]['token_cifrado'])
        try:
            caduca = datetime.fromisoformat(token['expires_at'])
            if not caduca.tzinfo:
                raise ValueError()
        except (KeyError, ValueError, TypeError) as exc:
            raise ErrorCalendario('La conexión guardada no es válida. Vuelve a conectar Google.') from exc
        if not set(SCOPES).issubset(set(str(token.get('scope', '')).split())):
            raise ErrorCalendario('La conexión no tiene los permisos de calendario. Vuelve a conectar.')
        if caduca <= ahora() + timedelta(seconds=90):
            nuevo = self.pedir_token(dict(grant_type='refresh_token', refresh_token=token['refresh_token']))
            try:
                vigencia = int(nuevo['expires_in'])
                if not 0 < vigencia <= 86400:
                    raise ValueError()
            except (KeyError, ValueError, TypeError) as exc:
                raise ErrorCalendario('No se pudo renovar el acceso. Vuelve a conectar Google.') from exc
            token.update(access_token=nuevo['access_token'], expires_at=(ahora() + timedelta(seconds=vigencia)).isoformat())
            if nuevo.get('refresh_token'):
                token['refresh_token'] = nuevo['refresh_token']
            self.guardar(token)
        return SimpleNamespace(token=token['access_token'])

    def desconectar(self):
        # Revoca primero en Google. Si no confirma, mantiene el registro para reintentar.
        filas = self.ejecutar(self.cliente.table(TOKEN_TABLE).select('*').eq('agente_id', self.uid))
        if filas:
            token = self.abrir('token', filas[0]['token_cifrado'])
            try:
                r = requests.post('https://oauth2.googleapis.com/revoke', data={'token':token['refresh_token']}, timeout=25)
                if r.status_code not in (200, 400):
                    raise ValueError()
                if r.status_code == 400 and r.json().get('error') != 'invalid_token':
                    raise ValueError()
            except (requests.RequestException, KeyError, ValueError) as exc:
                raise ErrorCalendario('Google no confirmó la revocación. Reintenta o revoca el acceso desde tu cuenta de Google.') from exc
        for tabla in [TOKEN_TABLE, STATE_TABLE]:
            self.ejecutar(self.cliente.table(tabla).delete().eq('agente_id', self.uid))


class CalendarioGoogleWeb(CalendarioGoogle):
    def __init__(self, cliente):
        conexion = ConexionGoogleWeb(cliente)
        self.uid = conexion.uid
        self.creds = conexion.credencial()


def procesar_retorno_google(cliente):
    """Se ejecuta en app.py después del acceso, aunque la sección activa sea Inicio."""
    import streamlit as st
    params = st.query_params
    if not ('state' in params and ('code' in params or 'error' in params)):
        return
    if cliente is None:
        st.info('Google te devolvió a la app. Inicia sesión con la misma cuenta de agente para completar la conexión y después abre Calendario.')
        return
    try:
        for clave in ['state', 'code', 'error']:
            if len(params.get_all(clave)) > 1:
                raise ErrorCalendario('El retorno tiene parámetros repetidos. Inicia nuevamente la conexión.')
        ConexionGoogleWeb(cliente).terminar(params.get('state'), params.get('code'), params.get('error'))
        aviso = ('success', 'Google Calendar quedó conectado. Abre Calendario en el menú izquierdo.')
    except (ErrorCalendario, ErrorDocumentos) as exc:
        aviso = ('warning', str(exc))
    except Exception:
        aviso = ('warning', 'No se completó la conexión. Vuelve a conectar desde Calendario.')
    # Retira código y estado de la URL, sin borrar otros parámetros de la aplicación.
    for clave in ['code', 'state', 'scope', 'authuser', 'prompt', 'error', 'error_description']:
        if clave in params:
            del params[clave]
    st.session_state['cal_web_aviso'] = aviso
    st.rerun()
