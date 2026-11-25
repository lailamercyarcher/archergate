"""Read-only local browser preview with MP4 byte-range support."""

import json
import re
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from os import fstat
from pathlib import Path
from urllib.parse import urlsplit

from . import media

WEB = Path(__file__).with_name("web_assets")
PAGES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/compare.js": ("compare.js", "text/javascript; charset=utf-8"),
}
VIDEOS = {f"/video/{edit}.mp4": f"{edit}.mp4" for edit in media.EDIT_IDS}
BYTE_RANGE = re.compile(r"bytes=([0-9]{0,19})-([0-9]{0,19})")


def byte_range(value, length):
    match = BYTE_RANGE.fullmatch(value)
    if not match:
        raise ValueError("Expected one byte range")
    first, last = match.groups()
    if first:
        start, end = int(first), min(int(last), length - 1) if last else length - 1
    elif last and int(last):
        start, end = max(0, length - int(last)), length - 1
    else:
        raise ValueError("Empty range")
    if start > end:
        raise ValueError("Unsatisfiable range")
    return start, end


class PreviewHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, workspace, **kwargs):
        self.workspace = workspace
        super().__init__(*args, **kwargs)

    def do_GET(self):
        self.serve_route(send_body=True)

    def do_HEAD(self):
        self.serve_route(send_body=False)

    def serve_route(self, send_body):
        try:
            path = urlsplit(self.path).path
        except ValueError:
            self.send_error(400)
            return
        if path in PAGES:
            filename, mime = PAGES[path]
            self.respond(200, mime, (WEB / filename).read_bytes(), send_body)
        elif path == "/comparison.json":
            try:
                manifests = [media.export_manifest(self.workspace, edit) for edit in media.EDIT_IDS]
                data = media.compare(*manifests)
                status = 200
            except (media.ExperimentError, OSError) as error:
                data = {"error": str(error)}
                status = 409
            self.respond(status, "application/json", json.dumps(data).encode(), send_body)
        elif path in VIDEOS:
            self.serve_video(VIDEOS[path], send_body)
        else:
            self.send_error(404)

    def respond(self, status, mime, content, send_body):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        if send_body:
            self.wfile.write(content)

    def serve_video(self, filename, send_body):
        try:
            path = media.storage_path(self.workspace, "renders", filename)
            if not path.is_file():
                raise FileNotFoundError(path)
            stream = path.open("rb")
        except (OSError, media.ExperimentError):
            self.send_error(404, "Render unavailable. Run the demo command.")
            return
        with stream:
            length = fstat(stream.fileno()).st_size
            requested = self.headers.get("Range")
            start, end = 0, length - 1
            if requested is not None:
                try:
                    start, end = byte_range(requested, length)
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{length}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            remaining = end - start + 1
            self.send_response(206 if requested is not None else 200)
            if requested is not None:
                self.send_header("Content-Range", f"bytes {start}-{end}/{length}")
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(remaining))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if send_body:
                stream.seek(start)
                try:
                    while remaining:
                        chunk = stream.read(min(remaining, 64 * 1024))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    # Browsers may cancel a read when the viewer seeks.
                    return


def serve(workspace, port):
    if not 0 <= port <= 65535:
        raise media.ExperimentError("Port must be between 0 and 65535.")
    handler = partial(PreviewHandler, workspace=media.data_root(workspace))
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"ArcherGate preview at http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
