"""Busca PDF privados guardados sin volver a subir ni seleccionar archivos."""
from collections import defaultdict
from io import BytesIO
from pathlib import PurePosixPath
import re
import unicodedata
from pypdf import PdfReader
from almacen_documentos import AlmacenDocumentos, usuario_validado, ErrorDocumentos
from clientes import AgendaClientes, ErrorClientes
from ia_documentos import buscar_fragmentos, responder_documentos

MAX_PDFS = 40
MAX_TOTAL_BYTES = 150 * 1024 * 1024
MAX_PAGINAS = 2000


class ErrorConsulta(Exception):
    pass


def normalizar(texto):
    s = unicodedata.normalize('NFKD', str(texto or '').casefold())
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in s if not unicodedata.combining(c))).split())


def contiene(pregunta, termino):
    return bool(termino) and f' {termino} ' in f' {pregunta} '


def resolver_ambito(pregunta, clientes, polizas, archivos):
    """No elige una póliza por similitud: ante varios casos pide precisar en texto."""
    q = normalizar(pregunta)
    por_cliente = {c['id']: c for c in clientes}
    vinculados = {a['poliza_id'] for a in archivos if a.get('poliza_id')}
    numeros = [p for p in polizas if contiene(q, normalizar(p['numero'])) and (
        len(normalizar(p['numero']).replace(' ', '')) >= 3
        or contiene(q, 'poliza ' + normalizar(p['numero'])))]
    nombres = [c for c in clientes if contiene(q, normalizar(c['nombre']))]
    # Si un nombre es prefijo de otro, conserva la coincidencia más específica.
    if nombres:
        nombres = [c for c in nombres if not any(
            c['id'] != otro['id'] and normalizar(c['nombre']) != normalizar(otro['nombre'])
            and contiene(normalizar(otro['nombre']), normalizar(c['nombre']))
            for otro in nombres)]
    else:
        nombres = [c for c in clientes if len(normalizar(c['nombre']).split()[0]) >= 3
                   and contiene(q, normalizar(c['nombre']).split()[0])]
    referencia = re.search(r'poliza\s+(?:numero\s+)?([a-z0-9][a-z0-9_-]*)', normalizar(pregunta))
    if referencia and not numeros and referencia[1] not in {
        'de', 'del', 'que', 'gmm', 'vida', 'ppr', 'tengo', 'tiene', 'es', 'anual',
        'para', 'vigente', 'contratada', 'familiar', 'individual', 'basica'}:
        raise ErrorConsulta('No encontré ese número de póliza. Revisa el número registrado en Clientes.')
    generales = [a for a in archivos if not a.get('poliza_id')]
    definicion = any(contiene(q, frase) for frase in ['que es', 'que significa', 'definicion de', 'define'])
    if definicion and not numeros and not nombres and not contiene(q, 'cliente') and generales:
        return generales, 'Consulta general en documentos sin vincular. No acredita la cobertura de un cliente.'
    if numeros:
        candidatos = numeros
        if nombres:
            ids = {c['id'] for c in nombres}
            candidatos = [p for p in candidatos if p['cliente_id'] in ids]
            if not candidatos:
                raise ErrorConsulta('El cliente y el número de póliza de la pregunta no coinciden. Revisa ambos datos.')
    elif nombres:
        ids = {c['id'] for c in nombres}
        candidatos = [p for p in polizas if p['cliente_id'] in ids]
    elif contiene(q, 'cliente'):
        raise ErrorConsulta('No identifiqué al cliente. Incluye su nombre registrado o el número de póliza en la pregunta.')
    else:
        candidatos = [p for p in polizas if p['id'] in vinculados]
    # Un tipo o compañía expresos pueden desambiguar las pólizas de ese cliente.
    tipos = [normalizar(p.get('tipo')) for p in candidatos if contiene(q, normalizar(p.get('tipo')))]
    if tipos:
        candidatos = [p for p in candidatos if normalizar(p.get('tipo')) in tipos]
    companias = [normalizar(p.get('compania')) for p in candidatos if contiene(q, normalizar(p.get('compania')))]
    if companias:
        candidatos = [p for p in candidatos if normalizar(p.get('compania')) in companias]
    if len(candidatos) > 1:
        raise ErrorConsulta('Hay varias pólizas que podrían corresponder. Incluye el nombre completo del cliente y el número de póliza en tu pregunta; no necesitas seleccionar archivos.')
    if candidatos:
        p = candidatos[0]
        elegidos = [a for a in archivos if a.get('poliza_id') == p['id']]
        if not elegidos:
            raise ErrorConsulta('La póliza está registrada, pero no tiene PDF vinculados. Adjunta sus documentos una sola vez desde Clientes.')
        c = por_cliente.get(p['cliente_id'], {})
        etiqueta = f"{c.get('nombre', 'Cliente')} · {p['compania']} · Póliza {p['numero']}"
        return elegidos, etiqueta
    if nombres or numeros:
        raise ErrorConsulta('No encontré una póliza con PDF vinculados para ese cliente. Revisa su ficha en Clientes.')
    generales = [a for a in archivos if not a.get('poliza_id')]
    if not generales:
        raise ErrorConsulta('No hay PDF guardados con los que responder. Guarda los archivos una sola vez en Clientes o Mis documentos.')
    return generales, 'Documentos sin vincular a una póliza: la respuesta no acredita cobertura de un cliente.'


