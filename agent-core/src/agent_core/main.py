"""Process entry point: wires storage, MCP, models and the orchestrator behind FastAPI."""

from __future__ import annotations

import logging
import sys
from contextlib import AsyncExitStack, asynccontextmanager

import aiosqlite
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agent_core import __version__
from agent_core.api import rest, ws
from agent_core.api.security import LocalOnlyMiddleware, load_or_create_token
from agent_core.consent import ConsentEngine
from agent_core.mcp.host import McpHost
from agent_core.models import ModelRouter
from agent_core.orchestrator import Orchestrator, SessionNotFound, UnknownProfile, build_graph
from agent_core.settings import Settings
from agent_core.storage import Store

log = logging.getLogger("agent_core")


def create_app(settings: Settings | None = None, *, models: ModelRouter | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()
        async with AsyncExitStack() as stack:
            store = await Store.open(settings.db_path)
            stack.push_async_callback(store.close)
            checkpoint_conn = await aiosqlite.connect(settings.db_path)
            stack.push_async_callback(checkpoint_conn.close)
            checkpointer = AsyncSqliteSaver(checkpoint_conn)
            await checkpointer.setup()

            mcp = await McpHost.start(
                settings.config_file("mcp_servers.json"),
                {"{python}": sys.executable, "{sandbox}": str(settings.sandbox_dir)},
            )
            stack.push_async_callback(mcp.aclose)

            router = models or ModelRouter.from_file(settings.config_file("model_profiles.yaml"))
            consent = ConsentEngine(store)
            graph = build_graph(router=router, mcp=mcp, consent=consent, store=store, checkpointer=checkpointer)
            app.state.token = load_or_create_token(settings.token_path)
            app.state.store = store
            app.state.models = router
            app.state.consent = consent
            app.state.orchestrator = Orchestrator(
                graph=graph,
                store=store,
                router=router,
                consent=consent,
                mcp=mcp,
                max_iterations=settings.max_iterations,
            )
            log.info("Data folder: %s (API token in %s)", settings.data_dir, settings.token_path.name)
            yield

    app = FastAPI(title="agent-core", version=__version__, lifespan=lifespan)
    app.add_middleware(LocalOnlyMiddleware, allowed_origins=settings.allowed_origins)
    app.include_router(rest.public)
    app.include_router(rest.router)
    app.include_router(ws.router)

    @app.exception_handler(SessionNotFound)
    async def session_not_found(request: Request, exc: SessionNotFound) -> JSONResponse:
        return JSONResponse({"detail": "Session not found"}, status_code=404)

    @app.exception_handler(UnknownProfile)
    async def unknown_profile(request: Request, exc: UnknownProfile) -> JSONResponse:
        return JSONResponse({"detail": f"Unknown model profile: {exc}"}, status_code=400)

    return app


def run() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    # No reload and a single worker: with either, uvicorn switches Windows to a
    # SelectorEventLoop, which cannot spawn the MCP stdio subprocesses.
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False)
