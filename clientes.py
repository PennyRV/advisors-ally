"""Clientes, pólizas y adjuntos privados. Usa la sesión Supabase existente."""
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
import hashlib
import re
from uuid import UUID
from almacen_documentos import AlmacenDocumentos, ErrorDocumentos, usuario_validado, TIPOS

TIPOS_POLIZA = ['GMM', 'Vida', 'PPR', 'Auto', 'Hogar', 'Otro']
FORMAS = ['Tarjeta', 'Domiciliación', 'Transferencia', 'Depósito', 'Otro']
FRECUENCIAS = ['Mensual', 'Trimestral', 'Semestral', 'Anual', 'Pago único', 'Otra']


class ErrorClientes(Exception):
    pass


def texto(valor, limite):
    return str(valor or '').strip()[:limite]


def identificador(valor):
    try:
        return str(UUID(str(valor)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ErrorClientes('Selecciona un registro válido.') from exc


def fecha(valor):
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor)).isoformat()
    except ValueError as exc:
        raise ErrorClientes('Revisa las fechas.') from exc


def datos_cliente(datos):
    nombre = texto(datos.get('nombre'), 200)
    rfc = texto(datos.get('rfc'), 13).upper()
    correo = texto(datos.get('correo'), 254)
    nacimiento = fecha(datos.get('fecha_nacimiento'))
    if not nombre:
        raise ErrorClientes('Escribe el nombre del cliente.')
    if rfc and not re.fullmatch(r'[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}', rfc):
        raise ErrorClientes('El RFC debe tener 12 o 13 caracteres con su formato habitual.')
    if correo and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', correo):
        raise ErrorClientes('Revisa el correo electrónico.')
    if nacimiento and nacimiento > date.today().isoformat():
        raise ErrorClientes('La fecha de nacimiento no puede estar en el futuro.')
    return dict(nombre=nombre, rfc=rfc or None, fecha_nacimiento=nacimiento,
                telefono=texto(datos.get('telefono'), 40), correo=correo,
                comentarios=texto(datos.get('comentarios'), 5000))


def datos_poliza(datos):
    numero, compania = texto(datos.get('numero'), 100), texto(datos.get('compania'), 200)
    if not numero or not compania:
        raise ErrorClientes('Completa el número de póliza y la compañía.')
    emision, vencimiento = fecha(datos.get('fecha_emision')), fecha(datos.get('fecha_vencimiento'))
    if not emision or not vencimiento or vencimiento < emision:
        raise ErrorClientes('Completa las fechas; el vencimiento no puede ser anterior a la emisión.')
    try:
        suma = Decimal(str(datos.get('suma_asegurada'))) if datos.get('suma_asegurada') not in (None, '') else None
        if suma is not None and (not suma.is_finite() or suma < 0 or suma > Decimal('9999999999999999.99')):
            raise InvalidOperation
        if suma is not None and suma != suma.quantize(Decimal('.01')):
            raise InvalidOperation
    except (InvalidOperation, ValueError) as exc:
        raise ErrorClientes('La suma asegurada debe ser positiva o cero, con máximo dos decimales.') from exc
    moneda = texto(datos.get('moneda'), 3).upper()
    if moneda not in ['MXN', 'USD', 'EUR', 'UDI']:
        raise ErrorClientes('Selecciona una moneda válida.')
    return dict(numero=numero, compania=compania, fecha_emision=emision,
                fecha_vencimiento=vencimiento, tipo=texto(datos.get('tipo'), 100),
                suma_asegurada=str(suma) if suma is not None else None, moneda=moneda,
                forma_pago=texto(datos.get('forma_pago'), 100),
                frecuencia_pago=texto(datos.get('frecuencia_pago'), 100),
                comentarios=texto(datos.get('comentarios'), 5000))


