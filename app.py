import streamlit as st
from pypdf import PdfReader
from io import BytesIO
from pathlib import Path
import hashlib
import json
from google import genai
from google.genai import errors
from ia_documentos import buscar_fragmentos, responder_documentos, MODELO_GEMINI

RUTA_LOGO = Path(__file__).resolve().parent / "logo.svg"

st.set_page_config(
    page_title="Advisor’s Ally",
    page_icon=str(RUTA_LOGO) if RUTA_LOGO.is_file() else None,
    layout="wide",
)

# Estilo visual: plata y verde metálico.
st.html("""
<style>
.stApp {
    background: linear-gradient(
        125deg,
        #FFFFFF 0%,
        #E1E5E4 22%,
        #FAFBFB 45%,
        #CCD3D0 72%,
        #F7F9F8 100%
    );
}

section[data-testid="stSidebar"] {
    background: linear-gradient(
        155deg,
        #F4F6F5 0%,
        #C0CAC6 38%,
        #E9EEEB 63%,
        #AEBDB6 100%
    );
    border-right: 1px solid #9AA9A2;
}

div.stButton > button {
    background: linear-gradient(
        115deg,
        #173D34 0%,
        #356657 35%,
        #427462 50%,
        #245345 70%,
        #173D34 100%
    );
    color: #FFFFFF;
    border: 1px solid #78988A;
    border-radius: 12px;
    box-shadow: 0 3px 8px rgba(20, 45, 35, 0.18);
    font-weight: 600;
    padding: 0.65rem 1.2rem;
}

div.stButton > button:hover {
    color: #FFFFFF;
    border-color: #B5C8BE;
    filter: brightness(1.08);
}

div.stButton > button:focus-visible {
    outline: 3px solid #245C50;
    outline-offset: 3px;
}
</style>
""")

# Menú lateral.
st.sidebar.title("Advisor’s Ally")

with st.sidebar.expander("Personalizar mi perfil"):
    nombre_agente = st.text_input(
        "Nombre del agente o despacho",
        key="nombre_agente",
    )

    logo_agente = st.file_uploader(
        "Sube tu logo en PNG o JPG",
        type=["png", "jpg", "jpeg"],
        key="logo_agente",
    )

if logo_agente is not None:
    st.sidebar.image(logo_agente, width=150)

if nombre_agente.strip():
    st.sidebar.write(nombre_agente.strip())

seccion = st.sidebar.radio(
    "¿Qué quieres hacer?",
    [
        "Inicio",
        "Clasificar consultas",
        "Consultar documentos",
        "Mis pendientes",
    ],
)

with st.sidebar.expander("Modelo de IA"):
    modelo_gemini = st.text_input(
        "Modelo de Gemini", value=MODELO_GEMINI, key="modelo_gemini"
    ).strip()
    st.caption("Cambiar el modelo puede cambiar su disponibilidad y costo.")

# Encabezado principal.
if RUTA_LOGO.is_file():
    st.image(str(RUTA_LOGO), width=70)
st.title("Advisor’s Ally")
st.caption("Tu asistente para agentes de seguros")

# Pantallas iniciales.
if seccion == "Inicio":
    st.header("Bienvenida a Advisor’s Ally")

    st.write(
        "Organiza tu trabajo y encuentra la información "
        "que necesitas para atender a tus clientes."
    )

    st.info("Selecciona una opción en el menú de la izquierda.")

    if st.button("Probar estilo metálico"):
        st.success("¡El botón funciona!")

elif seccion == "Clasificar consultas":
    st.header("Clasificar consultas")

    st.write(
        "Aquí analizaremos el motivo del mensaje de un cliente."
    )

    st.info("La clasificación con IA se incorporará en el siguiente paso.")

