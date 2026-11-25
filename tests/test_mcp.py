"""Start the real MCP server and exercise it with the official SDK client."""

import asyncio
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from archergate import media
from archergate.fixtures import generate_fixtures
from test_media import inspect_export


class MCPTests(unittest.TestCase):
    def test_stdio_client_discovers_and_invokes_tools(self):
        with TemporaryDirectory() as temp:
            workspace = Path(temp)
            generate_fixtures(workspace)
            asyncio.run(self.exercise_client(workspace))

    async def exercise_client(self, workspace):
        parameters = StdioServerParameters(
            command=sys.executable, cwd=media.ROOT,
            args=["-m", "archergate.mcp_server", "--workspace", str(workspace)],
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as client:
                identity = await client.initialize()
                self.assertEqual(identity.serverInfo.name, "ArcherGate Experiment 001")
                tools = await client.list_tools()
                self.assertEqual({tool.name for tool in tools.tools}, {
                    "inspect_assets", "inspect_edit", "compare_edits", "render_edit",
                })

                async def call(name, **arguments):
                    response = await client.call_tool(name, arguments)
                    self.assertFalse(response.isError, response.content)
                    return response.structuredContent

                assets = (await call("inspect_assets"))["assets"]
                self.assertEqual(set(assets), {"signal", "transit", "echo"})
                self.assertEqual(assets["signal"]["video"]["frames"], 72)
                self.assertIn("synthetic", assets["echo"]["provenance"])
                edit = await call("inspect_edit", edit="edit-b")
                self.assertEqual([cut["asset"] for cut in edit["cuts"]], ["echo", "signal", "transit"])
                comparison = await call("compare_edits", left="edit-a", right="edit-b")
                self.assertTrue(comparison["same_assets"])
                self.assertTrue(comparison["order_changed"])
                output = await call("render_edit", edit="edit-b")
                self.assertEqual(output["path"], "renders/edit-b.mp4")
                path = workspace / output["path"]
                self.assertEqual(media.sha256(path), output["sha256"])
                inspect_export(path, media.named_manifest("edit-b"), {
                    asset: row["sha256"] for asset, row in assets.items()
                })
                for source in assets.values():
                    self.assertEqual(media.sha256(workspace / source["path"]), source["sha256"])
                failure = await client.call_tool("render_edit", {"edit": "../registry"})
                self.assertTrue(failure.isError)
                self.assertIn("edit-a", failure.content[0].text)
                self.assertEqual(list((workspace / "renders").iterdir()), [path])
                print(f"MCP protocol {identity.protocolVersion}: discovered and invoked four tools", flush=True)
