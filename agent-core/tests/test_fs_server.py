import pytest
from mcp.server.fastmcp.exceptions import ToolError

from agent_core.mcp.fs_server import build_server


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    return build_server(root)


async def test_write_list_and_read(server):
    await server.call_tool("write_file", {"path": "notes/a.txt", "content": "hola"})
    listing = await server.call_tool("list_directory", {"path": "notes"})
    assert "[FILE] a.txt" in str(listing)
    content = await server.call_tool("read_file", {"path": "notes/a.txt"})
    assert "hola" in str(content)


@pytest.mark.parametrize("tool", ["read_file", "write_file", "create_directory", "list_directory"])
async def test_server_enforces_the_sandbox_itself(server, tool):
    args = {"path": "../outside.txt"}
    if tool == "write_file":
        args["content"] = "x"
    with pytest.raises(ToolError):
        await server.call_tool(tool, args)
