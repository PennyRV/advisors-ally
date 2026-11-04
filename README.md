# Advisor’s Ally

Asistente para agentes de seguros: clientes, múltiples pólizas por cliente, documentos privados, preguntas con Gemini y agenda con Google Calendar y Google Meet.

**Proyecto académico de Penélope Romero · estado al 4 de noviembre de 2026.**

- Aplicación: https://advisors-ally-3nttdzmg6i5qh92vbpaorz.streamlit.app/
- Repositorio: https://github.com/PennyRV/advisors-ally
- Archivo de arranque en Streamlit Community Cloud: `app.py`.

## Problema y usuario

Un agente de seguros necesita encontrar condiciones de una póliza, conservar los datos de sus clientes y dar seguimiento a citas y renovaciones. Hoy suele hacerlo entre archivos PDF, hojas de cálculo, correo y WhatsApp. Advisor’s Ally reúne estas tareas y permite preguntar por documentos previamente cargados, sin volver a seleccionarlos en cada consulta.

La IA ayuda a localizar y explicar información documental con referencias. El agente revisa las páginas originales y decide qué comunicar al cliente; la aplicación no determina la procedencia de un siniestro ni garantiza una cobertura.

## Funciones y estado

| Función | Estado y alcance |
| --- | --- |
| Acceso | Registro e inicio de sesión mediante Supabase Auth; confirmación por correo. |
| Clientes | Nombre, RFC, cumpleaños, teléfono, correo y comentarios. |
| Pólizas | Varias por cliente: número, compañía, emisión, vencimiento, tipo, suma asegurada, moneda, forma y frecuencia de pago y comentarios. |
| Documentos | Archivos privados del agente, asociados a sus clientes y pólizas. La búsqueda textual utiliza PDF con texto extraíble. |
| Consulta automática | Búsqueda local de fragmentos y respuesta de Gemini con referencias al documento y a la página PDF. |
| Calendario | Autorización individual de Google, consulta de agenda y creación de citas con recordatorios e invitaciones de Google Meet. |
| WhatsApp | Preparación y simulación de preguntas y borradores de cumpleaños/renovación. La verificación empresarial de Meta está pendiente; todavía no hay envío real ni recepción por webhook. |
| Clasificar consultas | Pendiente de desarrollo. |
| Mis pendientes | Pendiente de desarrollo. |

**Recordatorios:** los cumpleaños y vencimientos se calculan por fechas y reglas; sus borradores actuales no los genera un LLM. Google Calendar gestiona las notificaciones de las citas guardadas. No hay un proceso programado que envíe WhatsApp automáticamente.

## IA y arquitectura

1. El agente inicia sesión y carga documentos de su cartera.
2. La app extrae texto de PDF con `pypdf` y busca fragmentos pertinentes mediante BM25 y normalización de términos en español.
3. Recupera hasta ocho fragmentos, conserva su documento y página y, con autorización del usuario, los envía a Gemini junto con la pregunta.
4. El prompt exige usar únicamente esas fuentes, citar referencias y reconocer lo que no se puede responder.
5. El agente revisa la respuesta y las páginas originales antes de utilizarla.

Es una implementación de recuperación aumentada por generación (RAG) con búsqueda léxica local; no utiliza una base vectorial ni un modelo entrenado con las pólizas. El modelo predeterminado en el código revisado es `gemini-3.5-flash-lite`, configurable desde la app y sujeto a disponibilidad y cuota de la cuenta.

Si falta la clave, se agota la cuota, el modelo no está disponible o falla la API, la app muestra un aviso y permite revisar las fuentes recuperadas localmente. La validación de números de cita no garantiza que cada afirmación sea correcta: sigue siendo necesaria la revisión humana.

## Herramientas utilizadas

| Herramienta | Uso |
| --- | --- |
| Python y Streamlit 1.50.0 | Lógica, formularios, navegación e interfaz web. |
| Visual Studio Code, PowerShell y entorno virtual | Desarrollo y ejecución local en Windows. |
| GitHub y Streamlit Community Cloud | Versionado y despliegue público. |
| Supabase Auth, PostgreSQL y Storage | Usuarios, datos y documentos privados con políticas RLS por agente. |
| Google Gen AI SDK y Gemini | Redacción de respuestas documentales fundamentadas. |
| pypdf y BM25 | Extracción de texto y recuperación de fuentes. |
| Google Calendar API y OAuth 2.0 | Acceso autorizado a la agenda de cada agente y creación de eventos con Meet. |
| cryptography y PKCE | Cifrado de tokens y protección del flujo OAuth web. |
| Gmail SMTP | Correos de confirmación de Supabase con contraseña de aplicación y verificación en dos pasos. |
| Meta for Developers | Preparación de WhatsApp Business; integración de producción pendiente. |
| ChatGPT/Codex | Especificación, apoyo a programación, depuración y documentación. |

