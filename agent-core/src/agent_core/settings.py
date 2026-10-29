"""Runtime settings: where user state lives and how the core is exposed.

Factory config ships inside the package (agent_core/config/). Everything the user
generates (database, sandbox, API token, config overrides) lives in the per-user
data directory, never in the install folder.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "agent-core"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765
    max_iterations: int = 6
    allowed_origins: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = os.environ.get("AGENT_CORE_DATA_DIR") or user_data_dir(APP_NAME, appauthor=False)
        origins = os.environ.get("AGENT_CORE_ALLOWED_ORIGINS", "")
        return cls(
            data_dir=Path(data_dir),
            port=int(os.environ.get("AGENT_CORE_PORT", "8765")),
            max_iterations=int(os.environ.get("AGENT_CORE_MAX_ITERATIONS", "6")),
            allowed_origins=frozenset(o.strip() for o in origins.split(",") if o.strip()),
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "agent.db"

    @property
    def sandbox_dir(self) -> Path:
        return self.data_dir / "sandbox"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "api_token"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def ensure_dirs(self) -> None:
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)

    def config_file(self, name: str) -> Path:
        """A copy in <data_dir>/config/ overrides the packaged default."""
        override = self.data_dir / "config" / name
        if override.exists():
            return override
        return Path(str(resources.files("agent_core") / "config" / name))
