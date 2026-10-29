# Proyecto: Agente Local con GraphRAG de Perfil, A2A y MCP
## Documento de Arquitectura Consolidado (visión + decisiones + roadmap)

> Documento vivo y portable. Resume el diseño acordado en las sesiones de planificación.
> Pensado para retomar el contexto en otra conversación/IDE o con otro asistente.
> Estado: **F1 implementada** (`agent-core/`). Siguiente: F2 (empieza por decidir el
> backend del grafo, ver §4 y §11).
> Revisión 2026-10-27: integradas las conclusiones de la revisión de arquitectura
> (Kuzu archivado, seguridad de localhost, servidor MCP propio, SQLite único, etc.;
> ver §12, decisiones 14-24).

---

## 0. Resumen ejecutivo (TL;DR)

Producto: una **aplicación instalable** (desktop primero, móvil después) que corre
un **agente local** dueño de un **GraphRAG del perfil del usuario** (sus gustos,
decisiones, preferencias). El agente:
- Conversa con el usuario y **aprende de cada conversación** (proceso de reflexión
  que actualiza el grafo de perfil de forma incremental). **Esta es la tesis
  innovadora del producto.**
- Usa un **LLM intercambiable** (hoy remoto = Claude Haiku 5.5; mañana también local).
- Ejecuta **herramientas MCP** locales con un **gate de aprobación** interactivo.
- Puede **delegar en otros agentes** vía protocolo **A2A**.
- Actúa como **guardián de confianza**: ve todo el perfil pero **comparte el mínimo
  imprescindible** con el exterior (LLM remoto, otros agentes, apps terceras),
  siempre con **consentimiento explícito** para lo sensible.

Premisa de producto: **esconder la complejidad del sistema tras la simplicidad de
una sola app**. El usuario no ve Docker, ni servidores, ni puertos.

---

## 1. Requisitos originales del usuario

1. Servicio local con un agente que corre en la máquina del usuario.
2. Acceso a un **GraphRAG que se actualiza con el perfil** según las conversaciones.
3. Interacción con **otros agentes (protocolo A2A)**.
4. Interacción con **servidores MCP** que el usuario instale.
5. Un **loop que actualiza el propio estado** del agente según las conversaciones.
6. (Añadido en sesión) **Agente seleccionable**: poder usar un LLM local (en su
   puerto) o uno remoto más potente, de forma intercambiable.
7. (Añadido) **Un único agente principal** dueño del GraphRAG, intercambiable, que
   a su vez puede acceder a otros agentes si lo considera necesario.
8. (Añadido) **Embeddings fijos y locales**, pero cambiables en el futuro mediante
   un servicio que permita descargar modelos de embedding (desarrollo posterior).
9. (Añadido) Instalable como **una sola app** en Windows/Linux/Mac y, en el futuro,
   móvil. **Esconder la complejidad.**
