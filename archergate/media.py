"""Shared media and editorial operations for Experiment 001."""

import hashlib
import json
import re
import subprocess
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "experiments" / "001"
TOOLS = ROOT / ".tools"
FPS, WIDTH, HEIGHT, SAMPLE_RATE = 24, 640, 360, 48_000
SAMPLES_PER_FRAME = SAMPLE_RATE // FPS
EDIT_IDS = ("edit-a", "edit-b")
IDENTIFIER = re.compile(r"[a-z][a-z0-9-]{0,39}", re.ASCII)
MP4_INPUT = ("-protocol_whitelist", "file", "-f", "mov", "-enable_drefs", "0")
ENCODE = (
    "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
    "-threads:v", "1", "-c:a", "aac", "-b:a", "128k", "-ar", str(SAMPLE_RATE),
    "-ac", "1", "-movflags", "+faststart", "-map_metadata", "-1", "-map_chapters", "-1",
)


class ExperimentError(ValueError):
    """An actionable experiment input or media error."""


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ExperimentError("Use an identifier of 1–40 lowercase letters, digits or hyphens, starting with a letter.")
    return value


def local_child(parent, name):
    path = parent / name
    if path.is_symlink():
        raise ExperimentError(f"Symbolic links are unsupported in experiment storage: {path}")
    return path


def data_root(workspace):
    path = Path(workspace).absolute()
    if path.resolve() != path:
        raise ExperimentError("The workspace must use a canonical path without symbolic links.")
    return path


def storage_path(workspace, directory, filename):
    return local_child(local_child(data_root(workspace), directory), filename)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run_tool(name, *arguments, timeout=90, cwd=None):
    binary = TOOLS / name
    if not binary.is_file():
        raise ExperimentError(f"{name} is unavailable. Run ./scripts/setup first.")
    try:
        result = subprocess.run(
            [binary, *arguments], capture_output=True, check=True, timeout=timeout, cwd=cwd
        )
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode(errors="replace").strip()
        raise ExperimentError(f"{name} failed: {detail[-1200:]}") from error
    except (subprocess.TimeoutExpired, OSError) as error:
        raise ExperimentError(f"{name} could not finish: {error}") from error
    return result.stdout


def probe(path):
    return json.loads(run_tool(
        "ffprobe", "-v", "error", *MP4_INPUT, "-show_streams", "-show_format", "-of", "json", path
    ))


def read_json(path):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ExperimentError(f"{path.name}: duplicate JSON field {key!r}.")
            result[key] = value
        return result

    try:
        if path.exists() and not path.is_file():
            raise ExperimentError(f"Expected a JSON file: {path}")
        with path.open(encoding="utf-8") as stream:
            contents = stream.read(1_000_001)
        if len(contents) > 1_000_000:
            raise ExperimentError(f"{path.name}: JSON exceeds the 1 MB limit.")
        return json.loads(contents, object_pairs_hook=unique_object)
    except FileNotFoundError as error:
        raise ExperimentError(f"Missing {path}.") from error
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ExperimentError(f"Invalid JSON in {path.name}: {error}") from error


def load_manifest(path):
    return validate_manifest(read_json(path))