elif seccion == "Consultar documentos":
    st.header("Consultar documentos")
    st.write(
        "Carga documentos de un solo caso y selecciona cuáles consultar. "
        "Para las pruebas con Gemini gratuito, utiliza documentos públicos "
        "o copias sin información personal ni confidencial."
    )

    documentos = st.file_uploader(
        "Selecciona uno o varios PDF", type=["pdf"],
        accept_multiple_files=True, key="documentos_caso",
    )

    # Caché privada de esta sesión: nunca compartimos PDF entre usuarios.
    identificadores = []
    for documento in documentos or []:
        identificadores.append((
            documento.name, hashlib.sha256(documento.getvalue()).hexdigest()
        ))
    firma_archivos = hashlib.sha256(
        json.dumps(identificadores, ensure_ascii=False).encode()
    ).hexdigest()

    if st.session_state.get("firma_pdf") != firma_archivos:
        paginas = []
        resumenes = []
        for documento in documentos or []:
            resumen = {"nombre": documento.name, "paginas": 0,
                       "sin_texto": [], "error": None}
            try:
                lector = PdfReader(BytesIO(documento.getvalue()))
                if lector.is_encrypted:
                    resumen["error"] = "PDF protegido: carga una copia sin contraseña."
                else:
                    resumen["paginas"] = len(lector.pages)
                    for numero, pagina in enumerate(lector.pages, 1):
                        # Conserva mejor las columnas de las carátulas.
                        texto = (pagina.extract_text(
                            extraction_mode="layout") or "").strip()
                        if not texto:
                            texto = (pagina.extract_text() or "").strip()
                        if texto:
                            paginas.append({"documento": documento.name,
                                            "pagina": numero, "texto": texto})
                        else:
                            resumen["sin_texto"].append(numero)
            except Exception:
                # No conservamos la extracción parcial de un archivo fallido.
                paginas = [p for p in paginas if p["documento"]
                           != documento.name]
                resumen["error"] = "No pudimos leer este archivo. Prueba con otra copia."
            resumenes.append(resumen)
        st.session_state.update({"firma_pdf": firma_archivos,
                                 "paginas_documentos": paginas,
                                 "resumenes_pdf": resumenes})
        st.session_state.pop("consulta_guardada", None)

    paginas_documentos = st.session_state.get("paginas_documentos", [])
    for resumen in st.session_state.get("resumenes_pdf", []):
        with st.expander(resumen["nombre"]):
            if resumen["error"]:
                st.warning(resumen["error"])
                continue
            del_archivo = [p for p in paginas_documentos
                           if p["documento"] == resumen["nombre"]]
            st.write(f"Páginas del PDF: {resumen['paginas']}")
            st.write(f"Páginas con texto: {len(del_archivo)}")
            if resumen["sin_texto"]:
                st.warning(
                    "Sin texto en páginas "
                    + ", ".join(map(str, resumen["sin_texto"]))
                    + ". Pueden estar vacías o necesitar OCR."
                )
            if del_archivo:
                seleccion = st.selectbox(
                    "Página para revisar", range(len(del_archivo)),
                    format_func=lambda i, ps=del_archivo: f"Página {ps[i]['pagina']}",
                    key=f"vista_{firma_archivos}_{resumen['nombre']}",
                )
                st.text_area(
                    "Texto extraído", value=del_archivo[seleccion]["texto"],
                    height=300, disabled=True,
                    key=f"texto_{firma_archivos}_{resumen['nombre']}_{seleccion}",
                )
    st.caption("Cargar y buscar en los PDF no los envía a Gemini.")

    nombres = list(dict.fromkeys(p["documento"] for p in paginas_documentos))
    alcance = st.multiselect(
        "Documentos que quieres consultar", options=nombres, default=nombres,
        key=f"alcance_{firma_archivos}",
        help="Para definiciones, prueba primero solo las condiciones generales.",
    )
    paginas_consulta = [
        p for p in paginas_documentos if p["documento"] in alcance]

    st.subheader("Consultar información con IA")
    consulta = st.text_input(
        "¿Qué información necesitas?",
        placeholder="Ejemplo: ¿Qué significa deducible anual?",
        key="consulta_documental",
    )
    firma_consulta = hashlib.sha256(json.dumps(
        [firma_archivos, sorted(alcance), consulta.strip(), modelo_gemini],
        ensure_ascii=False,
    ).encode()).hexdigest()
    if st.session_state.get("firma_consulta") != firma_consulta:
        st.session_state["firma_consulta"] = firma_consulta
        st.session_state.pop("consulta_guardada", None)

    if st.button("Buscar fuentes", key="buscar_documentos"):
        if not paginas_consulta:
            st.warning("Carga y selecciona al menos un documento con texto.")
        elif not consulta.strip():
            st.warning("Escribe tu pregunta.")
        else:
            fuentes = buscar_fragmentos(consulta, paginas_consulta, k=8)
            st.session_state["consulta_guardada"] = {
                "fuentes": fuentes, "respuesta": None, "error": None,
            }

    guardada = st.session_state.get("consulta_guardada")
    if guardada is not None:
        fuentes = guardada["fuentes"]
        if not fuentes:
            st.info(
                "No encontramos fuentes pertinentes. Prueba otros términos "
                "o selecciona otros documentos. No se envió nada a Gemini."
            )
        else:
            st.subheader("Fuentes encontradas")
            st.caption(
                "Revisa las páginas antes de generar la respuesta. "
                "La búsqueda local prioriza frases y variantes de palabras."
            )
            for numero, fuente in enumerate(fuentes, 1):
                with st.expander(
                    f"Fuente {numero} · {fuente['documento']} · "
                    f"Página PDF {fuente['pagina']}"
                ):
                    st.text(fuente["texto"])

            autorizar_envio = st.checkbox(
                "Confirmo que la consulta, los nombres de archivo y las fuentes "
                "seleccionadas no contienen información personal, sensible ni "
                "confidencial, y autorizo su envío a Gemini.",
                key=f"autorizar_{firma_consulta}",
            )
            if st.button("Generar borrador", key="generar_borrador",
                         disabled=not autorizar_envio):
                try:
                    clave = st.secrets["GEMINI_API_KEY"].strip()
                    if not clave or not modelo_gemini:
                        guardada["error"] = "Revisa la clave y el nombre del modelo."
                        guardada["respuesta"] = None
                    else:
                        with st.spinner("Preparando respuesta con las fuentes..."):
                            respuesta, error = responder_documentos(
                                consulta, fuentes, clave, modelo=modelo_gemini
                            )
                        guardada.update(respuesta=respuesta, error=error)
                except (FileNotFoundError, KeyError):
                    guardada.update(respuesta=None, error=(
                        "No encontramos GEMINI_API_KEY. Revisa secrets.toml."
                    ))
                except Exception:
                    guardada.update(respuesta=None, error=(
                        "No se pudo completar la solicitud. Revisa la configuración."
                    ))
            if guardada["error"]:
                st.warning(guardada["error"])
            if guardada["respuesta"]:
                st.subheader("Borrador de respuesta")
                st.markdown(guardada["respuesta"])
                st.caption(
                    "Las referencias identifican las fuentes enviadas; comprueba "
                    "que respaldan la respuesta. La aplicación no valida la "
                    "procedencia de un siniestro ni la compatibilidad contractual."
                )

