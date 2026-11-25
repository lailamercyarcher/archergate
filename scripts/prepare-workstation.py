"""Build the workstation resource directory from pinned upstream Windows releases."""

import argparse
import hashlib
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".tools" / "alpha"
PAYLOAD = ROOT / "src-tauri" / "payload"
FFMPEG = "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1"
WHISPER_VERSION = "1.8.2"
FILES = {
    "python.zip": ("https://www.python.org/ftp/python/3.14.2/python-3.14.2-embed-amd64.zip",
                   "f05e28d161c6b15af64a7cb7f08b4a22b3a6b03eee71baee24ea557b3bdd5798"),
    "ffmpeg.exe": (f"{FFMPEG}/ffmpeg-win32-x64", "04e1307997530f9cf2fe35cba2ca7e8875ca91da02f89d6c7243df819c94ad00"),
    "ffprobe.exe": (f"{FFMPEG}/ffprobe-win32-x64", "3a7e2dc003dc2cd1472827e4c7c4f056ae1ae0ae7c5bbc580c99b49827351ba4"),
    "FFmpeg-LICENSE.txt": (f"{FFMPEG}/win32-x64.LICENSE", "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"),
    "FFmpeg-README.txt": (f"{FFMPEG}/win32-x64.README", "a636a7183c58006351acbaf35303c0ed85c6e1320fd4e80de453ba6157de6311"),
    "whisper.tar.gz": (f"https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/refs/tags/v{WHISPER_VERSION}",
                       "bcee25589bb8052d9e155369f6759a05729a2022d2a8085c1aa4345108523077"),
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


def build_whisper(archive, folder, toolchain):
    """Keep optional transcription independent of a workstation's C++ redistributable."""
    with TemporaryDirectory(prefix="archergate-whisper-") as temporary:
        stage = Path(temporary)
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(stage, filter="data")
        source = stage / f"whisper.cpp-{WHISPER_VERSION}"
        # Older Windows headers omit the optional thread power-throttling API.
        cpu = source / "ggml" / "src" / "ggml-cpu" / "ggml-cpu.c"
        code = cpu.read_bytes()
        guard = b"#if _WIN32_WINNT >= 0x0602\n        THREAD_POWER_THROTTLING_STATE t;"
        if code.count(guard) != 1:
            raise ValueError("Unexpected Whisper Windows thread-priority code.")
        supported = b"0x0602 && defined(THREAD_POWER_THROTTLING_CURRENT_VERSION)"
        cpu.write_bytes(code.replace(guard, guard.replace(b"0x0602", supported)))

        build = stage / "build"
        config = [
            "cmake", "-S", str(source), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW", "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded",
            "-DBUILD_SHARED_LIBS=OFF", "-DWHISPER_BUILD_TESTS=OFF", "-DWHISPER_BUILD_SERVER=OFF",
            "-DGGML_NATIVE=OFF", "-DGGML_OPENMP=OFF", "-DGGML_AVX=OFF", "-DGGML_AVX2=OFF",
            "-DGGML_BMI2=OFF", "-DGGML_SSE42=OFF", "-DGGML_FMA=OFF", "-DGGML_F16C=OFF", "-DGGML_CCACHE=OFF",
        ]
        if toolchain:
            config.append(f"-DCMAKE_TOOLCHAIN_FILE={toolchain}")
        else:
            config.extend(("-G", "Visual Studio 17 2022", "-A", "x64"))
        subprocess.run(config, check=True)
        subprocess.run(["cmake", "--build", str(build), "--target", "whisper-cli",
                        "--config", "Release", "--parallel", "3"], check=True)
        candidates = (build / "bin" / "whisper-cli.exe", build / "bin" / "Release" / "whisper-cli.exe")
        executable = next((candidate for candidate in candidates if candidate.is_file()), None)
        if executable is None:
            raise ValueError("The Whisper build did not produce a Windows executable.")
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(executable, folder / executable.name)
        for old in ("whisper.dll", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll"):
            (folder / old).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--whisper-toolchain", type=Path,
                        help="Windows x64 CMake toolchain with static C and C++ runtimes, for cross-builds")
    args = parser.parse_args()
    if os.name != "nt" and args.whisper_toolchain is None:
        parser.error("supply --whisper-toolchain when preparing a Windows payload from another platform")
    if args.whisper_toolchain and not args.whisper_toolchain.is_file():
        parser.error("--whisper-toolchain must name a CMake toolchain file")
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
    toolchain = args.whisper_toolchain.resolve() if args.whisper_toolchain else None
    build_whisper(files["whisper.tar.gz"], speech, toolchain)
    shutil.copyfile(files["Whisper-LICENSE.txt"], speech / "LICENSE.txt")
    shutil.copytree(ROOT / "archergate", PAYLOAD / "engine" / "archergate", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"Prepared resources in {PAYLOAD}")


if __name__ == "__main__":
    main()
