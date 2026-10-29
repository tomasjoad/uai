# agent-core

Núcleo local del agente (fase F1 de [la arquitectura](../docs/ARQUITECTURA.md)):
FastAPI + WebSocket, orquestador LangGraph tras una fachada, Model Router
(Claude Haiku 5.5 por defecto), servidor MCP de ficheros propio sobre un sandbox
y gate de aprobación con reglas revocables. Todo el estado vive en un SQLite de la
carpeta de datos del usuario.

## Puesta en marcha

Requiere [uv](https://docs.astral.sh/uv/). Desde `agent-core/`:

```sh
uv sync
cp .env.example .env        # y rellena ANTHROPIC_API_KEY
uv run agent-core           # arranca el núcleo en http://127.0.0.1:8765
```

En otra terminal, el cliente de chat con el gate interactivo:

```sh
uv run agent-core-cli
```

Prueba algo como «crea notas/hola.txt con un saludo». Antes de escribir, el agente
pedirá permiso: `o` (una vez), `a` (siempre) o `r` (rechazar).

No arranques el núcleo con `uvicorn --reload` ni con varios workers. En Windows
uvicorn cambiaría a un bucle de eventos que no puede lanzar los servidores MCP.

## Dónde quedan los datos

En `%LOCALAPPDATA%\agent-core` (Windows) o el equivalente de cada sistema
(`AGENT_CORE_DATA_DIR` para cambiarlo):

- `agent.db`: sesiones, checkpoints, reglas de consentimiento y trail
- `api_token`: token que exige la API local (la CLI lo lee de aquí)
- `sandbox/`: única carpeta que el agente puede tocar

## API por curl

```sh
TOKEN=$(cat "$LOCALAPPDATA/agent-core/api_token")
curl -s -X POST localhost:8765/sessions -H "Authorization: Bearer $TOKEN"
curl -s -X POST localhost:8765/sessions/<id>/messages -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{"content":"lista el sandbox"}'
curl -s -X POST localhost:8765/sessions/<id>/approvals/<tool_call_id> -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d '{"decision":"once"}'
curl -s localhost:8765/trail -H "Authorization: Bearer $TOKEN"
```

El contrato completo está en el §7 de la arquitectura.

## Tests

```sh
uv run pytest
```

Usan un modelo con guion, el servidor MCP real como subproceso y un SQLite
temporal. `tests/test_models.py` incluye una prueba contra la API real de
Anthropic que solo se ejecuta si `ANTHROPIC_API_KEY` está definida.
