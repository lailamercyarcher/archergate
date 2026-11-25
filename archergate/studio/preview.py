"""Render a temporary listening and viewing copy of a saved multitrack edit."""

import hashlib
import json
from contextlib import ExitStack, contextmanager
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from archergate.media import ExperimentError, sha256

from . import timeline
from .operations import transform_filter
from .library import ffmpeg, input_args, probe


def decimal(value):
    return f"{float(value):.9f}"


def video_fades(effects, length, base):
    filters = []
    for edge in ("in", "out"):
        if f"fade_{edge}" in effects:
            duration = effects[f"fade_{edge}"] * base
            start = length - duration if edge == "out" else 0
            filters.append(f",fade=t={edge}:st={decimal(start)}:d={decimal(duration)}:alpha=1")
    return "".join(filters)


def audio_effects(effects, base, samples):
    filters = []
    if "gain_db" in effects:
        filters.append(f",volume={effects['gain_db']}dB")
    if "volume_points" in effects:
        # Interpolate dB at each decoded sample, then convert to a linear multiplier.
        points = [(round(point["tick"] * base * 48000), point["gain_db"]) for point in effects["volume_points"]]
        expression = str(points[-1][1])
        for (start, first), (stop, last) in reversed(list(zip(points, points[1:]))):
            if start < stop:
                expression = f"if(lt(n,{stop}),{first}+({last - first})*(n-{start})/{stop - start},{expression})"
        multiplier = f"pow(10,({expression})/20)"
        filters.append(f",aeval=exprs='val(0)*{multiplier}|val(1)*{multiplier}':c=stereo")
    for edge in ("in", "out"):
        if f"fade_{edge}" in effects:
            duration = min(samples, max(1, round(effects[f"fade_{edge}"] * base * 48000)))
            start = samples - duration if edge == "out" else 0
            if duration:
                filters.append(f",afade=t={edge}:ss={start}:ns={duration}")
    return "".join(filters)


@contextmanager
def open_cached(library, key):
    """Open the same verified file handle that will be used for playback."""
    output = library.path("previews", key + ".mp4")
    receipt = library.path("previews", key + ".sha256")
    with ExitStack() as files:
        try:
            with receipt.open("rb") as stream:
                expected = stream.read(65)
            stream = files.enter_context(output.open("rb"))
        except FileNotFoundError:
            stream = None
        if stream is not None:
            if hashlib.file_digest(stream, "sha256").hexdigest().encode() != expected:
                stream = None
            else:
                stream.seek(0)
        yield stream


def render(library, name):
    manifest = timeline.load(library, name)
    base, fps = timeline.schema(manifest)
    report = timeline.inspect(library, manifest)
    if not report["valid"]:
        raise ExperimentError("Fix the timeline errors before making a preview.")
    duration = report["duration_ticks"] * base
    if duration > 600 or sum(len(track["clips"]) for track in manifest["tracks"]) > 48:
        raise ExperimentError("Alpha preview supports up to ten minutes and 48 clips.")
    assets = {clip["asset"]: library.asset(clip["asset"]) for track in manifest["tracks"] for clip in track["clips"]}
    for asset in assets.values():
        library.verify_original(asset)
    key = hashlib.sha256(json.dumps([2, manifest, {k: v["sha256"] for k, v in assets.items()}],
                                  sort_keys=True).encode()).hexdigest()
    library.path("previews").mkdir(exist_ok=True)
    output = library.path("previews", key + ".mp4")
    with open_cached(library, key) as cached:
        if cached is not None:
            return key
    width, height = 768, 432
    length = decimal(duration)
    args = ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}:d={length}",
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={length}"]
    filters = ["[0:v]setpts=PTS-STARTPTS[base]", "[1:a]asetpts=PTS-STARTPTS[silence]"]
    picture = "base"
    audio = ["[silence]"]
    index = 2
    for track in manifest["tracks"]:
        kind = track["kind"]
        for clip in sorted(track["clips"], key=lambda row: row["at"]):
            asset = assets[clip["asset"]]
            stream = asset["source"]["streams"][kind]
            args.extend(("-threads", "1", *input_args(library.original(asset))))
            start = clip["source_start"] * base - Fraction(stream["start"])
            length_clip = clip["duration"] * base
            stop = start + length_clip
            at = clip["at"] * base
            label = f"clip{index}"
            effects = clip.get("effects", {})
            selector = f"[{index}:{stream['index']}]"
            if kind == "video":
                motion = ""
                if "transform" in effects:
                    motion = f",fps={fps}," + transform_filter(effects["transform"], base, fps, width, height)
                fades = video_fades(effects, length_clip, base)
                if fades:
                    if not motion:
                        motion = f",fps={fps}"
                    motion += ",format=yuva420p" + fades
                filters.append(
                    f"{selector}setpts=PTS-STARTPTS,trim=start={decimal(start)}:end={decimal(stop)},"
                    f"setpts=PTS-STARTPTS,scale={width}:{height}:"
                    f"force_original_aspect_ratio=decrease:force_divisible_by=2,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1{motion},"
                    f"setpts=PTS-STARTPTS+{decimal(at)}/TB[{label}]"
                )
                next_picture = f"picture{index}"
                filters.append(
                    f"[{picture}][{label}]overlay=eof_action=pass:repeatlast=0:"
                    f"enable='gte(t,{decimal(at)})*lt(t,{decimal(at + length_clip)})'[{next_picture}]"
                )
                picture = next_picture
            else:
                samples = round(at * 48000)
                clip_samples = round(stop * 48000) - round(start * 48000)
                volume = audio_effects(effects, base, clip_samples)
                filters.append(
                    f"{selector}aresample=48000,aformat=channel_layouts=stereo,"
                    f"atrim=start_sample={round(start * 48000)}:end_sample={round(stop * 48000)},"
                    f"asetpts=N/SR/TB{volume},adelay={samples}S:all=1[{label}]"
                )
                audio.append(f"[{label}]")
            index += 1
    filters.append("".join(audio) + f"amix=inputs={len(audio)}:duration=first:normalize=0,alimiter=limit=0.95:latency=1[sound]")
    with TemporaryDirectory(dir=output.parent) as temp:
        staged = Path(temp) / "preview.mp4"
        graph = Path(temp) / "filters.txt"
        graph.write_text(";".join(filters), encoding="utf-8")
        ffmpeg(*args, "-filter_complex_threads", "1", "-filter_complex_script", graph,
               "-map", f"[{picture}]", "-map", "[sound]", "-t", length, "-r", str(fps), "-fps_mode", "cfr",
               "-c:v", "libx264",
               "-preset", "veryfast", "-threads:v", "2", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-movflags", "+faststart", staged)
        info = probe(staged)
        codecs = {kind: stream["codec"] for kind, stream in info["streams"].items()}
        if codecs != {"audio": "aac", "video": "h264"} or abs(Fraction(info["duration"]) - duration) > max(1 / fps, Fraction(1, 20)):
            raise ExperimentError("Rendered preview failed media validation.")
        ffmpeg("-xerror", *input_args(staged), "-map", "0", "-f", "null", "-")
        for asset in assets.values():
            library.verify_original(asset)
        staged_receipt = staged.with_suffix(".sha256")
        staged_receipt.write_bytes(sha256(staged).encode())
        with library.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            with open_cached(library, key) as cached:
                if cached is not None:
                    return key
            staged.replace(output)
            staged_receipt.replace(library.path("previews", key + ".sha256"))
    return key
