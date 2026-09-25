#!/usr/bin/env python3
"""Forward MCP through one authenticated local database owner for all AI clients."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

URL = 'http://127.0.0.1:3131'
TOOLS = {'search', 'get_page', 'list_pages', 'get_backlinks', 'list_link_sources',
         'traverse_graph', 'resolve_slugs', 'get_ingest_log', 'whoami',
         'get_recent_salience', 'find_anomalies'}


class LocalClientAuth(httpx.Auth):
    def __init__(self):
        path = Path.home() / '.config/gbrain-mcp/client.json'
        assert path.resolve() == path and path.stat().st_mode & 0o077 == 0
        self.credentials = json.loads(path.read_text())
        self.token = None
        self.expires = 0.0
        self.lock = asyncio.Lock()

    async def async_auth_flow(self, request):
        async with self.lock:
            if self.token is None or time.monotonic() >= self.expires:
                async with httpx.AsyncClient(trust_env=False) as client:
                    response = await client.post(URL + '/token', data={
                        'grant_type': 'client_credentials', **self.credentials})
                    response.raise_for_status()
                    data = response.json()
                    self.token = data['access_token']
                    self.expires = time.monotonic() + int(data.get('expires_in', 3600)) - 30
            request.headers['Authorization'] = 'Bearer ' + self.token
        yield request


async def main():
    # MCP launchers may provide only HOME/PATH; use the current user's session bus.
    runtime = Path('/run/user') / str(os.getuid())
    assert runtime.stat().st_uid == os.getuid() and (runtime / 'bus').exists()
    os.environ.setdefault('XDG_RUNTIME_DIR', str(runtime))
    os.environ.setdefault('DBUS_SESSION_BUS_ADDRESS', 'unix:path=' + str(runtime / 'bus'))
    subprocess.run(['systemctl', '--user', 'start', 'gbrain-mcp.service'], check=True,
                   stdout=subprocess.DEVNULL)
    async with httpx.AsyncClient(trust_env=False) as probe:
        for _ in range(100):
            try:
                response = await probe.get(URL + '/health', timeout=1)
                if response.is_success:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError('Local GBrain service did not become ready')
    async with httpx.AsyncClient(auth=LocalClientAuth(), trust_env=False, timeout=120) as http:
        async with streamable_http_client(URL + '/mcp', http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                server = Server('gbrain-local')

                @server.list_tools()
                async def list_tools():
                    return [tool for tool in (await session.list_tools()).tools if tool.name in TOOLS]

                @server.call_tool()
                async def call_tool(name, arguments):
                    if name not in TOOLS:
                        raise ValueError('This integration exposes only local read operations')
                    return await session.call_tool(name, arguments)

                async with stdio_server() as (incoming, outgoing):
                    await server.run(incoming, outgoing, server.create_initialization_options())


if __name__ == '__main__':
    asyncio.run(main())
