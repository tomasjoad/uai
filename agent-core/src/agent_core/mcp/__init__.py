"""MCP host and the bundled filesystem server.

Kept import-free: fs_server runs as `python -m agent_core.mcp.fs_server` and should
not pay for loading the host's LangChain dependencies.
"""
