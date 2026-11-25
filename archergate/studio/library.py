"""Local asset ingestion, derived media, and analysis records."""

import json
import os
import re
import shutil
import sqlite3
import subprocess
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from archergate.media import ROOT, ExperimentError, sha256

FORMATS = {".mp4": "mov", ".mov": "mov", ".wav": "wav", ".mp3": "mp3"}
ASSET_ID = re.compile(r"[a-f0-9]{32}")
DEFAULT_WORKSPACE = ROOT / "workspace" / "studio"


def binary(name):
    configured = os.getenv(f"ARCHERGATE_{name.upper()}")
    if configured:
        path = Path(configured)
        if not path.is_absolute() or not path.is_file():
            raise ExperimentError(f"ARCHERGATE_{name.upper()} must name an absolute executable path.")
        return path
    local = ROOT / ".tools" / (name + (".exe" if os.name == "nt" else ""))
    if local.is_file():
        return local
    found = shutil.which(name)
    if found:
        return found
    raise ExperimentError(f"Install or configure {name} locally. See the Experiment 002 guide.")


def run(executable, *args, timeout=1800):
    try:
        return subprocess.run([executable, *args], check=True, timeout=timeout, capture_output=True).stdout
    except subprocess.CalledProcessError as error:
        message = error.stderr.decode(errors="replace").strip()
        raise ExperimentError(f"{Path(executable).name}: {message[-1500:] or 'processing failed'}") from error
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ExperimentError(f"{Path(executable).name} could not finish: {error}") from error


def input_args(path):
    return ("-protocol_whitelist", "file", "-f", FORMATS.get(path.suffix.lower(), "mov"), "-i", str(path))


def ffmpeg(*args):
    return run(binary("ffmpeg"), "-nostdin", "-hide_banner", "-v", "error", *args)


def probe(path):
    data = json.loads(run(binary("ffprobe"), "-v", "error", *input_args(path),
                          "-show_streams", "-show_format", "-of", "json"))
    try:
        container = data["format"]
        origin = Fraction(container.get("start_time", "0"))
        duration = Fraction(container["duration"])
        streams = {}
        for stream in data["streams"]:
            kind = stream["codec_type"]
            if kind not in ("video", "audio") or kind in streams or stream.get("disposition", {}).get("attached_pic"):
                continue
            base = Fraction(stream["time_base"])
            start = int(stream["start_pts"]) * base if "start_pts" in stream else origin
            length = int(stream["duration_ts"]) * base if "duration_ts" in stream else Fraction(
                stream.get("duration", str(duration - max(0, start - origin)))
            )
            streams[kind] = {
                "index": stream["index"], "codec": stream["codec_name"], "time_base": str(base),
                "start": str(start - origin), "duration": str(length),
            }
            keys = ("width", "height", "avg_frame_rate", "r_frame_rate") if kind == "video" else ("sample_rate", "channels")
            streams[kind].update({key: stream[key] for key in keys})
        if not streams or duration <= 0 or any(Fraction(row["duration"]) <= 0 for row in streams.values()):
            raise ValueError("no timed video or audio stream")
    except (KeyError, ValueError, ZeroDivisionError) as error:
        raise ExperimentError(f"Cannot establish media timing for {path.name}: {error}") from error
    return {"origin": str(origin), "duration": str(duration), "streams": streams}


def create_proxy(source, metadata, folder):
    streams = metadata["streams"]
    proxy = folder / ("proxy.mp4" if "video" in streams else "proxy.m4a")
    args = ["-copyts", "-start_at_zero", *input_args(source), "-map_metadata", "-1", "-map_chapters", "-1"]
    if "video" in streams:
        args += ["-map", f"0:{streams['video']['index']}", "-vf",
                 "scale=w='min(960,iw)':h='min(540,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                 "-fps_mode", "passthrough", "-enc_time_base", "1:90000",
                 "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p"]
    if "audio" in streams:
        args += ["-map", f"0:{streams['audio']['index']}", "-c:a", "aac", "-ar", "48000"]
    ffmpeg(*args, "-movflags", "+faststart", proxy)
    result = probe(proxy)
    for kind, original in streams.items():
        derived = result["streams"][kind]
        # The MP4 track starts can shift by a codec frame or muxer tick.
        tolerance = Fraction(1, 20)
        if abs(Fraction(original["start"]) - Fraction(derived["start"])) > tolerance:
            raise ExperimentError(f"Proxy did not preserve the {kind} stream offset within 50 ms.")
        if abs(Fraction(original["duration"]) - Fraction(derived["duration"])) > tolerance:
            raise ExperimentError(f"Proxy did not preserve the {kind} stream duration within 50 ms.")
    return {
        "file": proxy.name, "probe": result,
        "source_time_at_proxy_zero": metadata["origin"],
        "stream_start_deltas": {kind: str(Fraction(result["streams"][kind]["start"]) -
                                         Fraction(row["start"])) for kind, row in streams.items()},
    }


