"""Acceso por usuario y archivos persistentes en el bucket privado documentos.

No usa service_role, clientes globales ni cachés compartidas entre sesiones.
Requiere las políticas RLS de carpeta auth.uid() configuradas en Supabase.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath
import hashlib
import re
import unicodedata
from uuid import uuid4

BUCKET = "documentos"
MAX_BYTES = 50 * 1024 * 1024
TIPOS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
}


class ErrorDocumentos(Exception):
    """Mensaje apto para mostrar; no contiene claves, tokens o datos internos."""


@dataclass
class DocumentoConsulta:
    name: str
    contenido: bytes

    def getvalue(self):
        return self.contenido


def crear_cliente(url, clave):
    from supabase import create_client, ClientOptions
    url, clave = str(url).strip().rstrip("/"), str(clave).strip()
    if not url.startswith("https://") or not clave.startswith("sb_publishable_"):
        raise ErrorDocumentos(
            "Revisa SUPABASE_URL y SUPABASE_PUBLISHABLE_KEY en los secretos."
        )
    return create_client(url, clave, options=ClientOptions(
        persist_session=False, auto_refresh_token=True, storage_client_timeout=120,
    ))


def usuario_validado(cliente):
    """Consulta Auth; no confía en un ID enviado por un formulario."""
    try:
        usuario = cliente.auth.get_user().user
    except Exception as exc:
        raise ErrorDocumentos(
            "No pudimos verificar tu acceso. Revisa la conexión o vuelve a iniciar sesión."
        ) from exc
    if usuario is None or not usuario.id:
        raise ErrorDocumentos("Inicia sesión para acceder a tus documentos.")
    return usuario


def nombre_seguro(nombre):
    nombre = str(nombre).replace("\\", "/").split("/")[-1]
    extension = PurePosixPath(nombre).suffix.lower()
    if extension not in TIPOS:
        raise ErrorDocumentos("Formato no admitido. Usa PDF, DOC, DOCX, JPG o PNG.")
    base = unicodedata.normalize("NFKD", PurePosixPath(nombre).stem)
    base = base.encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9_-]+", "_", base).strip("_-")[:120]
    return (base or "documento") + extension


def mensaje_storage(exc, operacion):
    # Nunca presenta el error original: puede incluir rutas, identificadores o tokens.
    codigo = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if str(codigo) == "413":
        return "El archivo supera el tamaño permitido por el bucket o el plan."
    return (
        f"No pudimos {operacion}. Revisa la conexión, el bucket privado "
        "'documentos' y sus permisos por usuario."
    )


class AlmacenDocumentos:
    def __init__(self, cliente):
        self.cliente = cliente

    def _ruta(self, ruta):
        uid = str(usuario_validado(self.cliente).id)
        partes = str(ruta).split("/")
        if (len(partes) != 2 or partes[0] != uid or not partes[1]
                or partes[1] in (".", "..") or "\\" in partes[1]):
            raise ErrorDocumentos("El documento no pertenece a la cuenta activa.")
        return str(ruta)

    def guardar(self, nombre, contenido):
        usuario = usuario_validado(self.cliente)
        nombre = nombre_seguro(nombre)
        if not contenido:
            raise ErrorDocumentos("El archivo está vacío.")
        if len(contenido) > MAX_BYTES:
            raise ErrorDocumentos("El máximo configurado en esta versión es 50 MB por archivo.")
        ruta = f"{usuario.id}/{uuid4().hex}__{nombre}"
        try:
            self.cliente.storage.from_(BUCKET).upload(
                path=ruta, file=contenido,
                file_options={"content-type": TIPOS[PurePosixPath(nombre).suffix],
                              "upsert": "false"},
            )
        except Exception as exc:
            raise ErrorDocumentos(mensaje_storage(exc, "guardar el archivo")) from exc
        return ruta

    def listar(self):
        uid = str(usuario_validado(self.cliente).id)
        archivos, offset = [], 0
        try:
            while True:
                lote = self.cliente.storage.from_(BUCKET).list(
                    path=uid,
                    options={"limit": 100, "offset": offset,
                             "sortBy": {"column": "name", "order": "asc"}},
                )
                if not isinstance(lote, list):
                    raise ValueError("Respuesta de catálogo no válida")
                for item in lote:
                    nombre = item.get("name", "")
                    if not nombre or "/" in nombre or not item.get("id"):
                        continue
                    visible = nombre.split("__", 1)[-1]
                    archivos.append({
                        "ruta": f"{uid}/{nombre}", "nombre": visible,
                        "fecha": item.get("created_at", ""),
                        "tamano": (item.get("metadata") or {}).get("size", 0),
                    })
                if len(lote) < 100:
                    break
                offset += 100
        except Exception as exc:
            raise ErrorDocumentos(mensaje_storage(exc, "listar los archivos")) from exc
        return archivos

    def descargar(self, ruta):
        ruta = self._ruta(ruta)
        try:
            return self.cliente.storage.from_(BUCKET).download(ruta)
        except Exception as exc:
            raise ErrorDocumentos(mensaje_storage(exc, "recuperar el archivo")) from exc

    def eliminar(self, ruta):
        ruta = self._ruta(ruta)
        try:
            self.cliente.storage.from_(BUCKET).remove([ruta])
        except Exception as exc:
            raise ErrorDocumentos(mensaje_storage(exc, "eliminar el archivo")) from exc


def detalle_acceso(error, etapa):
    """Diagnóstico seguro: solo etapa, clase y códigos, nunca mensaje bruto."""
    origen = error.__cause__ or error
    codigo = getattr(origen, "code", None)
    if not isinstance(codigo, str) or not re.fullmatch(r"[a-zA-Z0-9_]+", codigo):
        codigo = "no_disponible"
    estado = getattr(origen, "status", None) or getattr(origen, "status_code", None)
    if not str(estado).isdigit():
        estado = "no_disponible"
    tipo = type(origen).__name__
    return f"Acceso v2 · etapa: {etapa} · tipo: {tipo} · código: {codigo} · estado: {estado}"


def mostrar_acceso():
    """Devuelve el cliente de esta sesión, o None para la demostración pública."""
    import streamlit as st
    if st.session_state.pop("_aa_limpiar_formulario", False):
        st.session_state.pop("_aa_email", None)
        st.session_state.pop("_aa_password", None)
    cliente = st.session_state.get("_aa_cliente")
    usuario = None
    if cliente is not None:
        try:
            usuario = usuario_validado(cliente)
        except ErrorDocumentos:
            # Ante fallo de verificación, no se muestran datos privados en memoria.
            st.session_state.clear()
            st.sidebar.warning("Vuelve a iniciar sesión para ver tus documentos.")
            cliente = None

    with st.sidebar.expander("Mi cuenta", expanded=usuario is None):
        if usuario is not None:
            st.write(f"Sesión: {usuario.email}")
            if st.button("Cerrar sesión", key="_aa_salir"):
                try:
                    cliente.auth.sign_out({"scope": "local"})
                except Exception:
                    pass
                st.session_state.clear()
                st.rerun()
            return cliente

        st.caption("Entra para guardar y recuperar tus documentos privados.")
        st.caption("Acceso v2")
        with st.form("_aa_form_acceso", clear_on_submit=True):
            correo = st.text_input("Correo", key="_aa_email")
            password = st.text_input("Contraseña", type="password", key="_aa_password")
            entrar = st.form_submit_button("Entrar")
        if entrar:
            if not correo.strip() or not password:
                st.warning("Completa el correo y la contraseña.")
            else:
                etapa = "configuracion"
                try:
                    url = st.secrets["SUPABASE_URL"]
                    clave_publicable = st.secrets["SUPABASE_PUBLISHABLE_KEY"]
                    etapa = "crear_cliente"
                    nuevo = crear_cliente(url, clave_publicable)
                    etapa = "autenticar_usuario"
                    respuesta = nuevo.auth.sign_in_with_password({
                        "email": correo.strip(), "password": password,
                    })
                    if not respuesta.session or not respuesta.user:
                        raise ErrorDocumentos("No se pudo abrir la sesión.")
                    etapa = "verificar_usuario"
                    usuario_validado(nuevo)
                    etapa = "guardar_sesion"
                    # Solo memoria de esta sesión del navegador, nunca caché global.
                    st.session_state.clear()
                    st.session_state["_aa_cliente"] = nuevo
                    st.session_state["_aa_limpiar_formulario"] = True
                    st.rerun()
                except (FileNotFoundError, KeyError):
                    st.error("Falta configurar la URL y clave publicable de Supabase en Secrets.")
                except ImportError:
                    st.error("Falta instalar la librería supabase.")
                except ErrorDocumentos as exc:
                    st.error(str(exc))
                    st.caption(detalle_acceso(exc, etapa))
                except Exception as exc:
                    codigo = getattr(exc, "code", None)
                    if codigo == "invalid_credentials":
                        st.error("Supabase no aceptó ese correo y contraseña. Comprueba que sean los mismos que funcionaron en la terminal.")
                    elif codigo == "email_not_confirmed":
                        st.error("La cuenta necesita confirmar su correo en Supabase.")
                    else:
                        st.error("No se pudo completar el acceso. Comparte únicamente el diagnóstico de abajo.")
                    st.caption(detalle_acceso(exc, etapa))
    return None


def documentos_para_consulta(cliente):
    """PDF seleccionados en Mis documentos; siempre asociados al usuario activo."""
    import streamlit as st
    if cliente is None:
        return []
    uid = str(usuario_validado(cliente).id)
    if st.session_state.get("_aa_propietario_consulta") != uid:
        return []
    return st.session_state.get("_aa_pdf_consulta", [])


def mostrar_documentos(cliente):
    import streamlit as st
    st.header("Mis documentos")
    if cliente is None:
        st.info("Inicia sesión en Mi cuenta para guardar y recuperar tus archivos.")
        return
    almacen = AlmacenDocumentos(cliente)
    try:
        uid = str(usuario_validado(cliente).id)
    except ErrorDocumentos as exc:
        st.warning(str(exc))
        return
    aviso = st.session_state.pop("_aa_aviso", None)
    if aviso:
        st.success(aviso)
    st.write("Guarda archivos privados para recuperarlos cuando vuelvas a entrar.")
    version = st.session_state.get("_aa_version_subida", 0)
    nuevos = st.file_uploader(
        "Archivos para guardar · máximo 50 MB por archivo",
        type=["pdf", "doc", "docx", "jpg", "jpeg", "png"],
        accept_multiple_files=True, key=f"_aa_subida_{version}",
    )
    if st.button("Guardar archivos", disabled=not nuevos, key="_aa_guardar"):
        errores = False
        guardados = st.session_state.setdefault("_aa_huellas_subidas", set())
        for archivo in nuevos or []:
            contenido = archivo.getvalue()
            huella = hashlib.sha256(archivo.name.encode() + contenido).hexdigest()
            if huella in guardados:
                continue
            try:
                with st.spinner(f"Guardando {archivo.name}..."):
                    almacen.guardar(archivo.name, contenido)
                guardados.add(huella)
                st.success(f"Guardado: {archivo.name}")
            except ErrorDocumentos as exc:
                errores = True
                st.error(f"{archivo.name}: {exc}")
        if not errores:
            st.session_state["_aa_version_subida"] = version + 1
            st.session_state.pop("_aa_huellas_subidas", None)
            st.session_state["_aa_aviso"] = "Archivos guardados en tu cuenta."
            st.rerun()

    st.subheader("Archivos guardados")
    st.button("Actualizar lista", key="_aa_actualizar")
    try:
        archivos = almacen.listar()
    except ErrorDocumentos as exc:
        st.error(str(exc))
        return
    if not archivos:
        st.info("Tu cuenta todavía no tiene archivos guardados.")
        return
    por_ruta = {a["ruta"]: a for a in archivos}
    # Evita presentar descargas o selecciones de objetos ya eliminados.
    descargado = st.session_state.get("_aa_descarga")
    if descargado and descargado["ruta"] not in por_ruta:
        st.session_state.pop("_aa_descarga", None)
    for a in archivos:
        try:
            mb = float(a["tamano"] or 0) / (1024 * 1024)
        except (TypeError, ValueError):
            mb = 0
        st.write(f"{a['nombre']} · {mb:.2f} MB")

    st.subheader("Usar PDF en consultas")
    pdfs = [a["ruta"] for a in archivos if a["nombre"].lower().endswith(".pdf")]
    elegidos = st.multiselect(
        "Selecciona los PDF de un mismo caso", options=pdfs,
        format_func=lambda r: f"{por_ruta[r]['nombre']} · {r.split('/')[-1][:6]}",
        key="_aa_pdf_elegidos",
    )
    if st.button("Preparar PDF para consultar", disabled=not elegidos, key="_aa_preparar"):
        try:
            preparados = []
            with st.spinner("Recuperando tus PDF..."):
                for ruta in elegidos:
                    a = por_ruta[ruta]
                    # Nombre único por objeto, sin incluir el identificador del usuario.
                    nombre = f"{PurePosixPath(a['nombre']).stem}__{ruta.split('/')[-1][:8]}.pdf"
                    preparados.append(DocumentoConsulta(nombre, almacen.descargar(ruta)))
            st.session_state["_aa_pdf_consulta"] = preparados
            st.session_state["_aa_propietario_consulta"] = uid
            st.success("PDF preparados. Abre Consultar documentos en el menú.")
        except ErrorDocumentos as exc:
            st.error(str(exc))
    st.caption("Word e imágenes se pueden guardar y descargar. La consulta con IA sigue disponible para PDF con texto.")

    st.subheader("Descargar o eliminar")
    elegido = st.selectbox(
        "Archivo", options=list(por_ruta),
        format_func=lambda r: f"{por_ruta[r]['nombre']} · {r.split('/')[-1][:6]}",
        key="_aa_archivo_accion",
    )
    if st.button("Preparar descarga", key="_aa_descargar"):
        try:
            st.session_state["_aa_descarga"] = {
                "ruta": elegido, "datos": almacen.descargar(elegido),
            }
        except ErrorDocumentos as exc:
            st.error(str(exc))
    descarga = st.session_state.get("_aa_descarga")
    if descarga and descarga["ruta"] == elegido:
        nombre = por_ruta[elegido]["nombre"]
        st.download_button(
            "Descargar archivo", data=descarga["datos"], file_name=nombre,
            mime=TIPOS.get(PurePosixPath(nombre).suffix.lower(), "application/octet-stream"),
            key="_aa_descarga_boton",
        )
    confirmado = st.checkbox(
        "Confirmo que quiero eliminar este archivo de mi cuenta.",
        key=f"_aa_confirmar_{elegido}",
    )
    if st.button("Eliminar archivo", disabled=not confirmado, key="_aa_eliminar"):
        try:
            almacen.eliminar(elegido)
            # Limpia el contexto en memoria para no citar una copia del archivo borrado.
            for k in ["_aa_pdf_consulta", "_aa_propietario_consulta", "_aa_descarga",
                      "paginas_documentos", "resumenes_pdf", "firma_pdf",
                      "consulta_guardada", "firma_consulta", "_aa_pdf_elegidos",
                      "_aa_archivo_accion"]:
                st.session_state.pop(k, None)
            st.session_state["_aa_aviso"] = "Archivo eliminado. Vuelve a seleccionar los PDF que quieras consultar."
            st.rerun()
        except ErrorDocumentos as exc:
            st.error(str(exc))
