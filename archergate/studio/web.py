"""Local browser shell over the shared workspace services."""

import json
import os
import re
import sqlite3
import webbrowser
import hmac
import sys
import hashlib
from functools import partial
from http.server import ThreadingHTTPServer
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from threading import Lock, Thread
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from archergate.media import ExperimentError
from archergate.web import PreviewHandler

from . import interchange, memory, preview, reasoning, timeline
from .library import run

ASSETS = Path(__file__).with_name("web_assets")
STATIC = {
    "/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css"),
    "/Hauora-Regular.woff2": ("Hauora-Regular.woff2", "font/woff2"),
    "/Hauora-Bold.woff2": ("Hauora-Bold.woff2", "font/woff2"),
    "/IBMPlexSans-Regular.woff2": ("IBMPlexSans-Regular.woff2", "font/woff2"),
    "/IBMPlexSans-SemiBold.woff2": ("IBMPlexSans-SemiBold.woff2", "font/woff2"),
}
HOST = re.compile(r"(?:127\.0\.0\.1|localhost|[a-z0-9-]+\.app\.github\.dev)(?::[0-9]{1,5})?", re.ASCII)
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin"
MODEL_SHA = "921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f"


def speech_settings():
    backend, model = os.getenv("ARCHERGATE_WHISPER"), os.getenv("ARCHERGATE_WHISPER_MODEL")
    return backend, model if model and Path(model).is_file() else None


def install_model():
    executable, location = os.getenv("ARCHERGATE_WHISPER"), os.getenv("ARCHERGATE_WHISPER_MODEL")
    if not executable or not location or not Path(executable).is_file():
        raise ExperimentError("The standalone speech executable is unavailable.")
    try:
        run(executable, "--help", timeout=15)
    except ExperimentError as error:
        raise ExperimentError("The optional speech executable could not start on this workstation.") from error
    destination = Path(location)
    if destination.is_file():
        return {"installed": True}
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    digest, size = hashlib.sha256(), 0
    try:
        with urlopen(MODEL_URL, timeout=60) as response, NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
            staged = Path(stream.name)
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > 85 * 1024 * 1024:
                    raise ExperimentError("The English model exceeded the expected size.")
                digest.update(block)
                stream.write(block)
        if digest.hexdigest() != MODEL_SHA:
            raise ExperimentError("The English model failed integrity verification.")
        staged.replace(destination)
    finally:
        if staged:
            staged.unlink(missing_ok=True)
    return {"installed": True}


class StudioHandler(PreviewHandler):
    def __init__(self, *args, library, token=None, media_lock=None, **kwargs):
        self.library = library
        self.token = token
        self.media_lock = media_lock
        super().__init__(*args, workspace=library.root, **kwargs)

    def json_response(self, status, body, send_body=True):
        self.respond(status, "application/json; charset=utf-8", json.dumps(body).encode(), send_body)

    def authorized(self):
        if not HOST.fullmatch(self.headers.get("Host", "")):
            return False
        if self.token is None:
            return True
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except CookieError:
            return False
        value = cookies.get("archergate_session")
        header = value.value.encode("utf-8") if value else b""
        return hmac.compare_digest(header, self.token)

    def log_request(self, code="-", size="-"):
        if not self.path.startswith("/desktop-session/"):
            super().log_request(code, size)

    def serve_route(self, send_body):
        if self.token and self.path.startswith("/desktop-session/"):
            if not HOST.fullmatch(self.headers.get("Host", "")) or not hmac.compare_digest(
                self.path.removeprefix("/desktop-session/").encode("utf-8"), self.token
            ):
                self.send_error(403)
                return
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", f"archergate_session={self.token.decode()}; Path=/; HttpOnly; SameSite=Strict")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not self.authorized():
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
            elif route == "/api/speech":
                self.json_response(200, {"installed": bool(speech_settings()[1])}, send_body)
            elif re.fullmatch(r"/api/preview/[a-f0-9]{64}", route):
                name = route.rsplit("/", 1)[1]
                self.serve_media(self.library.path("previews", name + ".mp4"), "video/mp4", send_body)
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
        if not self.authorized() or self.headers.get("X-ArcherGate-Workspace") != "1":
            self.send_error(403)
            return
        locked = False
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_000_000 or self.headers.get_content_type() != "application/json":
                raise ExperimentError("Expected a JSON request smaller than 1 MB.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ExperimentError("Expected a JSON object.")
            if self.media_lock and self.path in {
                "/api/import", "/api/preview", "/api/transcribe", "/api/speech/install", "/api/creative-revision",
            }:
                locked = self.media_lock.acquire(blocking=False)
                if not locked:
                    raise ExperimentError("Wait for the current media operation to finish.")
            match self.path:
                case "/api/import":
                    data = self.library.import_media(body.get("path", ""), body.get("provenance"), body.get("rights"),
                                                     *speech_settings(), profile=body.get("profile", "standard"))
                case "/api/transcribe":
                    data = self.library.transcribe(body.get("asset"), *speech_settings())
                case "/api/speech/install" if self.token:
                    data = install_model()
                case "/api/creative-revision" if self.token:
                    data = reasoning.generate(self.library, body)
                case "/api/preview":
                    data = {"key": preview.render(self.library, body.get("name"))}
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
        finally:
            if locked:
                self.media_lock.release()


def serve(library, port, *, open_browser=False, desktop=False):
    if not 0 <= port <= 65535:
        raise ExperimentError("Choose a port between 0 and 65535.")
    token = os.getenv("ARCHERGATE_SESSION_TOKEN")
    if desktop and (not token or not re.fullmatch(r"[a-f0-9]{64}", token)):
        raise ExperimentError("Desktop server requires a valid session key.")
    handler = partial(StudioHandler, library=library, token=token.encode() if desktop else None,
                      media_lock=Lock() if desktop else None)
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        if desktop:
            def stop_on_parent_exit():
                sys.stdin.buffer.read(1)
                server.shutdown()

            Thread(target=stop_on_parent_exit, daemon=True).start()
        address = f"http://127.0.0.1:{server.server_port}"
        print(f"ArcherGate studio at {address}", flush=True)
        if open_browser:
            webbrowser.open(address)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