elif seccion == "Mis pendientes":
    st.header("Mis pendientes")

    st.write("Aquí organizaremos tus tareas y seguimientos.")

    st.info("El registro de pendientes aún está en preparación.")
with st.sidebar.expander("Conexión con Gemini"):
    st.caption(
        "Esta prueba consulta el catálogo de modelos. "
        "No envía tus documentos."
    )

    if st.button("Comprobar conexión", key="probar_gemini"):
        try:
            clave = st.secrets["GEMINI_API_KEY"].strip()

            if not clave:
                st.warning("La clave está vacía en secrets.toml.")

            else:
                with st.spinner("Conectando con Gemini..."):
                    with genai.Client(
                        api_key=clave,
                        http_options={"timeout": 15000},
                    ) as cliente:
                        modelos = [
                            modelo.name
                            for modelo in cliente.models.list()
                            if "generateContent"
                            in (modelo.supported_actions or [])
                        ]

                st.success("¡Conexión correcta!")

                st.write("Modelos de generación disponibles:")
                for nombre in modelos:
                    st.write(nombre)

                st.caption(
                    "La conexión funciona. La cuota para generar "
                    "respuestas se comprobará por separado."
                )

        except (FileNotFoundError, KeyError):
            st.error(
                "No encontramos GEMINI_API_KEY. Revisa el archivo "
                ".streamlit/secrets.toml y reinicia la app."
            )

        except errors.APIError as error:
            st.error(
                f"Gemini rechazó la consulta (código {error.code}). "
                "Revisa la clave y los permisos del proyecto."
            )

        except Exception:
            st.error(
                "No pudimos completar la conexión. Revisa Internet "
                "y el formato de secrets.toml. No compartas tu clave."
            )
with st.sidebar.expander("Primera respuesta de Gemini"):
    st.caption(
        "Esta prueba envía solo un saludo, ningún documento."
    )

    if st.button("Probar respuesta", key="probar_respuesta"):
        try:
            with st.spinner("Esperando respuesta..."):
                with genai.Client(
                    api_key=st.secrets["GEMINI_API_KEY"],
                    http_options={"timeout": 30000},
                ) as cliente:
                    respuesta = cliente.models.generate_content(
                        model=modelo_gemini,
                        contents=(
                            "Saluda en español a Penny en una frase. "
                            "Preséntate como la asistente virtual "
                            "de Advisor’s Ally."
                        ),
                        config={"max_output_tokens": 500},
                    )

            if respuesta.text:
                st.success("¡Gemini respondió!")
                st.write(respuesta.text)
            else:
                st.warning(
                    "Gemini no devolvió texto. Prueba más tarde."
                )

        except errors.APIError as error:
            if error.code == 429:
                st.warning(
                    "La cuota no está disponible o se alcanzó "
                    "un límite. Revisa los límites en AI Studio."
                )
            elif error.code == 404:
                st.error(
                    "Este modelo no está disponible. "
                    "Revisaremos el catálogo para elegir otro."
                )
            else:
                st.error(
                    f"No se pudo generar la respuesta "
                    f"(código {error.code})."
                )

        except Exception:
            st.error(
                "No pudimos completar la prueba. Revisa "
                "la conexión y la configuración de secretos."
            )
