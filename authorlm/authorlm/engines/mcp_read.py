"""The read catalogue as an MCP server, for the claude-headless engine.

`python -m authorlm.engines.mcp_read -w <workspace> -m <manuscript>` serves
every entry of engines/catalogue.py as one MCP tool over stdio, and nothing
else: no write tool exists here. claude-headless starts it through
`--mcp-config` with `--strict-mcp-config`, so these tools, the web tools and
the structured-output tool are all the session has (claude_headless.py says
why this replaced a Bash allow-list).
"""

from __future__ import annotations

import argparse
import asyncio


def build_server(reader):
    import mcp.types as types
    from mcp.server.lowlevel import Server

    from . import catalogue as cat

    server = Server("authorlm-read")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [types.Tool(name=r.name, description=r.when,
                           inputSchema=r.input_schema()) for r in cat.CATALOGUE]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        text, _entry = cat.run(reader, name, arguments or {})
        return [types.TextContent(type="text", text=text)]

    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="authorlm-engine-read")
    parser.add_argument("-w", "--workspace", default=None)
    parser.add_argument("-m", "--manuscript", required=True)
    parser.add_argument("--refresh", action="store_true",
                        help="rebuild a stale summary before serving it "
                             "(catalogue.refresh_in_turns)")
    args = parser.parse_args(argv)

    from mcp.server.stdio import stdio_server

    from .. import paths
    from .catalogue import BookReader
    from .openai_api import _project_config

    reader = BookReader(args.workspace, args.manuscript,
                        config=_project_config(paths), refresh=args.refresh)
    server = build_server(reader)

    async def run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(run())


if __name__ == "__main__":
    main()
