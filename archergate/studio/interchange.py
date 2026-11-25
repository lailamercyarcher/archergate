"""Write and check the supported Final Cut Pro 7 xmeml v5 subset."""

import math
import xml.etree.ElementTree as ET
from fractions import Fraction
from uuid import uuid4

from archergate.media import ExperimentError, sha256

from . import timeline


def element(parent, tag, value=None, **attrs):
    node = ET.SubElement(parent, tag, attrs)
    if value is not None:
        node.text = str(value)
    return node


def rate(parent, fps):
    nominal = fps * Fraction(1001, 1000) if fps.denominator != 1 else fps
    if nominal.denominator != 1 or nominal > 240 or fps <= 0:
        raise ExperimentError(f"XML cannot represent frame rate {fps}. Use integer or 1000/1001 NTSC rates.")
    node = element(parent, "rate")
    element(node, "ntsc", "FALSE" if fps.denominator == 1 else "TRUE")
    element(node, "timebase", nominal.numerator)


def frame(value, fps, clip, field):
    result = value * fps
    if result.denominator != 1:
        raise ExperimentError(f"{clip}: {field} is between frames at {fps} fps. Align the edit before XML export.")
    return result.numerator


def path_url(path):
    uri = path.as_uri()
    return uri.replace("file:///", "file://localhost/", 1) if uri.startswith("file:///") else uri


def format_file(node, asset, source_fps, uri):
    source = asset["source"]
    element(node, "name", asset["name"])
    element(node, "pathurl", uri)
    rate(node, source_fps)
    element(node, "duration", math.ceil(Fraction(source["duration"]) * source_fps))
    media = element(node, "media")
    streams = source["streams"]
    if "video" in streams:
        sample = element(element(media, "video"), "samplecharacteristics")
        rate(sample, source_fps)
        for key in ("width", "height"):
            element(sample, key, streams["video"][key])
    if "audio" in streams:
        audio = element(media, "audio")
        sample = element(audio, "samplecharacteristics")
        element(sample, "samplerate", streams["audio"]["sample_rate"])
        element(audio, "channelcount", streams["audio"]["channels"])


def build(library, manifest):
    base, fps = timeline.schema(manifest)
    report = timeline.inspect(library, manifest)
    if not report["valid"]:
        raise ExperimentError("XML export requires a valid timeline.")
    root = ET.Element("xmeml", version="5")
    sequence = element(root, "sequence", id=manifest["id"])
    element(sequence, "name", manifest["title"])
    element(sequence, "duration", frame(report["duration_ticks"] * base, fps, manifest["id"], "sequence duration"))
    rate(sequence, fps)
    timecode = element(sequence, "timecode")
    rate(timecode, fps)
    element(timecode, "frame", 0)
    element(timecode, "displayformat", "NDF")
    media = element(sequence, "media")
    lanes = {kind: element(media, kind) for kind in ("video", "audio")}
    assets = {}
    referenced = set()
    expected = []
    for track in manifest["tracks"]:
        kind = track["kind"]
        for clip in track["clips"]:
            asset_id = clip["asset"]
            if asset_id in assets:
                continue
            asset = library.asset(asset_id)
            path = library.original(asset)
            if not path.is_file() or sha256(path) != asset["sha256"]:
                raise ExperimentError(f"Missing or changed original: {asset['name']}. Restore the registered file.")
            picture = asset["source"]["streams"].get("video")
            if picture and picture["avg_frame_rate"] != picture["r_frame_rate"]:
                raise ExperimentError(f"{asset['name']} has an ambiguous picture rate. A constant-rate source is required for XML.")
            source_fps = Fraction(picture["avg_frame_rate"]) if picture else fps
            assets[asset_id] = asset, path_url(path), source_fps
        channels = (max((assets[clip["asset"]][0]["source"]["streams"]["audio"]["channels"]
                         for clip in track["clips"]), default=1) if kind == "audio" else 1)
        for channel in range(1, channels + 1):
            lane = element(lanes[kind], "track")
            lane_records = []
            for clip in sorted(track["clips"], key=lambda value: value["at"]):
                asset, uri, source_fps = assets[clip["asset"]]
                if kind == "audio" and channel > asset["source"]["streams"]["audio"]["channels"]:
                    continue
                clip_id = f"{clip['id']}-{channel}"
                values = [
                    frame(clip["at"] * base, fps, clip_id, "timeline start"),
                    frame((clip["at"] + clip["duration"]) * base, fps, clip_id, "timeline end"),
                    frame(clip["source_start"] * base, source_fps, clip_id, "source in"),
                    frame((clip["source_start"] + clip["duration"]) * base, source_fps, clip_id, "source out"),
                ]
                node = element(lane, "clipitem", id=clip_id)
                element(node, "name", asset["name"])
                element(node, "duration", math.ceil(Fraction(asset["source"]["duration"]) * source_fps))
                rate(node, source_fps)
                for key, value in zip(("start", "end", "in", "out"), values):
                    element(node, key, value)
                file_id = f"file-{asset['id']}"
                file = element(node, "file", id=file_id)
                if file_id not in referenced:
                    format_file(file, asset, source_fps, uri)
                    referenced.add(file_id)
                source_track = element(node, "sourcetrack")
                element(source_track, "mediatype", kind)
                element(source_track, "trackindex", channel)
                lane_records.append((clip_id, values, file_id, uri, channel))
            expected.append((kind, lane_records))

    ET.indent(root)
    content = b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding="utf-8") + b"\n"
    validate(content, expected)
    return content


def validate(content, expected):
    root = ET.fromstring(content)
    nodes = {kind: root.findall(f"./sequence/media/{kind}/track") for kind in ("video", "audio")}
    files = {node.get("id"): node.findtext("pathurl") for node in root.findall(".//file") if len(node)}
    if root.tag != "xmeml" or root.get("version") != "5" or root.find("./sequence/rate/timebase") is None:
        raise ExperimentError("Invalid xmeml sequence structure.")
    for kind in nodes:
        lanes = [rows for media, rows in expected if media == kind]
        if len(lanes) != len(nodes[kind]):
            raise ExperimentError("XML track count differs from the timeline.")
        for track, rows in zip(nodes[kind], lanes):
            actual = []
            for node in track.findall("clipitem"):
                file_id = node.find("file").get("id")
                actual.append((node.get("id"), [int(node.findtext(key)) for key in ("start", "end", "in", "out")],
                               file_id, files.get(file_id), int(node.findtext("sourcetrack/trackindex"))))
            if actual != rows:
                raise ExperimentError("XML clip positions, media URIs or channels differ from the timeline.")


def export(library, name):
    manifest = timeline.load(library, name)
    data = build(library, manifest)
    library.path("exports").mkdir(exist_ok=True)
    filename = f"{name}-{uuid4().hex[:12]}.xml"
    path = library.path("exports", filename)
    with path.open("xb") as stream:
        stream.write(data)
    return {"file": f"exports/{filename}", "bytes": len(data)}
