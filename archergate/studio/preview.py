"""Render a temporary listening and viewing copy of a saved multitrack edit."""

import hashlib
import json
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from archergate.media import ExperimentError, sha256

from . import timeline
from .operations import transform_filter
from .library import ffmpeg, input_args, probe


def decimal(value):
    return f"{float(value):.9f}"


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
        source = library.original(asset)
        if not source.is_file() or sha256(source) != asset["sha256"]:
            raise ExperimentError(f"Missing or changed original: {asset['name']}.")
    key = hashlib.sha256(json.dumps([1, manifest, {k: v["sha256"] for k, v in assets.items()}],
                                  sort_keys=True).encode()).hexdigest()
    library.path("previews").mkdir(exist_ok=True)
    output = library.path("previews", key + ".mp4")
    if output.is_file():
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
                volume = f",volume={effects['gain_db']}dB" if "gain_db" in effects else ""
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
               "-map", f"[{picture}]", "-map", "[sound]", "-t", length, "-c:v", "libx264",
               "-preset", "veryfast", "-threads:v", "2", "-pix_fmt", "yuv420p", "-c:a", "aac",
               "-movflags", "+faststart", staged)
        info = probe(staged)
        if set(info["streams"]) != {"audio", "video"} or abs(Fraction(info["duration"]) - duration) > max(1 / fps, Fraction(1, 20)):
            raise ExperimentError("Rendered preview failed media validation.")
        try:
            staged.replace(output)
        except PermissionError:
            if not output.is_file():
                raise
    return key
