"""Data-only cinematic operations shared by timeline validation and preview rendering."""

from copy import deepcopy
from fractions import Fraction
from math import isfinite
from uuid import uuid4

from archergate.media import ExperimentError, identifier

from .library import user_text



def bounded(value, low, high, label):
    if type(value) not in (int, float) or not low <= value <= high or not isfinite(value):
        raise ExperimentError(f"{label} must be a number from {low} to {high}.")
    return value


def check_effects(kind, duration, effects):
    allowed = {"transform"} if kind == "video" else {"gain_db"}
    if not isinstance(effects, dict) or not effects or effects.keys() - allowed:
        raise ExperimentError(f"Unsupported {kind} effects. Supported effects are {', '.join(allowed)}.")
    if kind == "audio":
        bounded(effects["gain_db"], -60, 12, "Audio gain")
        return
    points = effects["transform"]
    if not isinstance(points, list) or not 1 <= len(points) <= 24:
        raise ExperimentError("Transform requires 1–24 keyframes.")
    previous = -1
    for point in points:
        if not isinstance(point, dict) or set(point) != {"tick", "scale", "x", "y", "ease"}:
            raise ExperimentError("Transform keyframes need tick, scale, x, y and ease.")
        tick = point["tick"]
        if type(tick) is not int or not previous < tick <= duration or (previous < 0 and tick != 0):
            raise ExperimentError("Keyframe ticks must start at zero, increase, and stay within the clip.")
        if point["ease"] not in ("linear", "smoothstep", "hold"):
            raise ExperimentError("Choose linear, smoothstep or hold interpolation.")
        bounded(point["scale"], 1, 2.5, "Digital scale")
        bounded(point["x"], 0, 1, "Horizontal position")
        bounded(point["y"], 0, 1, "Vertical position")
        previous = tick


def apply(library, base_id, proposal):
    from . import timeline

    if not isinstance(proposal, dict) or set(proposal) != {"id", "title", "operations"}:
        raise ExperimentError("A proposal needs id, title and operations.")
    source = timeline.load(library, base_id)
    parent_sha256 = timeline.digest(source)
    draft = deepcopy(source)
    draft.update(version=3, id=identifier(proposal["id"]), title=user_text(proposal["title"], "Revision title", 250))
    ops = proposal["operations"]
    if not isinstance(ops, list) or not 1 <= len(ops) <= 48:
        raise ExperimentError("A revision must contain 1–48 operations.")
    clips = {clip["id"]: (track["kind"], clip) for track in draft["tracks"] for clip in track["clips"]}
    used = set()
    for op in ops:
        if not isinstance(op, dict) or set(op) != {"clip", "effects"}:
            raise ExperimentError("Each operation needs clip and effects.")
        name = identifier(op["clip"])
        if name not in clips or name in used:
            raise ExperimentError("Each operation must target a distinct clip from the base edit.")
        used.add(name)
        kind, clip = clips[name]
        check_effects(kind, clip["duration"], op["effects"])
        clip["effects"] = op["effects"]
    return timeline.commit_revision(
        library, draft, parent_id=base_id, parent_sha256=parent_sha256,
        operation="apply_effects", payload=proposal,
    )


