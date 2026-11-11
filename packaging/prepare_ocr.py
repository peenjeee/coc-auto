"""Assemble the pinned Windows OCR runtime, checking every download's SHA-256.

Runs on any platform. Only binary dependencies, tessdata, and license notices ship.
"""

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import struct
import tarfile
import urllib.request
import zipfile

import zstandard

ROOT = Path(__file__).resolve().parent.parent
# DLLs Windows itself gives the OCR runtime. The Universal CRT (ucrtbase.dll and the api-ms-win-* sets) ships with
# Windows 10 and later, and the app's own Python relies on it the same way.
WINDOWS_DLLS = {
    "advapi32.dll", "bcrypt.dll", "crypt32.dll", "gdi32.dll", "iphlpapi.dll", "kernel32.dll",
    "secur32.dll", "ucrtbase.dll", "user32.dll", "wldap32.dll", "ws2_32.dll", "wsock32.dll",
}


def pe_imports(data):
    """DLL names a Windows executable or DLL imports, delay-loaded ones included."""
    header = struct.unpack_from("<I", data, 0x3C)[0]
    if data[:2] != b"MZ" or data[header : header + 4] != b"PE\0\0":
        raise ValueError("not a Windows binary")
    count = struct.unpack_from("<H", data, header + 6)[0]
    optional_size = struct.unpack_from("<H", data, header + 20)[0]
    optional = header + 24
    directories = optional + (112 if struct.unpack_from("<H", data, optional)[0] == 0x20B else 96)
    sections = [struct.unpack_from("<IIII", data, optional + optional_size + 40 * index + 8) for index in range(count)]

    def offset(rva):
        for size, address, raw_size, raw_pointer in sections:
            if address <= rva < address + max(size, raw_size):
                return rva - address + raw_pointer
        raise ValueError("address outside every section")

    names = []
    # (data directory, descriptor size, name field): the import table, then the delay-load table.
    for directory, size, field in ((1, 20, 3), (13, 32, 1)):
        table = struct.unpack_from("<I", data, directories + 8 * directory)[0]
        position = offset(table) if table else None
        while position is not None:
            descriptor = struct.unpack_from(f"<{size // 4}I", data, position)
            if not any(descriptor):
                break
            start = offset(descriptor[field])
            names.append(data[start : data.index(b"\0", start)].decode("ascii"))
            position += size
    return names


def prune(bin_dir):
    """Keep only the DLLs tesseract.exe loads; fail on one that neither the lock nor Windows provides."""
    files = {path.name.lower(): path for path in bin_dir.iterdir()}
    needed, pending = set(), ["tesseract.exe"]
    while pending:
        name = pending.pop()
        if name in needed or name in WINDOWS_DLLS or name.startswith("api-ms-win-"):
            continue
        if name not in files:
            raise RuntimeError(f"OCR runtime needs {name}, which no locked package provides")
        needed.add(name)
        pending += [dll.lower() for dll in pe_imports(files[name].read_bytes())]
    for name, path in files.items():
        if name not in needed:
            path.unlink()


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
    prune(output / "bin")
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