class AgendaClientes:
    def __init__(self, cliente):
        self.cliente = cliente
        self.uid = str(usuario_validado(cliente).id)
        self.almacen = AlmacenDocumentos(cliente)

    def _ejecutar(self, consulta):
        try:
            return consulta.execute().data or []
        except Exception as exc:
            codigo = str(getattr(exc, 'code', ''))
            if codigo in ('42P01', 'PGRST205'):
                mensaje = 'Falta preparar las tablas. Ejecuta clientes_supabase.sql en el SQL Editor de tu proyecto.'
            elif codigo == '23505':
                mensaje = 'Este registro o archivo ya existe. Revisa la lista antes de repetirlo.'
            elif codigo in ('42501', '23503'):
                mensaje = 'No se pudo guardar: revisa tu sesión, los permisos y el cliente seleccionado.'
            else:
                mensaje = 'No se pudo completar la operación. Revisa la conexión y la configuración de las tablas.'
            raise ErrorClientes(mensaje) from exc

    def listar(self, tabla, **filtros):
        # Solo estas tablas; todas las páginas mantienen el filtro de propietario.
        if tabla not in ['aa_clientes', 'aa_polizas', 'aa_adjuntos']:
            raise ErrorClientes('Consulta no admitida.')
        resultado, inicio = [], 0
        while True:
            q = self.cliente.table(tabla).select('*').eq('agente_id', self.uid)
            for campo, valor in filtros.items():
                if campo not in ['cliente_id', 'poliza_id', 'id']:
                    raise ErrorClientes('Filtro no admitido.')
                q = q.eq(campo, identificador(valor))
            lote = self._ejecutar(q.order('id').range(inicio, inicio + 199))
            resultado.extend(lote)
            if len(lote) < 200:
                return resultado
            inicio += 200

    def _guardar(self, tabla, datos, registro_id=None):
        q = self.cliente.table(tabla)
        if registro_id:
            filas = self._ejecutar(q.update(datos).eq('id', identificador(registro_id)).eq('agente_id', self.uid))
        else:
            filas = self._ejecutar(q.insert(dict(datos, agente_id=self.uid)))
        if not filas:
            raise ErrorClientes('No se guardó ningún registro. Actualiza la lista y comprueba tu sesión.')
        return filas[0]

    def guardar_cliente(self, datos, registro_id=None):
        return self._guardar('aa_clientes', datos_cliente(datos), registro_id)

    def guardar_poliza(self, cliente_id, datos, registro_id=None):
        valores = datos_poliza(datos)
        valores['cliente_id'] = identificador(cliente_id)
        return self._guardar('aa_polizas', valores, registro_id)

    def adjuntar(self, cliente_id, poliza_id, archivo):
        cliente_id, poliza_id = identificador(cliente_id), identificador(poliza_id)
        # Verifica la relación ANTES de subir bytes; RLS y FK repiten la comprobación.
        polizas = self.listar('aa_polizas', cliente_id=cliente_id, id=poliza_id)
        if not polizas:
            raise ErrorClientes('La póliza no pertenece al cliente de tu cuenta.')
        contenido = archivo.getvalue()
        huella = hashlib.sha256(contenido).hexdigest()
        anteriores = self.listar('aa_adjuntos', poliza_id=poliza_id)
        if any(a['sha256'] == huella for a in anteriores):
            return False
        ruta = self.almacen.guardar(archivo.name, contenido)
        try:
            self._guardar('aa_adjuntos', dict(cliente_id=cliente_id, poliza_id=poliza_id,
                ruta=ruta, nombre=PurePosixPath(ruta).name.split('__', 1)[-1],
                sha256=huella, tamano=len(contenido)))
        except ErrorClientes as exc:
            # Storage y Postgres no comparten transacción. Compensación ante fallo.
            try:
                self.almacen.eliminar(ruta)
            except ErrorDocumentos:
                raise ErrorClientes('No se vinculó el archivo y no pudimos retirar la copia. Revisa Mis documentos antes de repetir la carga.') from exc
            raise
        return True


def _fecha(valor):
    return date.fromisoformat(valor) if valor else None


def formulario_cliente(registro=None):
    import streamlit as st
    r = registro or {}
    with st.form('crm_cliente_' + r.get('id', 'nuevo')):
        nombre = st.text_input('Nombre completo *', value=r.get('nombre', ''), max_chars=200)
        rfc = st.text_input('RFC', value=r.get('rfc') or '', max_chars=13)
        nacimiento = st.date_input('Fecha de nacimiento / cumpleaños', value=_fecha(r.get('fecha_nacimiento')),
                                    min_value=date(1900, 1, 1), max_value=date.today(), format='DD/MM/YYYY')
        telefono = st.text_input('Número de teléfono (incluye lada internacional)', value=r.get('telefono', ''), max_chars=40)
        correo = st.text_input('Correo electrónico', value=r.get('correo', ''), max_chars=254)
        comentarios = st.text_area('Comentarios del cliente', value=r.get('comentarios', ''), max_chars=5000)
        enviar = st.form_submit_button('Guardar cliente', type='primary')
    if enviar:
        return dict(nombre=nombre, rfc=rfc, fecha_nacimiento=nacimiento, telefono=telefono,
                    correo=correo, comentarios=comentarios)
    return None