def catalogo_guardado(cliente):
    agenda = AgendaClientes(cliente)
    clientes = agenda.listar('aa_clientes')
    polizas = agenda.listar('aa_polizas')
    adjuntos = agenda.listar('aa_adjuntos')
    por_ruta = {a['ruta']: a for a in adjuntos}
    pdfs = []
    omitidos = 0
    for a in AlmacenDocumentos(cliente).listar():
        if not a['nombre'].lower().endswith('.pdf'):
            omitidos += 1
            continue
        vinculo = por_ruta.get(a['ruta'], {})
        pdfs.append(dict(a, poliza_id=vinculo.get('poliza_id'), sha256=vinculo.get('sha256')))
    return clientes, polizas, pdfs, omitidos


def recuperar_paginas(cliente, archivos, cache):
    """Cache de texto por sesión/usuario; el catálogo remoto se renueva cada pregunta."""
    uid = str(usuario_validado(cliente).id)
    if cache.get('uid') != uid:
        cache.clear()
        cache.update(uid=uid, textos={})
    if len(archivos) > MAX_PDFS:
        raise ErrorConsulta('Hay más de 40 PDF en ese ámbito. Incluye el cliente y la póliza en la pregunta para limitar la búsqueda.')
    total = sum(int(a.get('tamano') or 0) for a in archivos)
    if total > MAX_TOTAL_BYTES:
        raise ErrorConsulta('Los PDF de esta consulta superan 150 MB. Precisa la póliza o utiliza copias más pequeñas.')
    almacen = AlmacenDocumentos(cliente)
    paginas, avisos = [], []
    paginas_fisicas = 0
    for a in archivos:
        ruta = str(a['ruta'])
        if not ruta.startswith(uid + '/'):
            raise ErrorConsulta('Un archivo no pertenece a tu sesión. Vuelve a iniciar sesión.')
        firma = (ruta, a.get('sha256'), a.get('fecha'), a.get('tamano'))
        item = cache['textos'].get(firma)
        if item is None:
            try:
                contenido = almacen.descargar(ruta)
                if len(contenido) + total - int(a.get('tamano') or 0) > MAX_TOTAL_BYTES:
                    raise ErrorConsulta('El tamaño real supera el límite de esta consulta. Precisa la póliza.')
                lector = PdfReader(BytesIO(contenido))
                if lector.is_encrypted:
                    avisos.append(f"{a['nombre']}: PDF protegido con contraseña; no se pudo consultar.")
                    continue
                n = len(lector.pages)
                if paginas_fisicas + n > MAX_PAGINAS:
                    raise ErrorConsulta('La consulta supera 2,000 páginas. Precisa la póliza o reduce los documentos del caso.')
                textos, vacias = [], 0
                for numero, pagina in enumerate(lector.pages, 1):
                    texto = (pagina.extract_text(extraction_mode='layout') or '').strip()
                    if not texto:
                        texto = (pagina.extract_text() or '').strip()
                    if texto:
                        textos.append((numero, texto))
                    else:
                        vacias += 1
                item = dict(textos=textos, paginas=n, vacias=vacias)
                cache['textos'][firma] = item
            except (ErrorConsulta, ErrorDocumentos):
                raise
            except Exception:
                avisos.append(f"{a['nombre']}: no pudimos leer este PDF. Revisa el archivo guardado.")
                continue
        paginas_fisicas += item['paginas']
        if paginas_fisicas > MAX_PAGINAS:
            raise ErrorConsulta('La consulta supera 2,000 páginas. Precisa la póliza.')
        # El sufijo permite distinguir PDF homónimos sin exponer el UID.
        doc = f"{PurePosixPath(a['nombre']).stem} · {PurePosixPath(ruta).name.split('__', 1)[0][:8]}.pdf"
        paginas.extend(dict(documento=doc, pagina=n, texto=t) for n,t in item['textos'])
        if item['vacias']:
            avisos.append(f"{a['nombre']}: {item['vacias']} página(s) sin texto. Las imágenes escaneadas necesitan OCR.")
    # Retiene solo el caso actual: evita acumular el texto de todos los clientes.
    firmas_activas = {(str(a['ruta']), a.get('sha256'), a.get('fecha'), a.get('tamano')) for a in archivos}
    cache['textos'] = {k:v for k,v in cache['textos'].items() if k in firmas_activas}
    return paginas, avisos


