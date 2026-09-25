#!/usr/bin/env python3
"""Exercise two real MCP clients without printing personal brain contents."""
import asyncio
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check_client(number):
    params = StdioServerParameters(command=str(Path.home() / '.local/bin/gbrain-mcp'))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert {'search', 'get_page', 'list_pages'} <= tools
            assert not tools & {'capture', 'write_page', 'ingest', 'query', 'synthesize'}
            pages = await session.call_tool('list_pages', {'limit': 100})
            assert not pages.isError
            denied = await session.call_tool('list_pages', {'source_id': 'default', 'limit': 1})
            assert denied.isError, 'Other sources must remain inaccessible'
            denied_write = await session.call_tool('write_page', {'slug': 'verification-no-write'})
            assert denied_write.isError, 'Writes must not be exposed'
            search = await session.call_tool('search', {'query': 'project', 'limit': 3})
            assert not search.isError
            print(json.dumps({'client': number, 'tools': len(tools), 'list_pages': 'passed',
                              'keyword_search': 'passed', 'other_source': 'denied', 'write': 'denied'}))


async def main():
    await asyncio.gather(check_client(1), check_client(2))


if __name__ == '__main__':
    asyncio.run(main())
