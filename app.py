import streamlit as st
from pypdf import PdfReader
from io import BytesIO
from pathlib import Path
import hashlib
import json
from google import genai
from google.genai import errors
from ia_documentos import buscar_fragmentos, responder_documentos, MODELO_GEMINI
from almacen_documentos import (
    mostrar_acceso, mostrar_documentos, documentos_para_consulta, ErrorDocumentos,
)

from clientes import mostrar_clientes
from consulta_automatica import mostrar_consulta_automatica
from whatsapp import mostrar_whatsapp
from calendario import mostrar_calendario
from calendario_web import procesar_retorno_google

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

div.stButton > button,
div[data-testid="stFormSubmitButton"] button {
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

div.stButton > button:hover,
div[data-testid="stFormSubmitButton"] button:hover {
    color: #FFFFFF;
    border-color: #B5C8BE;
    filter: brightness(1.08);
}

div.stButton > button:focus-visible,
div[data-testid="stFormSubmitButton"] button:focus-visible {
    outline: 3px solid #245C50;
    outline-offset: 3px;
}
</style>
""")

# Menú lateral.
st.sidebar.title("Advisor’s Ally")
cliente_documentos = mostrar_acceso()
procesar_retorno_google(cliente_documentos)
aviso_google = st.session_state.pop('cal_web_aviso', None)
if aviso_google:
    getattr(st, aviso_google[0])(aviso_google[1])

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
        "Clientes",
        "Mis documentos",
        "Consultar documentos",
        "WhatsApp",
        "Calendario",
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


elif seccion == "Clasificar consultas":
    st.header("Clasificar consultas")

    st.write(
        "Aquí analizaremos el motivo del mensaje de un cliente."
    )

    st.info("La clasificación con IA se incorporará en el siguiente paso.")

elif seccion == "Clientes":
    mostrar_clientes(cliente_documentos)

elif seccion == "Mis documentos":
    mostrar_documentos(cliente_documentos)

elif seccion == "Consultar documentos":
    mostrar_consulta_automatica(cliente_documentos, modelo_gemini)

elif seccion == "WhatsApp":
    mostrar_whatsapp(cliente_documentos, modelo_gemini)

elif seccion == "Calendario":
    mostrar_calendario(cliente_documentos)

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

