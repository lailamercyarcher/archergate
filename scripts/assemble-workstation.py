"""Assemble the portable workstation ZIP once Tauri's Windows binary is built."""

import argparse
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "src-tauri" / "target" / "x86_64-pc-windows-msvc" / "release"
PAYLOAD = ROOT / "src-tauri" / "payload"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", default=RELEASE / "archergate.exe", type=Path)
    parser.add_argument("--output-dir", default=ROOT / "artifacts", type=Path)
    args = parser.parse_args()
    executable = args.exe
    with executable.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("The application must be a compiled Windows executable.")
    required = ["python/python.exe", "media-tools/ffmpeg.exe", "media-tools/ffprobe.exe",
                "speech-tools/whisper-cli.exe", "engine/archergate/studio/web_assets/index.html",
                "engine/archergate/studio/web_assets/IBMPlexSans-Regular.woff2",
                "engine/archergate/studio/web_assets/IBMPlexSans-SemiBold.woff2"]
    if any(not (PAYLOAD / name).is_file() for name in required):
        raise ValueError("Run scripts/prepare-workstation.py before assembling.")
    root = args.output_dir
    root.mkdir(parents=True, exist_ok=True)
    name = root / "ArcherGate-0.2.0-win-x64-portable.zip"
    staged = name.with_suffix(".zip.part")
    folder = "ArcherGate-0.2.0"
    try:
        with ZipFile(staged, "w", ZIP_DEFLATED, compresslevel=4) as zip:
            zip.write(executable, f"{folder}/ArcherGate.exe")
            for path in sorted(PAYLOAD.rglob("*")):
                if path.is_file():
                    zip.write(path, f"{folder}/payload/{path.relative_to(PAYLOAD).as_posix()}")
            zip.write(ROOT / "docs" / "workstation-alpha.md", f"{folder}/Install and release notes.md")
        with ZipFile(staged) as zip:
            failed = zip.testzip()
            if failed:
                raise ValueError(f"Invalid archive entry: {failed}")
        staged.replace(name)
    finally:
        staged.unlink(missing_ok=True)
    with name.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    name.with_suffix(".zip.sha256").write_text(f"{digest}  {name.name}\n", encoding="utf-8")
    print(name, digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