def _opcion(st, etiqueta, opciones, actual):
    opciones = list(opciones)
    if actual and actual not in opciones:
        opciones.append(actual)
    return st.selectbox(etiqueta, opciones, index=opciones.index(actual) if actual in opciones else 0)


def formulario_poliza(cliente_id, registro=None):
    import streamlit as st
    r = registro or {}
    with st.form('crm_poliza_' + cliente_id + '_' + r.get('id', 'nueva')):
        numero = st.text_input('Número de póliza *', value=r.get('numero', ''), max_chars=100)
        compania = st.text_input('Compañía aseguradora *', value=r.get('compania', ''), max_chars=200)
        emision = st.date_input('Fecha de emisión *', value=_fecha(r.get('fecha_emision')), min_value=date(1900, 1, 1), max_value=date(2200, 12, 31), format='DD/MM/YYYY')
        vencimiento = st.date_input('Fecha de vencimiento *', value=_fecha(r.get('fecha_vencimiento')), min_value=date(1900, 1, 1), max_value=date(2200, 12, 31), format='DD/MM/YYYY')
        tipo = _opcion(st, 'Tipo de póliza', TIPOS_POLIZA, r.get('tipo'))
        suma = st.text_input('Suma asegurada (opcional; ejemplo: 3000000.00)', value=str(r['suma_asegurada']) if r.get('suma_asegurada') is not None else '')
        moneda = _opcion(st, 'Moneda / unidad', ['MXN', 'USD', 'EUR', 'UDI'], r.get('moneda', 'MXN'))
        forma = _opcion(st, 'Tipo / forma de pago', FORMAS, r.get('forma_pago'))
        frecuencia = _opcion(st, 'Frecuencia de pago', FRECUENCIAS, r.get('frecuencia_pago'))
        comentarios = st.text_area('Comentarios de la póliza', value=r.get('comentarios', ''), max_chars=5000)
        enviar = st.form_submit_button('Guardar póliza', type='primary')
    if enviar:
        return dict(numero=numero, compania=compania, fecha_emision=emision, fecha_vencimiento=vencimiento,
                    tipo=tipo, suma_asegurada=suma, moneda=moneda, forma_pago=forma,
                    frecuencia_pago=frecuencia, comentarios=comentarios)
    return None


