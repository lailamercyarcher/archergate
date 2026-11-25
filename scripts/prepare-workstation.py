"""Build the workstation resource directory from pinned upstream Windows releases."""

import hashlib
import shutil
import zipfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".tools" / "alpha"
PAYLOAD = ROOT / "src-tauri" / "payload"
FFMPEG = "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1"
WHISPER = "https://github.com/ggml-org/whisper.cpp"
FILES = {
    "python.zip": ("https://www.python.org/ftp/python/3.14.2/python-3.14.2-embed-amd64.zip",
                   "f05e28d161c6b15af64a7cb7f08b4a22b3a6b03eee71baee24ea557b3bdd5798"),
    "ffmpeg.exe": (f"{FFMPEG}/ffmpeg-win32-x64", "04e1307997530f9cf2fe35cba2ca7e8875ca91da02f89d6c7243df819c94ad00"),
    "ffprobe.exe": (f"{FFMPEG}/ffprobe-win32-x64", "3a7e2dc003dc2cd1472827e4c7c4f056ae1ae0ae7c5bbc580c99b49827351ba4"),
    "FFmpeg-LICENSE.txt": (f"{FFMPEG}/win32-x64.LICENSE", "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"),
    "FFmpeg-README.txt": (f"{FFMPEG}/win32-x64.README", "a636a7183c58006351acbaf35303c0ed85c6e1320fd4e80de453ba6157de6311"),
    "whisper.zip": (f"{WHISPER}/releases/download/v1.8.2/whisper-bin-x64.zip",
                    "b1514ebc099765e39fa37eb780b92a140a94c86bb0b3b3d98226b38825979732"),
    "Whisper-LICENSE.txt": ("https://raw.githubusercontent.com/ggml-org/whisper.cpp/v1.8.2/LICENSE",
                           "e562a2ddfaf8280537795ac5ecd34e3012b6582a147ef69ba6a6a5c08c84757d"),
}


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(name, url, digest):
    path = CACHE / name
    if path.is_file() and checksum(path) == digest:
        return path
    staged = path.with_suffix(path.suffix + ".part")
    try:
        with urlopen(url, timeout=90) as response, staged.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        if checksum(staged) != digest:
            raise ValueError(f"Unexpected SHA-256 for {name}.")
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)
    return path


def extract_files(archive, folder, names):
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for source in names:
            with bundle.open(source) as contents, (folder / Path(source).name).open("wb") as output:
                shutil.copyfileobj(contents, output)


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    files = {name: download(name, *source) for name, source in FILES.items()}
    python = PAYLOAD / "python"
    with zipfile.ZipFile(files["python.zip"]) as package:
        names = [name for name in package.namelist() if "/" not in name and not name.endswith("/")]
    extract_files(files["python.zip"], python, names)
    (python / "python314._pth").write_text("python314.zip\n.\n../engine\n", encoding="utf-8")
    for name in ("ffmpeg.exe", "ffprobe.exe", "FFmpeg-LICENSE.txt", "FFmpeg-README.txt"):
        folder = PAYLOAD / "media-tools"
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(files[name], folder / name)
    speech = PAYLOAD / "speech-tools"
    extract_files(files["whisper.zip"], speech, [
        f"Release/{name}" for name in ("whisper-cli.exe", "whisper.dll", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll")
    ])
    shutil.copyfile(files["Whisper-LICENSE.txt"], speech / "LICENSE.txt")
    shutil.copytree(ROOT / "archergate", PAYLOAD / "engine" / "archergate", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"Prepared resources in {PAYLOAD}")


if __name__ == "__main__":
    main()
