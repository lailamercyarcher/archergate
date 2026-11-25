"""Version 2 editorial timelines with exact time arithmetic and nonmutating review."""

import json
from fractions import Fraction

from archergate.media import ExperimentError, identifier

from .library import user_text
from .operations import check_effects


def rational(value, field):
    if not isinstance(value, str) or len(value) > 32:
        raise ExperimentError(f"{field} needs a positive rational string, for example 1001/24000.")
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as error:
        raise ExperimentError(f"{field} needs a rational number.") from error
    if not 0 < result <= 1_000_000:
        raise ExperimentError(f"{field} is outside the supported positive range.")
    return result


def tick(value, field, minimum=0):
    if type(value) is not int or not minimum <= value <= 10**12:
        raise ExperimentError(f"{field} must be an integer tick count from {minimum} to 10^12.")
    return value


def schema(manifest):
    if not isinstance(manifest, dict) or set(manifest) != {"version", "id", "title", "timebase", "fps", "tracks"}:
        raise ExperimentError("Timeline needs version, id, title, timebase, fps and tracks.")
    if type(manifest["version"]) is not int or manifest["version"] not in (2, 3):
        raise ExperimentError("Timeline version must be 2 or 3.")
    identifier(manifest["id"])
    user_text(manifest["title"], "Title", 250)
    timebase = rational(manifest["timebase"], "Timebase")
    fps = rational(manifest["fps"], "Sequence FPS")
    if timebase > 1 or fps > 240:
        raise ExperimentError("Timeline tick size must be at most one second and FPS at most 240.")
    tracks = manifest["tracks"]
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= 24:
        raise ExperimentError("Timeline needs 1–24 tracks.")
    seen_tracks, seen_clips = set(), set()
    optional = {"sync", "note", "effects"} if manifest["version"] == 3 else {"sync", "note"}
    for track in tracks:
        if not isinstance(track, dict) or set(track) != {"id", "kind", "clips"} or track["kind"] not in ("video", "audio"):
            raise ExperimentError("Each track needs id, video/audio kind and clips.")
        name = identifier(track["id"])
        if name in seen_tracks:
            raise ExperimentError(f"Duplicate track ID {name}.")
        seen_tracks.add(name)
        if not isinstance(track["clips"], list) or len(track["clips"]) > 500:
            raise ExperimentError(f"Track {name} supports up to 500 clips.")
        for clip in track["clips"]:
            required = {"id", "asset", "at", "source_start", "duration"}
            if not isinstance(clip, dict) or not required <= clip.keys() or clip.keys() - required - optional:
                raise ExperimentError("Clips need id, asset, at, source_start and duration. Optional fields are sync and note.")
            clip_id = identifier(clip["id"])
            if clip_id in seen_clips:
                raise ExperimentError(f"Duplicate clip ID {clip_id}.")
            seen_clips.add(clip_id)
            if "sync" in clip:
                identifier(clip["sync"])
            if "note" in clip:
                user_text(clip["note"], "Editorial note", required=False)
            tick(clip["at"], "Timeline start")
            tick(clip["source_start"], "Source start")
            tick(clip["duration"], "Duration", minimum=1)
            if "effects" in clip:
                check_effects(track["kind"], clip["duration"], clip["effects"])
    if not seen_clips:
        raise ExperimentError("Timeline needs at least one clip.")
    return timebase, fps


