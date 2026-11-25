"""Assemble the portable workstation ZIP once Tauri's Windows binary is built."""

import argparse
import hashlib
import json
import shutil
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "src-tauri" / "target" / "x86_64-pc-windows-msvc" / "release"
PAYLOAD = ROOT / "src-tauri" / "payload"
REQUIRED = (
    "python/python.exe", "python/python314.dll", "python/python314.zip", "python/python314._pth",
    "python/_socket.pyd", "python/_sqlite3.pyd", "python/sqlite3.dll", "python/LICENSE.txt",
    "media-tools/ffmpeg.exe", "media-tools/ffprobe.exe", "media-tools/FFmpeg-LICENSE.txt",
    "media-tools/FFmpeg-README.txt", "speech-tools/whisper-cli.exe", "speech-tools/LICENSE.txt",
    "engine/archergate/studio/__main__.py", "engine/archergate/studio/web.py",
    "engine/archergate/studio/web_assets/index.html", "engine/archergate/studio/web_assets/app.js",
    "engine/archergate/studio/web_assets/style.css", "engine/archergate/studio/web_assets/Hauora-Regular.woff2",
    "engine/archergate/studio/web_assets/Hauora-Bold.woff2", "engine/archergate/studio/web_assets/Hauora-OFL.txt",
    "engine/archergate/studio/web_assets/IBMPlexSans-Regular.woff2",
    "engine/archergate/studio/web_assets/IBMPlexSans-SemiBold.woff2",
    "engine/archergate/studio/web_assets/IBMPlexSans-OFL.txt",
)


def app_version():
    config = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    with (ROOT / "src-tauri" / "Cargo.toml").open("rb") as stream:
        package = tomllib.load(stream)["package"]
    if config["version"] != package["version"]:
        raise ValueError("Tauri configuration and Cargo manifest versions must agree.")
    return config["version"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", default=RELEASE / "archergate.exe", type=Path)
    parser.add_argument("--payload-dir", default=PAYLOAD, type=Path)
    parser.add_argument("--output-dir", default=ROOT / "artifacts", type=Path)
    args = parser.parse_args()
    executable = args.exe
    with executable.open("rb") as stream:
        header = stream.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            raise ValueError("The application must be a compiled Windows x64 executable.")
        stream.seek(int.from_bytes(header[60:64], "little"))
        if stream.read(6) != b"PE\0\0\x64\x86":
            raise ValueError("The application must be a compiled Windows x64 executable.")
    payload = args.payload_dir
    missing = [name for name in REQUIRED if not (payload / name).is_file() or not (payload / name).stat().st_size]
    if missing:
        raise ValueError(f"Missing required payload files: {', '.join(missing)}")
    root = args.output_dir
    root.mkdir(parents=True, exist_ok=True)
    folder = f"ArcherGate-{app_version()}"
    name = root / f"{folder}-win-x64-portable.zip"
    checksum = name.with_suffix(".zip.sha256")
    if name.exists() or checksum.exists():
        raise FileExistsError(f"Choose a fresh output directory. An artifact already exists at {name}.")
    with TemporaryDirectory(prefix=".workstation-", dir=root) as temporary:
        staged = Path(temporary) / name.name
        with ZipFile(staged, "w", ZIP_DEFLATED, compresslevel=4) as zip:
            zip.write(executable, f"{folder}/ArcherGate.exe")
            for path in sorted(payload.rglob("*")):
                if path.is_file():
                    zip.write(path, f"{folder}/payload/{path.relative_to(payload).as_posix()}")
            zip.write(ROOT / "docs" / "workstation-alpha.md", f"{folder}/Install and release notes.md")
        with ZipFile(staged) as zip:
            failed = zip.testzip()
            if failed:
                raise ValueError(f"Invalid archive entry: {failed}")
            required_entries = {f"{folder}/payload/{path}" for path in REQUIRED} | {f"{folder}/ArcherGate.exe"}
            if not required_entries <= set(zip.namelist()):
                raise ValueError("Portable ZIP is missing required packaged files.")
        with staged.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        staged_checksum = Path(temporary) / checksum.name
        staged_checksum.write_text(f"{digest}  {name.name}\n", encoding="ascii")
        published = []
        try:
            for source, destination in ((staged, name), (staged_checksum, checksum)):
                with source.open("rb") as stream, destination.open("xb") as output:
                    published.append(destination)
                    shutil.copyfileobj(stream, output)
        except OSError:
            for path in published:
                path.unlink()
            raise
    print(f"Verified portable ZIP CRCs and {len(REQUIRED)} required payload files.")
    print(name, digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