def mostrar_clientes(cliente):
    import streamlit as st
    st.header('Clientes')
    if cliente is None:
        st.info('Inicia sesión en Mi cuenta para ver tus clientes y pólizas.')
        return
    try:
        agenda = AgendaClientes(cliente)
        aviso = st.session_state.pop('crm_aviso', None)
        if aviso:
            st.success(aviso)
        st.caption('Cada cliente puede tener varias pólizas. Los datos y archivos se guardan en tu cuenta; este módulo no los envía a Gemini.')
        modo = st.radio('¿Qué quieres hacer con tus clientes?', ['Buscar y editar', 'Agregar cliente'], horizontal=True)
        if modo == 'Agregar cliente':
            datos = formulario_cliente()
            if datos is not None:
                agenda.guardar_cliente(datos)
                st.session_state['crm_aviso'] = 'Cliente guardado. Selecciona Buscar y editar para abrir su ficha.'
                st.rerun()
            return
        clientes = agenda.listar('aa_clientes')
        busqueda = st.text_input('Buscar por nombre, RFC, teléfono o correo').strip().casefold()
        visibles = [c for c in clientes if not busqueda or busqueda in ' '.join(str(c.get(k) or '') for k in ['nombre','rfc','telefono','correo']).casefold()]
        st.caption(f'Clientes encontrados: {len(visibles)} · Total: {len(clientes)}')
        if not visibles:
            st.info('No hay coincidencias. Puedes agregar un cliente desde la opción superior.')
            return
        por_id = {c['id']: c for c in visibles}
        cid = st.selectbox('Selecciona un cliente', list(por_id), format_func=lambda i: f"{por_id[i]['nombre']} · {por_id[i].get('rfc') or i[:8]}", key='crm_cliente_elegido')
        c = por_id[cid]
        st.subheader(c['nombre'])
        with st.expander('Información del cliente', expanded=True):
            datos = formulario_cliente(c)
            if datos is not None:
                agenda.guardar_cliente(datos, cid)
                st.session_state['crm_aviso'] = 'Datos del cliente actualizados.'
                st.rerun()
        polizas = agenda.listar('aa_polizas', cliente_id=cid)
        st.subheader(f'Pólizas del cliente ({len(polizas)})')
        with st.expander('Agregar póliza'):
            datos = formulario_poliza(cid)
            if datos is not None:
                agenda.guardar_poliza(cid, datos)
                st.session_state['crm_aviso'] = 'Póliza guardada. Ya puedes adjuntar sus documentos.'
                st.rerun()
        if not polizas:
            st.info('Este cliente todavía no tiene pólizas registradas.')
            return
        por_poliza = {p['id']: p for p in polizas}
        pid = st.selectbox('Selecciona una póliza', list(por_poliza), format_func=lambda i: f"{por_poliza[i]['compania']} · {por_poliza[i]['numero']} · {por_poliza[i]['tipo']}", key='crm_poliza_' + cid)
        with st.expander('Información de la póliza'):
            datos = formulario_poliza(cid, por_poliza[pid])
            if datos is not None:
                agenda.guardar_poliza(cid, datos, pid)
                st.session_state['crm_aviso'] = 'Póliza actualizada.'
                st.rerun()
        st.subheader('Documentos de esta póliza')
        st.caption('PDF, Word, JPG y PNG · máximo 50 MB por archivo. Selecciona Guardar documentos para conservarlos.')
        version = st.session_state.get('crm_subida_version', 0)
        archivos = st.file_uploader('Adjuntar póliza, condiciones o endosos', type=['pdf','doc','docx','jpg','jpeg','png'], accept_multiple_files=True, key=f'crm_subir_{pid}_{version}')
        if st.button('Guardar documentos', type='primary', disabled=not archivos):
            fallos = False
            for a in archivos or []:
                try:
                    nuevo = agenda.adjuntar(cid, pid, a)
                    st.success(f'{a.name}: guardado.' if nuevo else f'{a.name}: ya estaba vinculado a esta póliza.')
                except (ErrorClientes, ErrorDocumentos) as exc:
                    fallos = True
                    st.error(str(exc))
            if not fallos:
                st.session_state['crm_subida_version'] = version + 1
                st.session_state['crm_aviso'] = 'Documentos guardados y vinculados a la póliza.'
                st.rerun()
        adjuntos = agenda.listar('aa_adjuntos', poliza_id=pid)
        if not adjuntos:
            st.info('Aún no hay documentos vinculados a esta póliza.')
        for a in adjuntos:
            st.write(f"{a['nombre']} · {a['tamano'] / 1048576:.2f} MB")
            if st.button('Preparar descarga', key='crm_descargar_' + a['id']):
                st.session_state['crm_descarga'] = dict(uid=agenda.uid, id=a['id'], datos=agenda.almacen.descargar(a['ruta']))
            descarga = st.session_state.get('crm_descarga')
            if descarga and descarga['uid'] == agenda.uid and descarga['id'] == a['id']:
                st.download_button('Descargar archivo', data=descarga['datos'], file_name=a['nombre'], mime=TIPOS.get(PurePosixPath(a['nombre']).suffix.lower(), 'application/octet-stream'), key='crm_bajar_' + a['id'])
        st.caption('Los PDF se buscan automáticamente desde Consultar documentos; no necesitas seleccionarlos otra vez. Word e imágenes se almacenan y descargan; aún no se interpretan con IA. Los recordatorios y WhatsApp se integrarán después.')
    except (ErrorClientes, ErrorDocumentos) as exc:
        st.error(str(exc))
    except Exception:
        st.error('No pudimos cargar Clientes. Revisa tu sesión y la conexión. No compartas contraseñas ni claves.')