def inspect(library, manifest):
    base, _ = schema(manifest)
    issues, video, audio, assets = [], [], [], {}
    duration = 0

    def issue(level, code, clip, explanation):
        issues.append({"level": level, "code": code, "clip": clip, "explanation": explanation})

    for track in manifest["tracks"]:
        last_end = 0
        for clip in sorted(track["clips"], key=lambda value: value["at"]):
            start, end = clip["at"], clip["at"] + clip["duration"]
            duration = max(duration, end)
            if start < last_end:
                issue("error", "track-overlap", clip["id"], "Use separate tracks for simultaneous clips.")
            last_end = max(last_end, end)
            try:
                if clip["asset"] not in assets:
                    assets[clip["asset"]] = library.asset(clip["asset"])
                asset = assets[clip["asset"]]
            except (TypeError, ExperimentError) as error:
                issue("error", "unknown-asset", clip["id"], str(error))
                continue
            stream = asset["source"]["streams"].get(track["kind"])
            if stream is None:
                issue("error", "missing-stream", clip["id"], f"Source has no {track['kind']} stream.")
                continue
            source_start = clip["source_start"] * base
            source_end = source_start + clip["duration"] * base
            stream_start = Fraction(stream["start"])
            if not stream_start <= source_start < source_end <= stream_start + Fraction(stream["duration"]):
                issue("error", "source-range", clip["id"], f"Source range falls outside its {track['kind']} stream.")
                continue
            (video if track["kind"] == "video" else audio).append((start, end, clip))
            if track["kind"] == "audio":
                transcript = asset["transcript"]
                if transcript["state"] == "complete":
                    for edge, cut in (("in", source_start), ("out", source_end)):
                        if any(Fraction(segment["start"]) < cut < Fraction(segment["end"])
                               for segment in transcript["segments"]):
                            issue("review", "possible-speech-interruption", clip["id"],
                                  f"The audio {edge}-point falls inside a locally transcribed speech segment.")

    split_edits = []
    for start, end, picture in video:
        covered = start
        for sound_start, sound_end, sound in sorted(audio, key=lambda value: value[0]):
            if sound_start <= covered:
                covered = max(covered, sound_end)
            if picture.get("sync") and picture["sync"] == sound.get("sync"):
                kinds = []
                if sound_start < start < sound_end:
                    kinds.append("J-cut")
                if sound_start < end < sound_end:
                    kinds.append("L-cut")
                if kinds:
                    split_edits.append({"video": picture["id"], "audio": sound["id"], "kinds": kinds})
                if picture["asset"] == sound["asset"] and picture["source_start"] - start != sound["source_start"] - sound_start:
                    issue("review", "possible-sync-offset", picture["id"],
                          f"Linked sound {sound['id']} uses a different original-to-timeline offset.")
                if sound_end <= start or sound_start >= end:
                    issue("review", "linked-clips-disjoint", picture["id"], "Linked sound never overlaps the picture.")
        if covered < end:
            issue("review", "uncovered-picture", picture["id"], "Some picture has no audio coverage. Review intentional silence.")
    return {"id": manifest["id"], "duration_ticks": duration, "duration_seconds": str(duration * base),
            "valid": all(row["level"] != "error" for row in issues), "issues": issues, "split_edits": split_edits}


def save(library, manifest):
    result = inspect(library, manifest)
    if not result["valid"]:
        raise ExperimentError("Fix timeline errors before saving: " + "; ".join(row["explanation"] for row in result["issues"]
                                                                                 if row["level"] == "error")[:1200])
    with library.connect() as db:
        cursor = db.execute("INSERT OR IGNORE INTO timelines VALUES (?, ?)", (manifest["id"], json.dumps(manifest)))
        if cursor.rowcount != 1:
            raise ExperimentError("Timeline ID already exists. Give the alternative a new ID.")
    return result


def load(library, name):
    identifier(name)
    with library.connect() as db:
        row = db.execute("SELECT details FROM timelines WHERE id = ?", (name,)).fetchone()
    if not row:
        raise ExperimentError(f"Unknown timeline {name}.")
    return json.loads(row[0])


def list_timelines(library):
    with library.connect() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT details FROM timelines ORDER BY id LIMIT 250")]


def compare(library, left, right):
    a, b = (load(library, name) for name in (left, right))
    def rows(manifest):
        return {clip["id"]: {"track": track["id"], "kind": track["kind"], "asset": clip["asset"], "effects": clip.get("effects", {}),
                             **{key: str(clip[key] * Fraction(manifest["timebase"])) for key in ("at", "source_start", "duration")}}
                for track in manifest["tracks"] for clip in track["clips"]}
    first, second = rows(a), rows(b)
    return {"left": a, "right": b, "changes": [
        {"clip": clip, "left": first.get(clip), "right": second.get(clip)}
        for clip in sorted(first.keys() | second.keys()) if first.get(clip) != second.get(clip)
    ]}


def demo_timelines(assets):
    picture, _, dialogue, music = assets
    for name, dialogue_start, dialogue_at in (("interview-j", 0, 0), ("interview-l", 0, 24)):
        yield {
            "version": 2, "id": name, "title": f"Synthetic interview / {name[-1].upper()}-cut", "timebase": "1/24", "fps": "24",
            "tracks": [
                {"id": "picture", "kind": "video", "clips": [
                    {"id": "interview", "asset": picture["id"], "at": 24, "source_start": 0, "duration": 36, "sync": "interview"}]},
                {"id": "dialogue", "kind": "audio", "clips": [
                    {"id": "voice", "asset": dialogue["id"], "at": dialogue_at, "source_start": dialogue_start,
                     "duration": 48, "sync": "interview"}]},
                {"id": "music", "kind": "audio", "clips": [
                    {"id": "score", "asset": music["id"], "at": 48, "source_start": 0, "duration": 36}]},
            ],
        }