10. (Añadido) **Minimización de datos**: el agente local comparte lo mínimo con el
    exterior. Futuro: **apps terceras** pueden solicitar scopes (p.ej. "películas
    favoritas") y el agente los facilita con **aprobación explícita** del usuario.
11. (Añadido) **Gobernanza de divulgación**: recordar qué se compartió con quién
    para no re-compartir datos que un tercero ya tiene (ejemplo: canciones ya dadas
    a Spotify; ni reenviarlas ni derivar "me gusta el grunge" si ya es inferible).

---

## 2. Principios de arquitectura (los "porqués" transversales)

- **Desacoplar para no quedar atrapados.** Cada pieza externa va detrás de una
  **fachada/interfaz propia**: el resto del código no conoce la implementación
  concreta. Aplica a: orquestador (LangGraph), LLM (Model Router), UI (carcasa
  desechable), grafo (backend intercambiable tras `ProfileStore`).
- **El agente local es el guardián de confianza.** Ve todo el perfil; comparte el
  mínimo; exige consentimiento para lo sensible.
- **Minimización de datos por defecto.** Hacia el LLM remoto, hacia A2A y hacia apps
  terceras: solo lo necesario, redactado/derivado cuando se pueda.
- **Un único mecanismo de consentimiento reutilizable** para: gate MCP (acciones),
  delegación A2A (contexto), y data broker (datos). No tres sistemas distintos.
- **Local-first y privacidad.** El perfil, el historial, el trail y los permisos
  viven en el dispositivo, en la **carpeta de datos del usuario** (nunca en la
  carpeta de instalación).
- **Localhost no es una frontera de confianza.** Cualquier web abierta en el
  navegador puede llamar a `127.0.0.1` y los WebSocket no tienen CORS. Toda
  llamada al núcleo lleva token y se validan `Host` y `Origin` (§7).
- **Los datos de las herramientas son datos, no instrucciones.** Lo que devuelven
  las tools (ficheros, respuestas A2A) puede contener inyecciones de prompt; el
  gate controla acciones, no impide que un dato llegue al prompt (§9.4).
- **Todo deja rastro en el trail.** Auditoría + materia prima para el perfil.
- **Defensa en profundidad.** Ninguna barrera es única: p.ej. el sandbox lo aplican
  a la vez el gate y el propio servidor MCP.

---

## 3. Arquitectura (visión global)

```
┌───────────────────────────────────────────────────────────┐
│  (Futuro) CARCASA: Tauri desktop / app móvil — capa fina    │
│            habla por HTTP/WS con el núcleo (localhost)       │
└───────────────────────────────┬───────────────────────────┘
                                ▼  localhost + token + Host/Origin
┌───────────────────────────────────────────────────────────┐
│  NÚCLEO DEL AGENTE — una app, SIN Docker                    │
│  (un proceso principal + servidores MCP como subprocesos)   │
│                                                             │
│  FastAPI (REST + WebSocket)                                 │
│        │                                                    │
│        ▼                                                    │
│  FACHADA DEL ORQUESTADOR (tipos neutros)                    │
│   └ LangGraph: retrieve → reason → gate → act → … → reflect │
│   ├─ MODEL ROUTER ──► LLM activo (Haiku 5.5 remoto)         │
│   ├─ MEMORIA (F2): ProfileStore → grafo de perfil           │
│   │     (backend a decidir en F2) + embeddings locales      │
│   ├─ CONSENT ENGINE: reglas (tool, patrón) + gate           │
│   ├─ MCP host ──► servers MCP locales (stdio)  [GATE]       │
│   └─ A2A client (F5) ──► delega en otros agentes            │
│                                                             │
│  LOOP DE REFLEXIÓN (F3): lee conversación + trail →         │
│     extrae gustos/decisiones → escribe en el grafo →        │
│     actualiza el perfil   (LA PARTE INNOVADORA)             │
│                                                             │
│  Estado local: SQLite único (checkpoints, sesiones, reglas, │
│  trail) + sandbox, en la carpeta de datos del usuario       │
└───────────────────────────────┬───────────────────────────┘
                                │ sale: la conversación del turno,
                                │ resultados de tools y el contexto
                                ▼ mínimo del perfil
                        ☁ LLM remoto (Claude Haiku 5.5)
            (no guarda estado de sesión; sí aplica su política de retención)
```

Dos "externos", solo uno es remoto:
- **El LLM (Haiku)**: remoto (nube). Es el único que sale del dispositivo.
- **El "servidor" del estado de sesión** = el **núcleo local** (proceso en tu
  propia máquina que escucha en `localhost`). NO es un servidor en la nube.

---

## 4. Decisiones tecnológicas (con justificación y alternativas)

| Capa | Decisión | Por qué / Alternativas descartadas |
|---|---|---|
| **Orquestación** | **LangGraph** (librería, MIT) **tras una fachada propia** | Checkpointer persistente, `interrupt()`/`Command(resume=…)` para el gate, reducers y streaming. **NO** usamos la plataforma de pago de LangChain. La fachada (`Orchestrator`) expone eventos y dicts neutros; los tipos de LangChain/LangGraph no salen de `orchestrator/` y `models/`. |
| **LLM (F1)** | **Claude Haiku 5.5** (`claude-haiku-5-5`) vía Model Router | Barato (0,10 $/0,50 $ por MTok hasta 100K de prompt), contexto 1M, bueno en tool-calling. Thinking adaptativo activado por defecto; profundidad con `effort` (por defecto `medium`). **No admite `temperature`/`top_p` no por defecto**. Sin fallback de servidor ante `refusal`: lo trata el nodo `reason`. |
| **Abstracción LLM** | **Model Router** propio sobre `model_profiles.yaml` | Claude va por **ChatAnthropic** (usa el SDK oficial `anthropic`), **nunca por la API compatible con OpenAI** (pierde caching, thinking/effort y `stop_details`). Modelos locales (Ollama/LM Studio/vLLM) por **ChatOpenAI con `base_url`**. Selección global + por sesión. |
| **GraphRAG (F2)** | **Graphiti como candidato; backend a decidir al empezar F2**, tras la interfaz `ProfileStore` | **Kuzu está archivado** (10-oct-2025, compra por Apple; última versión 0.11.3) y **Graphiti lo ha marcado deprecated**. **FalkorDB Lite no funciona nativo en Windows** (solo WSL2) → descartado para la app de escritorio. Opciones a probar en F2: (a) Graphiti + **LadybugDB** (fork de Kuzu, vía alias de módulo sobre el driver deprecated), (b) **grafo temporal propio sobre SQLite + sqlite-vec** (máxima portabilidad: Windows, móvil, Rust), (c) Neo4j servidor (choca con "una sola app"). Microsoft GraphRAG sigue descartado (batch). |
| **Embeddings (F2)** | Locales, **fijos y multilingües**, en CPU | `nomic-embed-text` v1.5 está orientado a inglés y `bge-m3` pesa ~2 GB: para español en app instalable, `multilingual-e5-small/base` o similar (ONNX). **Cambiar de embeddings ⇒ re-indexar**. Gestor de modelos = F6. |
| **A2A (F5)** | **a2a-sdk** (cliente; servidor/Agent Card opcional) | El agente principal es coordinador; **no comparte su memoria** (solo el contexto de la tarea). |
| **MCP** | SDK `mcp` + `langchain-mcp-adapters`; **servidor filesystem propio en Python (FastMCP, stdio)** | El de referencia (`@modelcontextprotocol/server-filesystem`) es Node → exigiría Node instalado. El propio son ~80 líneas, aplica el sandbox por su cuenta y controlamos qué tools expone. **El LLM propone, el agente local ejecuta.** Sesión stdio persistente por servidor. |
| **API/servicio** | **FastAPI** (REST + **WebSocket**) en `127.0.0.1` | WS = canal principal (gate interactivo). REST con el mismo gate para CLI/curl. Token por instalación + validación de `Host` y `Origin`. Sin access log (el token puede ir en la query del WS). |
| **Estado/persistencia** | **Un SQLite** en la carpeta de datos | Checkpointer `langgraph-checkpoint-sqlite` + tablas propias (sesiones, reglas, trail). Una aprobación pendiente sobrevive a un reinicio. |
| **Entorno Python** | **uv** (`pyproject.toml` + `uv.lock`), Python 3.12 | Rápido, lockfile reproducible (clave para empaquetar el sidecar). |
| **Empaquetado** | Una app, sin Docker. Carcasa **Tauri (sidecar)** después | **uvicorn sin `--reload` y con un solo worker**: con cualquiera de los dos, en Windows usa `SelectorEventLoop`, que no puede lanzar los subprocesos MCP por stdio. |

---

## 5. El loop del agente (grafo de estado)

```
START ─► retrieve ─► reason ─► ¿tool calls? ──no──► reflect ─► END
         (F2: grafo)  (LLM)        │sí                 ▲
                                   ▼                   │
                                  gate ─► act ─► ¿iteración < máx? ─sí─► reason
                          (pausa si hace      │no
                           falta aprobación)  ▼
                                            limit ─► reflect
```

- `retrieve`: F1 = stub; F2 = consulta el perfil (contexto mínimo relevante).
- `reason`: el LLM decide responder o proponer tools. Marca `halt_reason="refusal"`
  si el modelo rechaza; nunca deja un turno de asistente vacío en el historial.
- `gate`: evalúa cada tool propuesta (§6) y **pausa con `interrupt()`** si alguna
  necesita aprobación. Todo lo anterior al `interrupt()` es sin efectos (se
  re-ejecuta al reanudar).
- `act`: ejecuta las aprobadas; las rechazadas/bloqueadas vuelven al LLM como
  `tool_result` de error. Todos los resultados en una sola actualización.
- `limit`: tope `max_iterations` anti-bucle → `halt_reason="max_iterations"`.
- `reflect`: F1 = stub; F3 = consolida la conversación al perfil.

### AgentState (implementado en F1)
```python
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]  # incluye tool calls y ToolMessages
    model_profile: str
    retrieved_context: list[str]          # F2: hechos del perfil
    iteration: int
    max_iterations: int
    decisions: dict[str, dict]            # tool_call_id → {decision, reason} (lo fija el gate)
    halt_reason: str | None               # "max_iterations" | "refusal"
```
Simplificado respecto al borrador: las tool calls pendientes y sus resultados ya
están en `messages` (`AIMessage.tool_calls`, `ToolMessage`), y "esperando
aprobación" se obtiene del `interrupt()` pendiente en el checkpoint.
Ámbitos: **working** (1 turno) / **session** (la conversación) / **long-term**
(el perfil, que vive FUERA del state, en el grafo).

---

## 6. Gate de aprobación (consentimiento de acciones)

Patrón tipo "permisos de Cline". Para cada tool propuesta, el **Consent Engine**
devuelve un veredicto:
- **allow**: una regla activa la cubre → se ejecuta sin preguntar (decisión `auto`).
- **deny**: bloqueada por diseño (p.ej. ruta fuera del sandbox, tool desconocida) →
  no se pregunta y ninguna regla puede permitirla.
- **ask**: el núcleo **pausa** y emite `approval_required`.

Decisiones del usuario: **[Aprobar una vez] `once` · [Aprobar SIEMPRE] `always` ·
[Rechazar] `reject`** (mismo vocabulario en REST y WS).
- `always` crea una **regla** `(tool_name, patrón)`; por defecto el patrón es `**`
  (todo el sandbox), o el que envíe el cliente.
- **Patrones**: globs `fnmatch` sobre la ruta relativa al sandbox, tras
  normalizarla (`..`, symlinks/junctions, mayúsculas en Windows). `*`/`**` cubren
  cualquier llamada de la tool.
- **Qué argumento es el "recurso"** se declara por servidor en `mcp_servers.json`
  (`"resource": {"arg": "path", "root": "{sandbox}"}`).
- **Lotes**: Claude puede proponer varias tools a la vez. El gate pide todas las
  aprobaciones del lote y reanuda cuando están todas respondidas.
- **Rechazos** vuelven al LLM como `tool_result` de error, para que siga sin ellas.
- **Trail**: cada decisión (con su `rule_id` si la hay) y cada resultado quedan
  registrados. **[Revocar acceso]** = `DELETE /rules/{id}` → la próxima vez vuelve
  a pausar.
- **Persistencia**: la pausa vive en el checkpoint; sobrevive a reinicios y a
  desconexiones del cliente (`GET /sessions/{id}` devuelve lo pendiente).
- **Defensa en profundidad**: el servidor MCP filesystem también rechaza rutas
  fuera del sandbox.
- Es el **mismo motor de consentimiento** que se reutiliza para A2A y para el data
  broker (§9), en direcciones distintas:
  - Gate MCP = controla **acciones** que salen hacia fuera.
  - Data broker = controla **datos** que salen hacia terceros.

---

## 7. API HTTP/WS (contrato del núcleo local, implementado en F1)

Seguridad (todas las rutas salvo `/health`):
- **Token** por instalación en `<datos>/api_token` (lo leen la CLI y, en F6, la
  carcasa). REST: `Authorization: Bearer <token>`. WS: misma cabecera o `?token=`
  (los navegadores no pueden poner cabeceras en un WebSocket).
- **Host** debe ser `127.0.0.1`, `localhost` o `[::1]` (anti DNS rebinding).
- **Origin**, si viene, debe estar en `AGENT_CORE_ALLOWED_ORIGINS` (p.ej. el de la
  carcasa Tauri). Por defecto ninguno.

### REST (control)
```
GET    /health                                   → { status, version, default_model_profile }
GET    /profiles                                 → perfiles (sin secretos)
POST   /sessions                {profile?}       → { session_id, model_profile }
GET    /sessions/{id}                            → { session_id, model_profile, iteration,
                                                     message_count, pending_approvals[] }
DELETE /sessions/{id}
POST   /sessions/{id}/messages  {content}        → TurnResult
POST   /sessions/{id}/approvals/{tool_call_id}
                                {decision: once|always|reject, pattern?} → TurnResult
GET    /mcp/tools                                → [{ name, server, description, requires_approval }]
GET    /rules                                    → reglas activas
DELETE /rules/{rule_id}                          → revocar
GET    /trail?session_id=&limit=                 → entradas del trail

TurnResult = { status: completed|awaiting_approval|error, reply, halt_reason,
               tool_calls[], tool_results[], pending_approvals[], errors[] }
```
Si una aprobación de un lote no es la última, el `TurnResult` devuelve
`awaiting_approval` con el resto pendiente.

### WebSocket (chat + gate)
```
WS /sessions/{id}/stream
→  { type:"user_message", content }
→  { type:"approval", tool_call_id, decision:"once|always|reject", pattern? }
←  { type:"token", data }                                   # streaming de respuesta
←  { type:"tool_call", data:{id,name,args,requires_approval} }
←  { type:"approval_required", data:{tool_call_id,name,server,args} }   # pausa del gate
←  { type:"tool_result", data:{id,ok,content} }
←  { type:"done", data:{reply, halt_reason} }
←  { type:"error", data:{message} }
```

### Sesiones = con estado en el NÚCLEO LOCAL
El cliente solo manda el mensaje nuevo + `session_id`; el núcleo local guarda
historial/AgentState/checkpoints/aprobaciones. El LLM remoto **no** guarda estado
de sesión: en cada turno recibe el historial completo de la conversación.

---

## 8. Estrategia de empaquetado y camino a móvil

- **Núcleo** = un proceso principal (FastAPI + LangGraph + SQLite) más los
  servidores MCP como subprocesos stdio que él gestiona. Sin Docker.
- **Carcasa desktop** = **Tauri + sidecar Python** (capa fina y desechable que habla
  por `localhost` con el token). Se añade DESPUÉS del núcleo.
- **Instalador único** por plataforma; datos en la carpeta de usuario
  (`platformdirs`: `%LOCALAPPDATA%\agent-core` en Windows, etc.).
- Pendiente F6: hoy el servidor MCP propio se lanza con `python -m …`; en un
  ejecutable congelado (PyInstaller/Briefcase) habrá que lanzarlo como modo del
  propio ejecutable.
- **Móvil — dos caminos (el obstáculo real es Python en móvil, no LangGraph):**
  - **Camino 1 (inmediato):** app móvil como **carcasa fina** que se conecta por
    red al núcleo que corre en el PC/VPS del usuario. Cero reescritura. Requiere
    conectividad (no offline) y exponer el núcleo con autenticación fuerte.
  - **Camino 2 (offline/nativo):** reescribir `orchestrator/` (y `models/`) en un
    lenguaje portable (p.ej. **Rust**). Barato gracias a la **fachada**. Un backend
    de grafo sobre SQLite facilitaría mucho este camino.
- **Por eso la fachada del orquestador es fina y agnóstica desde F1**
  (`create_session`, `start_turn`, `resume_after_approval`, `get_state`,
  `delete_session`, `list_tools`), con eventos neutros.

---

## 9. Gobernanza de datos y broker de consentimiento (la capa de confianza)

### 9.1 Scopes = vistas consultables del perfil (NO etiquetar dato a dato)
Tres capas de etiquetado:
1. **Tipado** del modelo de perfil: un scope es un **filtro sobre tipos de
   entidad/relación**. Ojo: Graphiti no crea relaciones tipadas (`:LE_GUSTA`);
   guarda todas las aristas como `RELATES_TO` con un campo `name`, y las etiquetas
   de nodo dependen del backend. Por eso **los scopes se expresan sobre la interfaz
   `ProfileStore`**, no en Cypher a mano.
2. **Metadatos de gobernanza por nodo** (pocos y estables): `sensibilidad`
   (baja/media/alta), `dominio` (p.ej. "musica"), `compartible` (bool).
3. **Catálogo de scopes declarativo** (`config/scopes.yaml`): nombre legible →
   filtro + política (`sensibilidad_max`, `requiere_aprobacion`, `derivable_de`).
   Añadir un scope = una entrada en el YAML. El usuario consiente sobre un
   **nombre legible**.

```yaml
# config/scopes.yaml (ejemplo; sintaxis del filtro a cerrar en F2/F7)
scopes:
  canciones_favoritas:
    descripcion: "Tus canciones marcadas como favoritas"
    filtro: { relacion: LE_GUSTA, entidad: Cancion }
    sensibilidad_max: baja
    requiere_aprobacion: una_vez
  generos_musicales:
    descripcion: "Géneros musicales que prefieres"
    filtro: { relacion: PREFIERE, entidad: Genero, dominio: musica }
    sensibilidad_max: baja
    derivable_de: [canciones_favoritas]   # redundante si ya tiene las canciones
```

### 9.2 Disclosure ledger = recordar qué se compartió con quién
**En SQLite, no dentro del grafo**: el motor del grafo puede deduplicar, fusionar o
invalidar nodos y aristas, y eso rompería un ledger guardado ahí. El ledger es
auditable, transaccional y fácil de consultar por hash/delta:
```
disclosures(id, app, scope, grant_id, fecha, hash_contenido, ids_nodos, vigente)
grants(id, app, scope, concedido_en, caduca_en, revocado_en)
```
Flujo cuando una app vuelve a pedir un scope:
1. ¿Grant vigente para (app, scope)? Si no → pedir consentimiento.
2. Ejecutar el filtro del scope → set actual; filtrar por sensibilidad/`compartible`.
3. **¿Derivable de algo que la app YA tiene?** → omitir o preguntar.
4. **Hash del set actual vs. hash en el ledger:** igual → no reenvía; distinto →
   **delta** → compartir **solo lo nuevo**.
5. Entregar. Para **datos planos** (conexión directa) **sin pasar por el LLM**.
6. Registrar en el ledger + anotar en el trail.

Dos modos de entrega a terceros:
- **Mediado por el agente**: cuando hace falta inteligencia (filtrar/contextualizar).
- **Conexión directa (data API)**: dato plano por scope, **sin LLM**.

Ciclo de vida del permiso: **revocable** (revocar no borra lo que la app ya copió,
pero corta accesos futuros y queda auditado), **TTL/caducidad**, y
**re-consentimiento** si el contenido cambia mucho o caduca el grant.

### 9.3 Fronteras de privacidad
```
SIEMPRE LOCAL, no sale sin permiso: perfil completo, historial, trail,
                                     reglas/grants, categorías sensibles.
SALE, MÍNIMO Y CON POLÍTICA:
  · al LLM remoto      → la conversación del turno + resultados de tools
                          + el fragmento de perfil necesario (redactado/derivado)
  · a otros agentes A2A → solo el contexto de la tarea
  · a apps terceras     → solo el scope concedido, con consentimiento
```
Matices que hay que asumir explícitamente:
- Con LLM remoto, **la conversación sale entera en cada turno** y queda sujeta a la
  política de retención del proveedor. La minimización real aplica a **lo que sale
  del perfil**, no a lo que el usuario escribe.
- **La reflexión (F3) es el proceso más sensible.** Graphiti (y cualquier extractor
  con LLM) envía al modelo el contenido del episodio y nodos existentes del perfil
  para deduplicar. Decisión pendiente para F2/F3: **extracción con LLM local** o
  aceptar y documentar que usa el remoto. El routing por tarea del Model Router
  deja de ser opcional para convertirse en pieza de privacidad.

### 9.4 Amenazas explícitas
- **Inyección de prompts** vía resultados de tools (ficheros del sandbox,
  respuestas A2A). Mitigaciones F1: el system prompt declara los resultados como
  datos; toda acción pasa por el gate. El gate **no** impide que un dato llegue al
  prompt.
- **Envenenamiento del perfil (F3).** Un fichero que diga "al usuario le encanta X"
  podría acabar como preferencia. La reflexión debe guardar la **procedencia** de
  cada hecho y distinguir lo dicho por el usuario de lo que devolvió una tool.
- **Webs maliciosas contra localhost** → token + `Host` + `Origin` (§7).

---

## 10. Estructura del proyecto (repo `uai`)

```
uai/
├─ docs/ARQUITECTURA.md          # este documento
└─ agent-core/                   # núcleo (F1)
   ├─ pyproject.toml / uv.lock   # gestionado con uv, Python 3.12
   ├─ .env.example               # ANTHROPIC_API_KEY, AGENT_CORE_*
   ├─ src/agent_core/
   │  ├─ main.py                 # create_app() + run() (uvicorn sin reload)
   │  ├─ settings.py             # carpeta de datos, puerto, orígenes permitidos
   │  ├─ sandbox.py              # confinamiento de rutas (gate + servidor MCP)
   │  ├─ config/                 # configuración de fábrica (sobrescribible en <datos>/config/)
   │  │  ├─ model_profiles.yaml  # remote-haiku (activo); local-ollama (hueco F6)
   │  │  └─ mcp_servers.json     # servers MCP locales + qué argumento es el recurso
   │  ├─ api/                    # REST + WS + seguridad localhost
   │  ├─ orchestrator/           # FACHADA + grafo LangGraph + AgentState
   │  ├─ models/                 # Model Router
   │  ├─ consent/                # motor de consentimiento (reglas, veredictos)
   │  ├─ mcp/                    # host MCP + servidor filesystem propio
   │  ├─ storage/                # SQLite: sesiones, reglas, trail
   │  └─ cli.py                  # cliente de terminal (chat + gate)
   └─ tests/
   # Por llegar: memory/ (F2), reflection/ (F3), a2a/ (F5), broker/ (F7)

<carpeta de datos del usuario>/   # %LOCALAPPDATA%\agent-core, ~/.local/share/agent-core, …
├─ agent.db                      # checkpoints + sesiones + reglas + trail
├─ api_token
├─ sandbox/                      # área de trabajo del MCP filesystem
└─ config/                       # (opcional) copias que sobrescriben las de fábrica
```

---

## 11. Roadmap por fases

- **F1 — Núcleo mínimo** ✅ *(implementada)*
  FastAPI + WebSocket + LangGraph tras fachada + Model Router → Haiku 5.5 +
  servidor MCP filesystem propio sobre el sandbox con **gate interactivo**
  (lotes, allowlist, revocación, denegación por diseño) + SQLite único + trail
  estructurado + seguridad de localhost + CLI. Sin grafo (se decide en F2).
  Pendiente de F1: ejecutar la prueba real contra la API de Anthropic
  (`tests/test_models.py`, necesita `ANTHROPIC_API_KEY`).
- **F2 — GraphRAG**
  **Empieza con una prueba corta para elegir el backend** (LadybugDB vía Graphiti
  vs. grafo temporal propio sobre SQLite). Interfaz `ProfileStore`. **Esquema del
  perfil** (Usuario, Preferencia, Decisión, Tema, Proyecto, Objetivo,
  Canción/Película/Libro…) con metadatos de gobernanza (`sensibilidad`, `dominio`,
  `compartible`). Nodos `retrieve`/`write`; escritura incremental. Embeddings
  locales multilingües. Minimización hacia el LLM ("context shaping"). Decidir
  LLM local vs. remoto para la extracción.
- **F3 — Reflexión / auto-estado (LA PARTE INNOVADORA)**
  Worker que lee conversación + trail → extrae gustos/decisiones/preferencias →
  escribe en el perfil (dedup + invalidación temporal) **con procedencia**. Las
  **decisiones del gate** (qué aprobó/rechazó/allowlist) son señal de perfil.
- **F4 — MCP ampliado**
  Varios servers desde `mcp_servers.json`; políticas del gate por tool (p.ej.
  usando las anotaciones `readOnlyHint`/`destructiveHint`).
- **F5 — A2A**
  Cliente para descubrir/invocar otros agentes + "cuándo delegar" + minimización
  de contexto al delegar (pasa por el motor de consentimiento) + Agent Card propia
  (opcional).
- **F6 — Producto / portabilidad**
  Carcasa **Tauri (sidecar)** con el token; **Embedding Manager**; **perfil de LLM
  local** (Ollama/LM Studio); lanzamiento de servers MCP en el ejecutable
  congelado; auth MCP/A2A; observabilidad; camino a **móvil**.
- **F7 — Data Broker / Consent API**
  Catálogo de scopes; motor de consentimiento (reutiliza el del gate);
  **disclosure ledger en SQLite** con hashing/delta; derivabilidad; vigencia/
  revocación/TTL; modos mediado y conexión directa.

---

## 12. Decisiones cerradas (changelog)

1. Proyecto greenfield (independiente del repo de PDFs/KAIA). Vive en el repo
   `uai`; el prototipo Node previo se descartó.
2. Orquestación: **LangGraph (MIT) tras fachada propia**. Reemplazable a futuro.
3. LLM F1: **Claude Haiku 5.5** (`claude-haiku-5-5`) vía **Model Router**
   (perfiles YAML, selección global + por sesión). Hueco para LLM local en F6.
4. ~~GraphRAG: Graphiti sobre Kuzu embebido~~ → **sustituida por la 14**.
5. Embeddings: **locales fijos** (CPU), **multilingües**. Gestor de modelos → F6.
6. MCP: clientes en el agente local; servers locales; **el LLM propone, el agente
   local ejecuta**.
7. **Gate interactivo** que pausa + "aprobar siempre" (allowlist) + **revocación
   desde el trail**. Granularidad `(tool, patrón)`. Motor reutilizable.
8. A2A: agente principal = coordinador/cliente; **no comparte memoria**.
9. API: **WebSocket principal** + REST para test. **Sesiones con estado en el
   núcleo local**.
10. Entorno Python: **uv**, Python 3.12.
11. Empaquetado: **una sola app**, sin Docker; carcasa **Tauri (sidecar)** después;
    móvil por carcasa-remota o núcleo Rust.
12. **Minimización de datos** como tesis: guardián de confianza.
13. **Broker de consentimiento con scopes** y **disclosure ledger** para no
    re-compartir datos que un tercero ya tiene.
14. **Grafo fuera de F1; backend elegido al empezar F2** (Kuzu archivado y
    deprecated en Graphiti; FalkorDB Lite sin Windows nativo).
15. **Un único SQLite** en la carpeta de datos: checkpointer + sesiones + reglas +
    trail. Configuración de fábrica en el paquete; estado del usuario fuera.
16. **Servidor MCP filesystem propio en Python** (FastMCP, stdio) que aplica el
    sandbox por sí mismo.
17. **Seguridad de localhost**: token por instalación + `Host` loopback + `Origin`
    en lista blanca.
18. **Claude por ChatAnthropic, no por API compatible con OpenAI.** Tipos de
    LangChain confinados a `orchestrator/` y `models/`.
19. **Vocabulario único de decisión** `once|always|reject`; aprobaciones bajo la
    sesión (`/sessions/{id}/approvals/{tool_call_id}`); el REST síncrono también
    pausa (`awaiting_approval`).
20. **Lotes de tools**: el gate resuelve el lote completo; rechazos como
    `tool_result` de error.
21. **uvicorn sin reload ni varios workers** (Windows + subprocesos MCP).
22. **Disclosure ledger en SQLite**, no en el grafo.
23. **Scopes sobre `ProfileStore`**, no en Cypher sobre el modelo interno del grafo.
24. **Amenazas explícitas**: inyección de prompts y envenenamiento del perfil
    (procedencia obligatoria en F3).

---

## 13. Preguntas abiertas / a decidir más adelante

- Backend del grafo (LadybugDB vía Graphiti vs. grafo propio sobre SQLite) — inicio F2.
- Extracción del perfil con LLM local o remoto (privacidad vs. calidad/coste) — F2/F3.
- Modelo de embeddings concreto (multilingüe, tamaño) — F2.
- Esquema detallado del perfil (entidades/relaciones concretas) — F2.
- Política de "cuánto del perfil se envía al LLM remoto" por perfil de modelo — F2.
- Granularidad fina del allowlist: ¿`(tool, patrón)` basta o hace falta por
  argumento concreto en algún caso?
- Modelo de identidad/autenticación de apps terceras en el data broker — F7.
- Gestor de empaquetado del sidecar (PyInstaller/Briefcase) y cómo lanzar los
  servers MCP desde el ejecutable congelado — F6.
- Lenguaje del núcleo portable para móvil offline (Rust probable) — F6/posterior.

---

## 14. Cómo retomar este proyecto (nota para otra conversación/IDE/modelo)

> "Estamos construyendo un **agente local** (app instalable, desktop primero) con un
> **GraphRAG de perfil que se auto-actualiza**, **LLM intercambiable** (hoy Claude
> Haiku 5.5 remoto vía Model Router), **MCP con gate de aprobación** y **A2A** para
> delegar. El agente es un **guardián de confianza** que minimiza lo que comparte
> y gestiona **consentimiento + disclosure ledger** para apps terceras.
> Orquestación con **LangGraph tras una fachada propia**. Entorno **uv**.
> **F1 está implementada** en `agent-core/` (núcleo con WebSocket, gate, MCP
> filesystem propio, SQLite, seguridad de localhost). **Siguiente: F2**, que empieza
> eligiendo el backend del grafo (Kuzu está archivado). Ver este documento para el
> detalle de decisiones y roadmap."