def insert_source_range(library, base_id, asset_id, track_id, source_start, duration, at, revision_id):
    from . import timeline

    source = timeline.load(library, base_id)
    parent_sha256 = timeline.digest(source)
    draft = deepcopy(source)
    base, _ = timeline.schema(source)
    revision_id = identifier(revision_id)
    if revision_id == base_id:
        raise ExperimentError("Choose a new revision ID distinct from the source timeline.")
    track_id = identifier(track_id)
    at = timeline.tick(at, "Timeline insertion position")
    source_start = timeline.tick(source_start, "Source in-point")
    duration = timeline.tick(duration, "Source duration", minimum=1)

    track = next((row for row in draft["tracks"] if row["id"] == track_id), None)
    if track is None:
        raise ExperimentError(f"Unknown track {track_id} in timeline {base_id}.")
    if len(track["clips"]) >= 500:
        raise ExperimentError(f"Track {track_id} already contains 500 clips.")

    asset = library.asset(asset_id)
    stream = asset["source"]["streams"].get(track["kind"])
    if stream is None:
        raise ExperimentError(f"Asset {asset_id} has no {track['kind']} stream for track {track_id}.")
    source_in = source_start * base
    source_out = (source_start + duration) * base
    stream_in = Fraction(stream["start"])
    stream_out = stream_in + Fraction(stream["duration"])
    if not stream_in <= source_in < source_out <= stream_out:
        raise ExperimentError(
            f"Source range {source_start}–{source_start + duration} ticks falls outside "
            f"the asset's {track['kind']} stream."
        )

    end = at + duration
    for clip in track["clips"]:
        if at < clip["at"] + clip["duration"] and clip["at"] < end:
            raise ExperimentError(
                f"Timeline insertion overlaps clip {clip['id']} on track {track_id}; "
                "non-ripple insertion does not move existing clips."
            )

    track["clips"].append({
        "id": f"insert-{uuid4().hex}",
        "asset": asset_id,
        "at": at,
        "source_start": source_start,
        "duration": duration,
    })
    suffix = " / insert"
    draft["id"] = revision_id
    draft["title"] = source["title"][:250 - len(suffix)] + suffix
    return timeline.commit_revision(
        library, draft, parent_id=base_id, parent_sha256=parent_sha256,
        operation="insert_source_range",
        payload={
            "asset_id": asset_id, "track_id": track_id, "source_start": source_start,
            "duration": duration, "at": at, "revision_id": revision_id,
        },
    )


def trim_clip(library, base_id, clip_id, edge, amount, revision_id):
    from . import timeline

    source = timeline.load(library, base_id)
    parent_sha256 = timeline.digest(source)
    draft = deepcopy(source)
    timeline.schema(source)
    revision_id = identifier(revision_id)
    if revision_id == base_id:
        raise ExperimentError("Choose a new revision ID distinct from the source timeline.")
    clip_id = identifier(clip_id)
    if edge not in ("in", "out"):
        raise ExperimentError("Choose whether to trim the clip in-point or out-point.")
    amount = timeline.tick(amount, "Trim amount", minimum=1)
    clip = next(
        (clip for track in draft["tracks"] for clip in track["clips"] if clip["id"] == clip_id),
        None,
    )
    if clip is None:
        raise ExperimentError(f"Unknown clip {clip_id} in timeline {base_id}.")
    if amount >= clip["duration"]:
        raise ExperimentError("A trim must leave at least one frame in the clip.")
    if edge == "in":
        clip["at"] += amount
        clip["source_start"] += amount
    clip["duration"] -= amount
    suffix = f" / trim {edge}"
    draft["id"] = revision_id
    draft["title"] = source["title"][:250 - len(suffix)] + suffix
    return timeline.commit_revision(
        library, draft, parent_id=base_id, parent_sha256=parent_sha256,
        operation="trim_clip",
        payload={"clip_id": clip_id, "edge": edge, "amount": amount, "revision_id": revision_id},
    )


def transform_filter(points, base, fps, width, height):
    def expression(field):
        result = str(points[-1][field])
        for first, last in reversed(list(zip(points, points[1:]))):
            start = Fraction(first["tick"]) * base * fps
            end = Fraction(last["tick"]) * base * fps
            if first["ease"] == "hold":
                value = str(first[field])
            else:
                t = f"clip((on-({float(start):.8f}))/({float(end-start):.8f}),0,1)"
                if first["ease"] == "smoothstep":
                    t = f"({t}*{t}*(3-2*{t}))"
                value = f"({first[field]}+({last[field] - first[field]})*{t})"
            result = f"if(lt(on,{float(end):.8f}),{value},{result})"
        return result
    return (f"zoompan=z='{expression('scale')}':x='(iw-iw/zoom)*({expression('x')})':"
            f"y='(ih-ih/zoom)*({expression('y')})':d=1:s={width}x{height}:fps={fps}")
