"""Make labeled synthetic sources for local demonstrations and timing tests."""

from .library import ffmpeg


def make_sources(folder, speech=None):
    folder.mkdir(parents=True, exist_ok=True)

    def create(name, *args):
        path = folder / name
        if not path.exists():
            ffmpeg("-n", *args, path)
        return path

    tone = "sine=frequency=330:sample_rate=48000:duration=3"
    mono = ["-f", "lavfi", "-i", tone] if speech is None else ["-i", speech]
    # A scene transition whose expected position can be checked independently.
    picture = create(
        "Synthetic Interview.mp4", "-f", "lavfi", "-i", "color=black:s=352x240:r=24:d=1",
        "-f", "lavfi", "-i", "color=white:s=352x240:r=24:d=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-metadata", "title=SYNTHETIC interview picture",
    )
    delayed = create(
        "Synthetic Delayed Audio.mov", "-f", "lavfi", "-i", "color=0x2e687e:s=704x396:r=30000/1001:d=3",
        "-itsoffset", "0.25", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le",
        "-metadata", "title=SYNTHETIC delayed picture and sound",
    )
    dialogue = create("Synthetic Dialogue.wav", *mono, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
                      "-metadata", "title=SYNTHETIC dialogue fixture")
    music = create("Synthetic Music.mp3", "-f", "lavfi", "-i", "sine=frequency=550:sample_rate=44100:duration=4",
                   "-c:a", "libmp3lame", "-ac", "2", "-metadata", "title=SYNTHETIC musical reference")
    return picture, delayed, dialogue, music
