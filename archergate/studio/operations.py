"""Shared editorial commands and cinematic effect validation."""

from copy import deepcopy
from fractions import Fraction
from math import ceil, floor, isfinite

from archergate.media import ExperimentError, identifier

from .library import user_text



def bounded(value, low, high, label):
    if type(value) not in (int, float) or not low <= value <= high or not isfinite(value):
        raise ExperimentError(f"{label} must be a number from {low} to {high}.")
    return value


def check_effects(kind, duration, effects):
    allowed = {"fade_in", "fade_out"} | ({"transform"} if kind == "video" else {"gain_db", "volume_points"})
    if not isinstance(effects, dict) or not effects or effects.keys() - allowed:
        raise ExperimentError(f"Unsupported {kind} effects. Supported effects are {', '.join(sorted(allowed))}.")
    fades = [effects[field] for field in ("fade_in", "fade_out") if field in effects]
    if any(type(length) is not int or not 1 <= length <= duration for length in fades) or sum(fades) > duration:
        raise ExperimentError("Fade lengths must be positive integer ticks and fit together inside the clip.")
    if "gain_db" in effects:
        bounded(effects["gain_db"], -60, 12, "Audio gain")
    if "volume_points" in effects:
        _check_points(effects["volume_points"], duration, {"tick", "gain_db"})
        for point in effects["volume_points"]:
            bounded(point["gain_db"], -60, 12, "Volume keyframe gain")
    if "transform" in effects:
        _check_points(effects["transform"], duration, {"tick", "scale", "x", "y", "ease"})
        for point in effects["transform"]:
            if point["ease"] not in ("linear", "smoothstep", "hold"):
                raise ExperimentError("Choose linear, smoothstep or hold interpolation.")
            bounded(point["scale"], 1, 2.5, "Digital scale")
            bounded(point["x"], 0, 1, "Horizontal position")
            bounded(point["y"], 0, 1, "Vertical position")


def _check_points(points, duration, fields):
    if not isinstance(points, list) or not 1 <= len(points) <= 24:
        raise ExperimentError("An effect requires 1–24 keyframes.")
    previous = -1
    for point in points:
        if not isinstance(point, dict) or set(point) != fields:
            raise ExperimentError(f"Keyframes need {', '.join(sorted(fields))}.")
        tick = point["tick"]
        if type(tick) is not int or not previous < tick <= duration or (previous < 0 and tick != 0):
            raise ExperimentError("Keyframe ticks must start at zero, increase, and stay within the clip.")
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


def edit(library, operation, command, *, prepare=False, require_parent=True):
    """Validate and prepare or commit one supported manual edit."""
    from . import timeline

    if not isinstance(operation, str) or operation not in EDITS:
        raise ExperimentError(f"Supported editing operations are {', '.join(EDITS)}.")
    fields, build = EDITS[operation]
    required = fields | {"base_id", "revision_id"}
    if require_parent:
        required.add("parent_sha256")
    if not isinstance(command, dict) or not required <= command.keys() <= required | {"parent_sha256"}:
        optional = "" if require_parent else " (optional parent_sha256)"
        raise ExperimentError(f"{operation} requires {', '.join(sorted(required))}{optional}.")
    base_id = identifier(command["base_id"])
    revision_id = identifier(command["revision_id"])
    if revision_id == base_id:
        raise ExperimentError("Choose a new revision ID distinct from the source timeline.")

    source = timeline.load(library, base_id)
    parent_sha256 = timeline.digest(source)
    if "parent_sha256" in command and command["parent_sha256"] != parent_sha256:
        raise ExperimentError("The parent revision changed or does not match the supplied fingerprint.")
    timebase, _ = timeline.schema(source)
    draft = deepcopy(source)
    draft["id"] = revision_id
    parameters = {field: command[field] for field in fields}
    suffix = build(library, draft, base_id, timebase, **parameters)
    draft["title"] = source["title"][:250 - len(suffix)] + suffix
    if prepare:
        return {
            "manifest": draft, "sha256": timeline.digest(draft),
            "parent_id": base_id, "parent_sha256": parent_sha256, "operation": operation,
            "validation": timeline.prepare_revision(library, draft),
        }
    return timeline.commit_revision(
        library, draft, parent_id=base_id, parent_sha256=parent_sha256,
        operation=operation, payload={**parameters, "revision_id": revision_id},
    )


def _insert(library, draft, base_id, timebase, *, asset_id, track_id, source_start, duration, at):
    from . import timeline

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
    source_in = source_start * timebase
    source_out = (source_start + duration) * timebase
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
        "id": f"insert-{timeline.digest(draft['id'])[:32]}",
        "asset": asset_id,
        "at": at,
        "source_start": source_start,
        "duration": duration,
    })
    return " / insert"


