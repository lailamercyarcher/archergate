"""Editable creative references and decision history, separate from media analysis."""

import json
from datetime import datetime, timezone
from fractions import Fraction
from uuid import uuid4

from archergate.media import ExperimentError

from .library import ASSET_ID, user_text

KINDS = ("film", "scene", "music", "aesthetic", "footage", "annotation", "suggestion")
DECISIONS = ("accepted", "rejected", "modified")


def records(library, kind=""):
    if kind and kind not in KINDS:
        raise ExperimentError("Unknown creative record type.")
    with library.connect() as db:
        return [json.loads(row[0]) for row in db.execute("""
            SELECT details FROM memory
            WHERE ? = '' OR json_extract(details, '$.kind') = ?
            ORDER BY json_extract(details, '$.updated') DESC LIMIT 500
        """, (kind, kind))]


def write(library, record):
    fields = {"id", "kind", "title", "description", "reference", "timestamp", "asset", "decision", "revision"}
    if not isinstance(record, dict) or record.keys() - fields:
        raise ExperimentError("Invalid creative record fields.")
    kind = record.get("kind")
    if kind not in KINDS:
        raise ExperimentError("Choose film, scene, music, aesthetic, footage, annotation or suggestion.")
    title = user_text(record.get("title"), "Title", 250)
    description = user_text(record.get("description"), "Creative notes")
    reference = user_text(record.get("reference", ""), "Reference", 1000, required=False)
    timestamp = user_text(record.get("timestamp", ""), "Timestamp", 32, required=False)
    if timestamp:
        try:
            seconds = Fraction(timestamp)
            if seconds < 0:
                raise ValueError("negative timestamp")
        except (ValueError, ZeroDivisionError) as error:
            raise ExperimentError("Timestamp must be nonnegative seconds, for example 12.5.") from error
        timestamp = str(seconds)
    if kind in ("scene", "annotation") and not timestamp:
        raise ExperimentError("Scene and annotation records need a timestamp.")
    asset = user_text(record.get("asset", ""), "Asset ID", 32, required=False)
    if asset:
        media = library.asset(asset)
        if timestamp and Fraction(timestamp) > Fraction(media["source"]["duration"]):
            raise ExperimentError("Timestamp is outside the referenced asset.")
    if kind in ("footage", "annotation") and not asset:
        raise ExperimentError("Personal footage and annotations need a local asset.")
    decision = user_text(record.get("decision", ""), "Decision", 20, required=False)
    revision = user_text(record.get("revision", ""), "Modified suggestion", required=False)
    if kind == "suggestion":
        if decision not in DECISIONS or (decision == "modified" and not revision):
            raise ExperimentError("Choose an accepted, rejected or modified decision and include the modification if applicable.")
    elif decision or revision:
        raise ExperimentError("Decisions and revisions belong to suggestion records.")
    record_id = record.get("id") or uuid4().hex
    if not isinstance(record_id, str) or not ASSET_ID.fullmatch(record_id):
        raise ExperimentError("Invalid creative record ID.")
    now = datetime.now(timezone.utc).isoformat()
    with library.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT details FROM memory WHERE id = ?", (record_id,)).fetchone()
        if record.get("id") and existing is None:
            raise ExperimentError("Unknown creative record.")
        previous = json.loads(existing[0]) if existing else {}
        if previous and previous["kind"] != kind:
            raise ExperimentError("Keep the original record type when editing.")
        history = previous.get("history", [])
        if kind == "suggestion" and (decision, revision) != (previous.get("decision"), previous.get("revision")):
            history.append({"decision": decision, "revision": revision, "at": now})
        result = {"id": record_id, "kind": kind, "title": title, "description": description, "reference": reference,
                  "timestamp": timestamp, "asset": asset, "decision": decision, "revision": revision,
                  "history": history, "created": previous.get("created", now), "updated": now}
        db.execute("INSERT INTO memory VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET details = excluded.details",
                   (record_id, json.dumps(result)))
    return result