def extract_waveform(source, audio):
    rate = int(audio["sample_rate"])
    block = max(1, rate // 10)
    filters = (f"asetnsamples=n={block}:p=0,"
               "astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=Peak_level,"
               "ametadata=mode=print:key=lavfi.astats.Overall.Peak_level:file=-")
    output = ffmpeg(*input_args(source), "-map", f"0:{audio['index']}", "-af", filters, "-f", "null", "-").decode()
    levels = re.findall(r"(?m)^lavfi\.astats\.Overall\.Peak_level=(.+)$", output)
    if not levels:
        raise ExperimentError("FFmpeg did not report waveform peaks.")
    return {"state": "complete", "start": audio["start"], "seconds_per_peak": str(Fraction(block, rate)),
            "peaks": [round(10 ** (float(level) / 20), 3) for level in levels]}


def extract_shots(source, video, folder):
    ffmpeg(*input_args(source), "-map", f"0:{video['index']}", "-frames:v", "1",
           "-vf", "scale=320:-2", "-q:v", "3", folder / "thumbnail.jpg")
    output = ffmpeg("-copyts", "-start_at_zero", *input_args(source), "-map", f"0:{video['index']}",
                    "-vf", r"select=gt(scene\,0.32),metadata=mode=print:key=lavfi.scene_score:file=-",
                    "-an", "-f", "null", "-").decode()
    matches = re.findall(r"pts_time:([^\s]+)\s+lavfi\.scene_score=([0-9.]+)", output)
    return {"state": "complete", "threshold": 0.32, "candidates": [
        {"time": str(Fraction(time)), "score": float(score)} for time, score in matches if Fraction(time) >= 0
    ]}


def transcript(source, audio, folder, backend, model):
    if audio is None:
        return {"state": "unavailable", "reason": "No audio stream"}
    if not backend or not model:
        return {"state": "unavailable", "reason": "Configure a local whisper.cpp executable and English model"}
    try:
        wav, result = folder / "speech.wav", folder / "speech"
        ffmpeg(*input_args(source), "-map", f"0:{audio['index']}", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav)
        run(backend, "--no-gpu", "-m", model, "-f", wav, "-l", "en", "-oj", "-of", result)
        rows = json.loads(result.with_suffix(".json").read_text(encoding="utf-8"))["transcription"]
        start = Fraction(audio["start"])
        segments = []
        for row in rows:
            begin = start + Fraction(row["offsets"]["from"], 1000)
            end = start + Fraction(row["offsets"]["to"], 1000)
            text = row["text"].strip()
            if end > begin and text:
                segments.append({"start": str(begin), "end": str(end), "text": text})
        return {"state": "complete", "backend": "whisper.cpp", "language": "en", "segments": segments}
    except (ExperimentError, OSError, KeyError, ValueError, TypeError) as error:
        return {"state": "failed", "reason": str(error)}


def user_text(value, label, limit=4000, *, required=True):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value or (required and not value.strip()):
        raise ExperimentError(f"{label} must be text of {'1' if required else '0'}–{limit} characters.")
    return value.strip()


class Library:
    def __init__(self, root=DEFAULT_WORKSPACE):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path("media").mkdir(exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, provenance TEXT NOT NULL,
                    rights TEXT NOT NULL, details TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory (id TEXT PRIMARY KEY, details TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS timelines (id TEXT PRIMARY KEY, details TEXT NOT NULL);
            """)

    def path(self, *parts):
        path = self.root.joinpath(*parts)
        if not path.resolve().is_relative_to(self.root) or path.absolute() != path.resolve():
            raise ExperimentError("Storage paths must stay inside the workspace without symbolic links.")
        return path

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path("studio.sqlite3"), timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def search(self, query=""):
        query = user_text(query, "Query", 250, required=False)
        with self.connect() as db:
            rows = db.execute("""
                SELECT details FROM assets
                WHERE instr(lower(name || ' ' || provenance || ' ' || rights), lower(?)) > 0
                ORDER BY name, id LIMIT 250
            """, (query,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def asset(self, asset_id):
        if not isinstance(asset_id, str) or not ASSET_ID.fullmatch(asset_id):
            raise ExperimentError("Invalid asset ID.")
        with self.connect() as db:
            row = db.execute("SELECT details FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            raise ExperimentError(f"Unknown asset {asset_id}.")
        return json.loads(row[0])

    def original(self, asset):
        return self.path("media", asset["id"], "original", asset["name"])

    def import_media(self, path, provenance, rights, backend=None, model=None):
        provenance = user_text(provenance, "Provenance")
        rights = user_text(rights, "Rights")
        path = Path(path).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() not in FORMATS:
            raise ExperimentError("Choose a local MP4, MOV, WAV or MP3 file.")
        asset_id = uuid4().hex
        digest = sha256(path)
        with TemporaryDirectory(dir=self.root) as temporary:
            folder = Path(temporary) / "asset"
            (folder / "original").mkdir(parents=True)
            original = folder / "original" / path.name
            shutil.copyfile(path, original)
            if sha256(original) != digest or sha256(path) != digest:
                raise ExperimentError("The input changed while it was being copied.")
            metadata = probe(original)
            proxy = create_proxy(original, metadata, folder)
            streams = metadata["streams"]
            with TemporaryDirectory(dir=self.root) as analysis:
                analysis = Path(analysis)
                words = transcript(original, streams.get("audio"), analysis, backend, model)
            waveform = (extract_waveform(original, streams["audio"])
                        if "audio" in streams else {"state": "unavailable", "reason": "No audio stream"})
            shots = (extract_shots(original, streams["video"], folder)
                     if "video" in streams else {"state": "unavailable", "reason": "No video stream"})
            data = {"id": asset_id, "name": path.name, "sha256": digest, "bytes": original.stat().st_size,
                    "provenance": provenance, "rights": rights, "source": metadata, "proxy": proxy,
                    "waveform": waveform, "shots": shots, "transcript": words}
            destination = self.path("media", asset_id)
            folder.rename(destination)
            try:
                with self.connect() as db:
                    db.execute("INSERT INTO assets VALUES (?, ?, ?, ?, ?)",
                               (asset_id, path.name, provenance, rights, json.dumps(data)))
            except sqlite3.Error:
                destination.rename(folder)
                raise
        return data