def validate_manifest(data):
    if not isinstance(data, dict) or set(data) != {"version", "id", "fps", "cuts"}:
        raise ExperimentError("Manifest must contain exactly version, id, fps and cuts.")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ExperimentError("Manifest version must be integer 1.")
    identifier(data["id"])
    if type(data["fps"]) is not int or data["fps"] != FPS:
        raise ExperimentError(f"Manifest fps must be integer {FPS}.")
    cuts = data["cuts"]
    if not isinstance(cuts, list) or not 1 <= len(cuts) <= 24:
        raise ExperimentError("Manifest must contain 1–24 cuts.")
    for number, cut in enumerate(cuts, 1):
        if not isinstance(cut, dict) or set(cut) != {"asset", "start", "end"}:
            raise ExperimentError(f"Cut {number} must contain exactly asset, start and end.")
        identifier(cut["asset"])
        start, end = cut["start"], cut["end"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= 24 * 60:
            raise ExperimentError(f"Cut {number} requires integer frames with 0 <= start < end <= 1440.")
    if sum(cut["end"] - cut["start"] for cut in cuts) > 24 * 60:
        raise ExperimentError("The edit cannot exceed 60 seconds.")
    return data


def named_manifest(edit):
    if edit not in EDIT_IDS:
        raise ExperimentError("Choose edit-a or edit-b.")
    manifest = load_manifest(MANIFESTS / f"{edit}.json")
    if manifest["id"] != edit:
        raise ExperimentError(f"{edit}.json must have the ID {edit}.")
    return manifest


@contextmanager
def workspace_lock(workspace):
    import fcntl

    root = data_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = local_child(root, ".lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def read_registry(workspace, *, allow_empty=False):
    path = local_child(data_root(workspace), "registry.json")
    if allow_empty and not path.exists():
        return {}
    data = read_json(path)
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("assets"), dict):
        raise ExperimentError("Invalid registry format. Expected version 1 and an assets object.")
    return data["assets"]


def register(workspace, asset, provenance, rights):
    identifier(asset)
    for field, value in (("provenance", provenance), ("rights", rights)):
        if not isinstance(value, str) or not value.strip() or len(value) > 1000:
            raise ExperimentError(f"Provide a {field} note of 1–1000 characters.")
    with workspace_lock(workspace):
        root = data_root(workspace)
        path = storage_path(root, "sources", f"{asset}.mp4")
        if not path.is_file():
            raise ExperimentError(f"Missing source {asset}: {path}")
        assets = read_registry(root, allow_empty=True)
        digest = sha256(path)
        if asset in assets:
            existing = assets[asset]
            if (existing["sha256"], existing["provenance"], existing["rights"]) != (digest, provenance, rights):
                raise ExperimentError(f"Registration for {asset} is immutable. Keep the original or use a new asset ID.")
            return existing
        info = probe(path)
        streams = info["streams"]
        video = [stream for stream in streams if stream["codec_type"] == "video"]
        audio = [stream for stream in streams if stream["codec_type"] == "audio"]
        if len(video) != 1 or len(audio) != 1:
            raise ExperimentError(f"Source {asset} must have one video stream and one audio stream.")
        video, audio = video[0], audio[0]
        if (video["width"], video["height"], video["avg_frame_rate"], video["r_frame_rate"]) != (
            WIDTH, HEIGHT, "24/1", "24/1"
        ):
            raise ExperimentError(f"Source {asset} must be {WIDTH}×{HEIGHT} with constant {FPS} fps.")
        try:
            frames = int(video["nb_frames"])
            seconds = float(audio["duration"])
            starts_at_zero = float(video["start_time"]) == float(audio["start_time"]) == 0
        except (KeyError, ValueError) as error:
            raise ExperimentError(f"Source {asset} must report its frame count and stream timing.") from error
        if frames < 1 or not starts_at_zero or not seconds >= frames / FPS - 0.000001:
            raise ExperimentError(f"Source {asset} needs zero-based streams and audio covering the video.")
        if sha256(path) != digest:
            raise ExperimentError(f"Source {asset} changed during registration.")
        record = {
            "path": f"sources/{asset}.mp4", "sha256": digest, "bytes": path.stat().st_size,
            "provenance": provenance, "rights": rights,
            "video": {"codec": video["codec_name"], "width": WIDTH, "height": HEIGHT, "fps": FPS, "frames": frames},
            "audio": {"codec": audio["codec_name"], "sample_rate": int(audio["sample_rate"]),
                      "channels": audio["channels"], "duration_seconds": seconds},
        }
        assets[asset] = record
        with TemporaryDirectory(dir=root) as temp:
            staging = Path(temp) / "registry.json"
            staging.write_text(json.dumps({"version": 1, "assets": assets}, indent=2, sort_keys=True) + "\n")
            staging.replace(local_child(root, "registry.json"))
        return record


def source_paths(workspace, manifest, assets):
    paths = {}
    for cut in manifest["cuts"]:
        asset = cut["asset"]
        if asset not in assets:
            raise ExperimentError(f"Unknown asset {asset}. Register it before using it in a cut.")
        if cut["end"] > assets[asset]["video"]["frames"]:
            raise ExperimentError(f"Cut for {asset} ends beyond its {assets[asset]['video']['frames']} source frames.")
        if asset not in paths:
            path = storage_path(workspace, "sources", f"{asset}.mp4")
            if not path.is_file():
                raise ExperimentError(f"Missing source {asset}: {path}")
            if sha256(path) != assets[asset]["sha256"]:
                raise ExperimentError(f"Source {asset} no longer matches its registered SHA-256.")
            paths[asset] = path
    return paths


def timeline(manifest):
    position = 0
    rows = []
    for cut in manifest["cuts"]:
        end = position + cut["end"] - cut["start"]
        rows.append({**cut, "timeline_start": position, "timeline_end": end})
        position = end
    return {"id": manifest["id"], "fps": FPS, "frames": position, "duration_seconds": position / FPS, "cuts": rows}


def compare(left, right):
    left_counts = Counter(cut["asset"] for cut in left["cuts"])
    right_counts = Counter(cut["asset"] for cut in right["cuts"])
    return {
        "same_assets": left_counts.keys() == right_counts.keys(),
        "same_asset_counts": left_counts == right_counts,
        "order_changed": list(cut["asset"] for cut in left["cuts"]) != list(cut["asset"] for cut in right["cuts"]),
        "left": timeline(left),
        "right": timeline(right),
    }


def validate_export(path, expected_frames):
    info = probe(path)
    streams = info["streams"]
    if len(streams) != 2:
        raise ExperimentError("Export must contain exactly one video and one audio stream.")
    video, audio = streams
    if (video.get("codec_name"), video.get("width"), video.get("height"), video.get("avg_frame_rate"),
        video.get("nb_frames"), audio.get("codec_name"), audio.get("sample_rate"), audio.get("channels")) != (
        "h264", WIDTH, HEIGHT, "24/1", str(expected_frames), "aac", str(SAMPLE_RATE), 1
    ):
        raise ExperimentError(f"Export does not match the H.264/AAC profile or the expected {expected_frames} frames.")
    seconds = expected_frames / FPS
    if any(abs(float(stream.get("duration", "inf")) - seconds) > 1 / FPS for stream in streams):
        raise ExperimentError(f"Export streams differ from the expected {seconds:.3f} second duration.")
    return info


def export_manifest(workspace, edit):
    path = storage_path(workspace, "renders", f"{edit}.mp4")
    if not path.is_file():
        raise ExperimentError(f"Missing {edit}.mp4. Run the demo command to generate the videos.")
    try:
        receipt = json.loads(probe(path)["format"]["tags"]["comment"])
        manifest = validate_manifest(receipt["manifest"])
    except (KeyError, TypeError, json.JSONDecodeError, RecursionError) as error:
        raise ExperimentError(f"{edit}.mp4 has no usable embedded manifest. Render it again.") from error
    if manifest["id"] != edit:
        raise ExperimentError(f"{edit}.mp4 has a different manifest ID. Render it again.")
    return manifest


def render(workspace, manifest):
    validate_manifest(manifest)
    with workspace_lock(workspace):
        assets = read_registry(workspace)
        paths = source_paths(workspace, manifest, assets)
        output = storage_path(workspace, "renders", f"{manifest['id']}.mp4")
        output.parent.mkdir(exist_ok=True)
        inputs, filters, segments = [], [], []
        for index, cut in enumerate(manifest["cuts"]):
            inputs.extend(("-threads", "1", *MP4_INPUT, "-i", paths[cut["asset"]]))
            start, end = cut["start"], cut["end"]
            filters.append(
                f"[{index}:v:0]trim=start_frame={start}:end_frame={end},"
                f"setpts=N/({FPS}*TB),format=yuv420p,setsar=1[v{index}]"
            )
            filters.append(
                f"[{index}:a:0]aformat=sample_rates={SAMPLE_RATE}:channel_layouts=mono,"
                f"atrim=start_sample={start * SAMPLES_PER_FRAME}:end_sample={end * SAMPLES_PER_FRAME},"
                f"asetpts=N/SR/TB[a{index}]"
            )
            segments.append(f"[v{index}][a{index}]")
        filters.append("".join(segments) + f"concat=n={len(segments)}:v=1:a=1[video][audio]")
        receipt = {
            "manifest": manifest,
            "sources": {asset: assets[asset]["sha256"] for asset in paths},
            "ffmpeg": run_tool("ffmpeg", "-version").decode().splitlines()[0],
        }
        with TemporaryDirectory(dir=output.parent) as temp:
            staging = Path(temp) / "export.mp4"
            run_tool(
                "ffmpeg", "-nostdin", "-hide_banner", "-v", "error", *inputs,
                "-filter_complex_threads", "1", "-filter_complex", ";".join(filters),
                "-map", "[video]", "-map", "[audio]", "-r", str(FPS), *ENCODE,
                "-metadata", f"comment={json.dumps(receipt, sort_keys=True)}", staging,
            )
            validate_export(staging, timeline(manifest)["frames"])
            source_paths(workspace, manifest, assets)
            digest = sha256(staging)
            staging.replace(output)
    return {"path": f"renders/{output.name}", "sha256": digest, **receipt}
