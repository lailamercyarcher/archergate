"""Decoded-output assertions shared by synthetic-media acceptance tests."""

import json
import math
import struct
from fractions import Fraction

from archergate.studio.library import binary, ffmpeg, input_args, run


def decoded_luma(path, timestamp):
    frame = ffmpeg(
        "-ss", f"{timestamp:.6f}", *input_args(path), "-frames:v", "1",
        "-vf", "scale=1:1,format=gray", "-f", "rawvideo", "-",
    )
    if len(frame) != 1:
        raise AssertionError(f"Expected one decoded frame from {path} at {timestamp}s; got {len(frame)} bytes.")
    return frame[0]


def decoded_audio(path, start, duration):
    raw = ffmpeg(
        "-ss", f"{start:.6f}", *input_args(path), "-t", f"{duration:.6f}",
        "-vn", "-ac", "1", "-ar", "48000", "-f", "s16le", "-",
    )
    samples = [sample for (sample,) in struct.iter_unpack("<h", raw)]
    if len(samples) < 2:
        raise AssertionError(
            f"Expected decoded audio from {path} in [{start}, {start + duration})s; got {len(samples)} samples."
        )
    crossings = sum((left < 0) != (right < 0) for left, right in zip(samples, samples[1:]))
    return {
        "path": str(path),
        "interval_seconds": (start, start + duration),
        "samples": len(samples),
        "peak": max(abs(sample) for sample in samples),
        "rms": math.sqrt(sum(sample * sample for sample in samples) / len(samples)),
        "frequency_hz": crossings / (2 * (len(samples) - 1) / 48000),
    }


def decoded_video_timestamps(path):
    data = json.loads(run(
        binary("ffprobe"), "-v", "error", *input_args(path), "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate,nb_frames:frame=best_effort_timestamp_time",
        "-show_frames", "-of", "json",
    ))
    timestamps = [Fraction(frame["best_effort_timestamp_time"]) for frame in data["frames"]]
    stream = data["streams"][0]
    return {
        "path": str(path),
        "rate": stream["avg_frame_rate"],
        "frame_count": len(timestamps),
        "timestamps": timestamps,
    }