La conexión con Gmail para los correos de registro es distinta de la autorización de Google Calendar. La app no lee el buzón de Gmail.

## Instalación local

Se recomienda Python 3.12. Desde la carpeta del repositorio en PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

En Linux o macOS:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run app.py
```

Conservar las versiones declaradas en `requirements.txt`, especialmente `streamlit==1.50.0` y `pyarrow<25`. La versión fijada de Streamlit forma parte de la corrección de errores de carga de componentes observados durante las pruebas.

## Configuración privada

En local, crear `.streamlit/secrets.toml`, excluido de Git. En Community Cloud, introducir la configuración en **Settings → Secrets**. El siguiente ejemplo contiene únicamente marcadores, que deben sustituirse en la configuración privada:

```toml
GEMINI_API_KEY = "REEMPLAZAR_EN_SECRETS"
SUPABASE_URL = "https://TU_PROYECTO.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "TU_CLAVE_PUBLICABLE"

[acceso]
url_confirmacion = "https://advisors-ally-3nttdzmg6i5qh92vbpaorz.streamlit.app/"

[google_calendar]
client_id = "TU_CLIENTE_WEB.apps.googleusercontent.com"
client_secret = "REEMPLAZAR_EN_SECRETS"
redirect_uri = "https://advisors-ally-3nttdzmg6i5qh92vbpaorz.streamlit.app/"
token_key = "TU_CLAVE_FERNET"
```

Generar una clave Fernet una sola vez, guardarla como `token_key` y conservarla para poder descifrar conexiones existentes:

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

No publicar claves de API, contraseñas SMTP, JSON de OAuth, tokens, `secrets.toml`, archivos `.env` ni datos reales de clientes. Nunca usar la clave `service_role` de Supabase en el acceso ordinario de usuarios. Si una credencial se expone, revocarla y sustituirla, incluso si luego se borra del archivo.

### Supabase y confirmación de correo

- Crear las tablas y políticas de documentos, clientes y pólizas siguiendo los scripts del proyecto; revisar antes de ejecutar operaciones SQL que modifican objetos existentes.
- Usar el bucket privado `documentos` y las políticas RLS que restringen datos y rutas al usuario autenticado. No desactivar RLS para resolver errores de permisos.
- Habilitar registro por email y confirmación; configurar la URL de la aplicación en las URL de autenticación permitidas de Supabase.
- El prototipo utiliza Gmail como SMTP personalizado de Supabase: `smtp.gmail.com`, puerto `587`, STARTTLS y contraseña de aplicación. La contraseña va en Supabase, no en el repositorio.
- El correo personal puede tener límites de envío y entregabilidad; evaluar un proveedor transaccional antes de comercializar.

### Google Calendar y Meet en la versión web

- Habilitar Google Calendar API en el proyecto de Google Cloud y configurar la pantalla de consentimiento OAuth.
- Crear un cliente OAuth de tipo **Aplicación web**. El cliente de tipo Escritorio utilizado en la prueba local no sustituye al cliente web.
- Registrar como URI de redirección autorizada exactamente la URL HTTPS indicada en `redirect_uri`.
- Si OAuth permanece en modo de prueba, añadir las cuentas autorizadas como usuarios de prueba; las autorizaciones pueden necesitar renovarse.
- Aplicar `calendario_web.sql` y mantener RLS en las tablas `aa_google_tokens` y `aa_google_oauth_states`. Los tokens se cifran con la clave Fernet de Secrets.
- Cada agente inicia sesión en Advisor’s Ally y autoriza su propia cuenta desde Calendario. Si el retorno de Google abre una nueva sesión de Streamlit, debe volver a entrar con el mismo usuario de la app para completar la vinculación.
- Los eventos con invitados deben revisarse antes de confirmar su creación. Calendar enviará las invitaciones correspondientes; la app puede bloquear horarios con eventos de día completo, incluso si se marcaron como disponibles.

## Uso y ejemplos

1. Crear una cuenta y confirmar el correo; después iniciar sesión.
2. Añadir un cliente de prueba y dos pólizas distintas; cargar PDF con texto, sin datos personales para una demostración pública.
3. En Consultar documentos, preguntar: «¿Qué exclusiones aparecen en los documentos de esta póliza?». Revisar las páginas citadas.
4. Preguntar por algo no incluido: la respuesta debe reconocer la falta de información, sin inventarla.
5. Conectar Google Calendar y crear una cita en un horario libre. Revisar título, zona horaria, destinatario, recordatorio y opción de Meet antes de confirmar.
6. En WhatsApp, revisar únicamente la simulación y los borradores; no esperar mensajes reales.

## Límites y errores previstos

- Los archivos Word e imágenes pueden almacenarse, pero la consulta documental actual trabaja con texto extraído de PDF. No incluye OCR de escaneos.
- Se admiten archivos de hasta 50 MB. La consulta acota el procesamiento a 40 PDF, 150 MB y 2.000 páginas; si se supera el alcance, reducir la documentación consultada.
- Si hay clientes o pólizas ambiguos, precisar el nombre o número; no mezclar documentación de otra cartera.
- Sin evidencia suficiente, Gemini debe indicarlo. Una respuesta es apoyo de consulta, no una resolución de siniestro ni asesoría contractual definitiva.
- Ante límites de Gemini o problemas de red, revisar el aviso y las fuentes locales; no insistir con reintentos indefinidos.
- Si aparece un error de carga de módulos JavaScript, verificar el despliegue, la versión fijada de Streamlit y probar una recarga o ventana de incógnito.

## Prueba y mejora

Penélope Romero probó el proyecto como usuaria: detectó que solo podía agregar una póliza, que la IA no encontraba automáticamente los documentos, que faltaba registro de cuentas y que la versión publicada tenía errores de carga. Se añadieron múltiples pólizas, consulta automática, registro con confirmación y ajustes del despliegue. También se probó la autorización de Google y se observó el bloqueo de citas por solapamientos.

Las comprobaciones locales con servicios simulados ayudan a revisar la lógica; no sustituyen una prueba real de permisos entre usuarios, entregabilidad de correo ni notificaciones de Google. No se documentó todavía la prueba de la app de un compañero ni feedback en el debate.

El contexto de trabajo está en `AGENTS.md`, reconstruido para esta entrega a partir de la conversación y del código. El documento académico incluye prompts reutilizables, cambios y cuatro evidencias visuales.

## Pendientes

1. WhatsApp: esperar la respuesta de Meta y completar después webhook, verificación de firmas, permisos, consentimiento, plantillas y envío real; añadir un programador persistente con control de duplicados antes de automatizar recordatorios.
2. Implementar Clasificar consultas.
3. Implementar Mis pendientes.
4. Antes de ofrecerlo a usuarios de pago, ampliar pruebas de aislamiento, manejo de cuotas, recuperación de acceso, consentimiento y operación continua.

## Pitch de cinco frases

Un agente de seguros pierde tiempo alternando entre pólizas en PDF, datos de clientes y su agenda para responder una sola consulta.
Advisor’s Ally reúne clientes, varias pólizas por persona, documentos privados y citas con Google Meet en una aplicación web.
Gemini ayuda a responder preguntas sobre los PDF ya cargados con referencias a sus páginas, mientras las reglas de fechas y Google Calendar apoyan el seguimiento de recordatorios.
Al probarla personalmente detecté que era necesario admitir más de una póliza por cliente y consultar documentos sin volver a seleccionarlos, y esas mejoras ya se incorporaron.
El siguiente paso es completar la conexión real con WhatsApp tras la respuesta de Meta y desarrollar Clasificar consultas y Mis pendientes.

## Entrega y mantenimiento

Antes de entregar, comprobar el enlace público en incógnito y en el móvil, confirmar que GitHub contiene el último commit y que el archivo de arranque sigue siendo `app.py`. Entregar en Blackboard un único documento con los ocho apartados solicitados. Mantener README y código sincronizados: las funciones pendientes deben seguir identificadas como tales.