def mostrar_consulta_automatica(cliente, modelo):
    import streamlit as st
    st.header('Consultar documentos')
    st.write('Escribe tu pregunta. Buscaremos automáticamente en los PDF guardados en tu cuenta, sin volver a subirlos ni seleccionarlos.')
    if cliente is None:
        st.info('Inicia sesión en Mi cuenta para consultar tus documentos guardados.')
        return
    try:
        uid = str(usuario_validado(cliente).id)
    except ErrorDocumentos as exc:
        st.warning(str(exc))
        return
    if st.session_state.get('auto_uid') != uid:
        for k in ['auto_cache', 'auto_resultado']:
            st.session_state.pop(k, None)
        st.session_state['auto_uid'] = uid
    st.caption('Si tienes varios casos, incluye en la pregunta el nombre del cliente y su número de póliza. Se consultará ese caso sin mezclarlo con otros.')
    with st.form('auto_preguntar'):
        pregunta = st.text_input('¿Qué necesitas saber?', placeholder='Ejemplo: ¿Qué deducible tiene la póliza DEMO-001 de Ana Pérez?', max_chars=2000)
        permiso = st.checkbox('Autorizo enviar esta pregunta y los fragmentos recuperados a Gemini. Para estas pruebas uso información sin datos personales ni confidenciales.')
        enviar = st.form_submit_button('Consultar', type='primary')
    st.caption('Sin la autorización se buscan y muestran fuentes localmente. Word y fotos todavía no se interpretan con IA.')
    if enviar:
        st.session_state.pop('auto_resultado', None)
        resultado = dict(pregunta=pregunta.strip(), ambito='', fuentes=[], avisos=[], respuesta=None, error=None, uid=uid)
        try:
            if not pregunta.strip():
                raise ErrorConsulta('Escribe una pregunta.')
            with st.spinner('Buscando en tus documentos guardados...'):
                clientes, polizas, archivos, omitidos = catalogo_guardado(cliente)
                elegidos, ambito = resolver_ambito(pregunta, clientes, polizas, archivos)
                resultado['ambito'] = ambito
                cache = st.session_state.setdefault('auto_cache', {})
                paginas, avisos = recuperar_paginas(cliente, elegidos, cache)
                resultado['avisos'] = avisos
                if omitidos:
                    resultado['avisos'].append('Los archivos Word y las imágenes guardadas no se incluyen en esta búsqueda; requieren conversión u OCR.')
                fuentes = buscar_fragmentos(pregunta, paginas, k=8)
                resultado['fuentes'] = fuentes
            if not fuentes:
                resultado['error'] = 'No encuentro información suficiente en los PDF consultados. No se envió nada a Gemini.'
            elif permiso:
                try:
                    clave = st.secrets['GEMINI_API_KEY'].strip()
                except (KeyError, FileNotFoundError):
                    raise ErrorConsulta('Falta GEMINI_API_KEY en los secretos; puedes revisar las fuentes encontradas.')
                if not clave or not modelo:
                    raise ErrorConsulta('Revisa la clave y el modelo de Gemini; las fuentes siguen disponibles.')
                with st.spinner('Preparando la respuesta con sus fuentes...'):
                    resultado['respuesta'], resultado['error'] = responder_documentos(pregunta, fuentes, clave, modelo=modelo)
        except (ErrorConsulta, ErrorDocumentos, ErrorClientes) as exc:
            resultado['error'] = str(exc)
        except Exception:
            resultado['error'] = 'No se pudo completar la consulta. Revisa tu conexión y vuelve a intentarlo.'
        st.session_state['auto_resultado'] = resultado
    r = st.session_state.get('auto_resultado')
    if r and r['uid'] == uid:
        st.write('Pregunta consultada: ' + r['pregunta'])
        if r['ambito']:
            st.info(r['ambito'])
        for aviso in r['avisos']:
            st.warning(aviso)
        if r['error']:
            st.warning(r['error'])
        if r['respuesta']:
            st.subheader('Borrador de respuesta')
            st.markdown(r['respuesta'])
            st.caption('Verifica las referencias antes de compartir. No se determina la procedencia de un siniestro ni se garantiza cobertura.')
        if r['fuentes']:
            with st.expander('Ver fuentes utilizadas', expanded=not bool(r['respuesta'])):
                for i, f in enumerate(r['fuentes'], 1):
                    st.write(f"Fuente {i} · {f['documento']} · Página PDF {f['pagina']}")
                    st.text(f['texto'])
            if not r['respuesta'] and not r['error']:
                st.info('Fuentes encontradas localmente. Para generar el borrador, autoriza el envío y vuelve a pulsar Consultar.')
