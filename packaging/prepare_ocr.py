"""Assemble the pinned Windows OCR runtime, checking every download's SHA-256.

Runs on any platform. Only binary dependencies, tessdata, and license notices ship.
"""

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import urllib.request
import zipfile

import zstandard

ROOT = Path(__file__).resolve().parent.parent


def extract_member(member, data, output, package):
    name = PurePosixPath(member.name)
    if not member.isfile() or name.is_absolute() or ".." in name.parts:
        return
    if name.parts[:2] == ("Library", "bin") and (
        name.suffix.lower() == ".dll" or name.name == "tesseract.exe"
    ):
        target = output / "bin" / name.name
    elif "tessdata" in name.parts and name.name in (
        "eng.traineddata",
        "ind.traineddata",
        "osd.traineddata",
    ):
        target = output / "tessdata" / name.name
    elif name.parts[:2] == ("info", "licenses"):
        target = output / "licenses" / package / Path(*name.parts[2:])
    else:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    content = data.read()
    if target.exists() and target.read_bytes() != content:
        raise RuntimeError(f"Conflicting runtime files: {target.name}")
    target.write_bytes(content)


def prepare(output):
    lock = json.loads((ROOT / "packaging/windows-ocr.lock.json").read_text())
    cache = ROOT / "build/downloads"
    cache.mkdir(parents=True, exist_ok=True)
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for package in lock["packages"]:
        archive = cache / Path(package["url"]).name
        if not archive.exists():
            with urllib.request.urlopen(package["url"], timeout=120) as response:
                archive.write_bytes(response.read())
        if hashlib.sha256(archive.read_bytes()).hexdigest() != package["sha256"]:
            raise RuntimeError(f"Checksum mismatch: {archive.name}")
        with zipfile.ZipFile(archive) as conda:
            for filename in conda.namelist():
                if not filename.endswith(".tar.zst"):
                    continue
                with conda.open(filename) as compressed:
                    with zstandard.ZstdDecompressor().stream_reader(
                        compressed
                    ) as stream:
                        with tarfile.open(fileobj=stream, mode="r|") as contents:
                            for member in contents:
                                if member.isfile():
                                    extract_member(
                                        member,
                                        contents.extractfile(member),
                                        output,
                                        package["name"],
                                    )
    # Conda's Tesseract package supplies the engine but can omit individual language models.
    for model in lock.get("models", []):
        target = output / "tessdata" / model["name"]
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(model["url"], timeout=120) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != model["sha256"]:
            raise RuntimeError("OCR model checksum mismatch")
        target.write_bytes(data)
    for name in (
        "bin/tesseract.exe",
        "tessdata/eng.traineddata",
        "tessdata/ind.traineddata",
    ):
        if not (output / name).is_file():
            raise RuntimeError(f"Missing OCR runtime: {name}")
    shutil.copy2(ROOT / "packaging/windows-ocr.lock.json", output / "sources.json")


if __name__ == "__main__":
    prepare(ROOT / "build/ocr")