def _find_clip(draft, base_id, clip_id):
    identifier(clip_id)
    for track in draft["tracks"]:
        for clip in track["clips"]:
            if clip["id"] == clip_id:
                return track, clip
    raise ExperimentError(f"Unknown clip {clip_id} in timeline {base_id}.")


def _check_clip_length(draft, timebase, duration):
    if duration * timebase * Fraction(draft["fps"]) < 1:
        raise ExperimentError("An edit must leave at least one frame in each resulting clip.")


def _trim(_library, draft, base_id, timebase, *, clip_id, edge, amount):
    from . import timeline

    if edge not in ("in", "out"):
        raise ExperimentError("Choose whether to trim the clip in-point or out-point.")
    amount = timeline.tick(amount, "Trim amount", minimum=1)
    _, clip = _find_clip(draft, base_id, clip_id)
    _check_clip_length(draft, timebase, clip["duration"] - amount)
    if clip.get("effects", {}).keys() & {"fade_in", "fade_out", "volume_points"}:
        raise ExperimentError("Choose a revision before fades or volume automation to trim this clip.")
    if edge == "in":
        clip["at"] += amount
        clip["source_start"] += amount
    clip["duration"] -= amount
    return f" / trim {edge}"


def _move(_library, draft, base_id, _timebase, *, clip_id, at):
    from . import timeline

    at = timeline.tick(at, "New timeline start")
    _, clip = _find_clip(draft, base_id, clip_id)
    if at == clip["at"]:
        raise ExperimentError("Choose a different timeline start.")
    clip["at"] = at
    return " / move"


def _split(_library, draft, base_id, timebase, *, clip_id, at):
    from . import timeline

    at = timeline.tick(at, "Split point")
    track, clip = _find_clip(draft, base_id, clip_id)
    left = at - clip["at"]
    right = clip["duration"] - left
    _check_clip_length(draft, timebase, left)
    _check_clip_length(draft, timebase, right)
    if clip.get("effects", {}).keys() - {"gain_db"}:
        raise ExperimentError("Choose a revision before timed effects to split this clip.")
    remainder = {**clip, "id": f"split-{timeline.digest(draft['id'])[:32]}", "at": at,
                 "source_start": clip["source_start"] + left, "duration": right}
    track["clips"].insert(track["clips"].index(clip) + 1, remainder)
    clip["duration"] = left
    return " / split"


def _insert_passage(library, draft, base_id, timebase, *, asset_id, track_id, segment, at):
    from . import timeline

    timeline.tick(segment, "Transcript segment")
    identifier(track_id)
    asset = library.asset(asset_id)
    transcript = asset["transcript"]
    if transcript["state"] != "complete" or segment >= len(transcript["segments"]):
        raise ExperimentError("Choose an available transcript passage.")
    track = next((row for row in draft["tracks"] if row["id"] == track_id), None)
    if track is None or track["kind"] not in asset["source"]["streams"]:
        raise ExperimentError("Choose a destination track with a matching source stream.")
    passage = transcript["segments"][segment]
    stream = asset["source"]["streams"][track["kind"]]
    stream_start = Fraction(stream["start"])
    stream_end = stream_start + Fraction(stream["duration"])
    start, end = Fraction(passage["start"]), Fraction(passage["end"])
    if start >= stream_end or end <= stream_start:
        raise ExperimentError("The selected stream does not cover this passage.")
    frame = 1 / (timebase * Fraction(draft["fps"]))
    step = int(frame) if frame.denominator == 1 else 1
    source_start = max(ceil(stream_start / timebase / step), floor(start / timebase / step), 0) * step
    source_end = min(floor(stream_end / timebase / step), ceil(end / timebase / step)) * step
    _check_clip_length(draft, timebase, source_end - source_start)
    _insert(library, draft, base_id, timebase, asset_id=asset_id, track_id=track_id,
            source_start=source_start, duration=source_end - source_start, at=at)
    return " / passage"


EDITS = {
    "insert_source_range": ({"asset_id", "track_id", "source_start", "duration", "at"}, _insert),
    "trim_clip": ({"clip_id", "edge", "amount"}, _trim),
    "move_clip": ({"clip_id", "at"}, _move),
    "split_clip": ({"clip_id", "at"}, _split),
    "insert_passage": ({"asset_id", "track_id", "segment", "at"}, _insert_passage),
}


def insert_source_range(library, base_id, asset_id, track_id, source_start, duration, at, revision_id):
    return edit(library, "insert_source_range", {
        "base_id": base_id, "asset_id": asset_id, "track_id": track_id, "source_start": source_start,
        "duration": duration, "at": at, "revision_id": revision_id,
    }, require_parent=False)


def trim_clip(library, base_id, clip_id, edge, amount, revision_id):
    return edit(library, "trim_clip", {
        "base_id": base_id, "clip_id": clip_id, "edge": edge, "amount": amount, "revision_id": revision_id,
    }, require_parent=False)


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
