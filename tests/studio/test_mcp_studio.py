import asyncio
import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from archergate.studio.library import Library
from archergate.studio import memory, timeline
from archergate.studio.fixtures import make_sources


class StudioMCP(unittest.TestCase):
    def test_real_client_calls_all_studio_tools(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            library = Library(root / "studio")
            assets = [library.import_media(source, "Synthetic", "Local fixture")
                      for source in make_sources(root / "fixtures")]
            edit = next(timeline.demo_timelines(assets))
            timeline.save(library, edit)
            memory.write(library, {"kind": "aesthetic", "title": "Room tone", "description": "Keep the pauses."})
            asyncio.run(self.exercise(library, edit))

    async def exercise(self, library, edit):
        settings = StdioServerParameters(command=sys.executable,
                                         args=["-m", "archergate.mcp_server", "--studio", str(library.root)])
        async with stdio_client(settings) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as client:
                await client.initialize()
                tools = {tool.name for tool in (await client.list_tools()).tools}
                self.assertTrue({"inspect_assets", "search_assets", "inspect_transcript", "creative_preferences",
                                 "validate_timeline", "export_timeline"} <= tools)

                async def call(tool, **arguments):
                    result = await client.call_tool(tool, arguments)
                    self.assertFalse(result.isError, result.content)
                    return result.structuredContent

                result = await call("search_assets", query="interview")
                self.assertEqual(len(result["assets"]), 1)
                asset = result["assets"][0]["id"]
                self.assertEqual((await call("inspect_transcript", asset=asset))["state"], "unavailable")
                self.assertEqual((await call("creative_preferences", kind="aesthetic"))["records"][0]["title"], "Room tone")
                self.assertTrue((await call("validate_timeline", manifest=edit))["valid"])
                output = await call("export_timeline", name=edit["id"])
                self.assertEqual(ET.parse(library.root / output["file"]).getroot().get("version"), "5")
                self.assertTrue((await client.call_tool("export_timeline", {"name": "../escape"})).isError)
