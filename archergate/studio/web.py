"""Local browser shell over the shared workspace services."""

import json
import os
import re
import sqlite3
import webbrowser
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from archergate.media import ExperimentError
from archergate.web import PreviewHandler

from . import interchange, memory, timeline

ASSETS = Path(__file__).with_name("web_assets")
STATIC = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
HOST = re.compile(r"(?:127\.0\.0\.1|localhost|[a-z0-9-]+\.app\.github\.dev)(?::[0-9]{1,5})?", re.ASCII)


class StudioHandler(PreviewHandler):
    def __init__(self, *args, library, **kwargs):
        self.library = library
        super().__init__(*args, workspace=library.root, **kwargs)

    def json_response(self, status, body, send_body=True):
        self.respond(status, "application/json; charset=utf-8", json.dumps(body).encode(), send_body)

    def serve_route(self, send_body):
        if not HOST.fullmatch(self.headers.get("Host", "")):
            self.send_error(403)
            return
        try:
            request = urlsplit(self.path)
            route = request.path
            params = parse_qs(request.query)
            if route in STATIC:
                file, mime = STATIC[route]
                self.respond(200, mime + "; charset=utf-8", (ASSETS / file).read_bytes(), send_body)
            elif route == "/api/assets":
                keys = ("id", "name", "provenance", "source", "proxy")
                rows = self.library.search(params.get("q", [""])[0])
                self.json_response(200, [{key: row[key] for key in keys} for row in rows], send_body)
            elif route == "/api/timelines":
                self.json_response(200, timeline.list_timelines(self.library), send_body)
            elif route == "/api/memory":
                self.json_response(200, memory.records(self.library), send_body)
            elif route.startswith("/api/asset/"):
                parts = route.removeprefix("/api/asset/").split("/")
                asset = self.library.asset(parts[0])
                if len(parts) == 1:
                    self.json_response(200, asset, send_body)
                elif parts[1:] == ["proxy"]:
                    filename = asset["proxy"]["file"]
                    self.serve_media(self.library.path("media", asset["id"], filename),
                                     "video/mp4" if filename == "proxy.mp4" else "audio/mp4", send_body)
                elif parts[1:] == ["thumbnail"]:
                    self.serve_media(self.library.path("media", asset["id"], "thumbnail.jpg"), "image/jpeg", send_body)
                else:
                    self.send_error(404)
            else:
                self.send_error(404)
        except (ExperimentError, ValueError, OSError, sqlite3.Error) as error:
            self.json_response(400, {"error": str(error)}, send_body)

    def do_POST(self):
        if not HOST.fullmatch(self.headers.get("Host", "")) or self.headers.get("X-ArcherGate-Workspace") != "1":
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_000_000 or self.headers.get_content_type() != "application/json":
                raise ExperimentError("Expected a JSON request smaller than 1 MB.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ExperimentError("Expected a JSON object.")
            match self.path:
                case "/api/import":
                    data = self.library.import_media(body.get("path", ""), body.get("provenance"), body.get("rights"),
                                                     os.getenv("ARCHERGATE_WHISPER"), os.getenv("ARCHERGATE_WHISPER_MODEL"))
                case "/api/inspect":
                    data = timeline.inspect(self.library, body)
                case "/api/timelines":
                    data = timeline.save(self.library, body)
                case "/api/compare":
                    data = timeline.compare(self.library, body.get("left"), body.get("right"))
                case "/api/memory":
                    data = memory.write(self.library, body)
                case "/api/export":
                    name = body.get("name")
                    data = interchange.export(self.library, name)
                    xml = self.library.path("exports", Path(data["file"]).name).read_bytes()
                    self.respond(200, "application/xml", xml, True)
                    return
                case _:
                    self.send_error(404)
                    return
            self.json_response(200, data)
        except (ExperimentError, ValueError, TypeError, OSError, sqlite3.Error) as error:
            self.json_response(400, {"error": str(error)})


def serve(library, port, *, open_browser=False):
    if not 0 <= port <= 65535:
        raise ExperimentError("Choose a port between 0 and 65535.")
    with ThreadingHTTPServer(("127.0.0.1", port), partial(StudioHandler, library=library)) as server:
        address = f"http://127.0.0.1:{server.server_port}"
        print(f"ArcherGate studio at {address}", flush=True)
        if open_browser:
            webbrowser.open(address)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
