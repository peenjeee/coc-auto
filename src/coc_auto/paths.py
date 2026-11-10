"""Read-only application resources and separately writable user state."""

from pathlib import Path
import os
import sys
import site

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parents[1]
RESOURCE_ROOT = PACKAGE_ROOT / "resources"
FROZEN = bool(getattr(sys, "frozen", False))
SOURCE_CHECKOUT = (REPO_ROOT / "pyproject.toml").is_file() and (
    REPO_ROOT / "src/coc_auto"
).is_dir()
if FROZEN:
    WEB_ROOT = Path(sys._MEIPASS) / "web"
else:
    candidates = (
        REPO_ROOT / "web",
        Path(sys.prefix) / "share/coc-auto/web",
        Path(site.USER_BASE) / "share/coc-auto/web",
    )
    WEB_ROOT = next(
        (path for path in candidates if (path / "index.html").is_file()), candidates[0]
    )


def default_config() -> Path:
    return RESOURCE_ROOT / "config.json"


def config_argument(value: str) -> Path:
    """Accept the old root-level shared config names, while preserving custom paths."""
    path = Path(value)
    if (
        not path.exists()
        and path.parent == Path(".")
        and path.name in ("config.json", "config.legacy.json", "config.example.json")
    ):
        return RESOURCE_ROOT / path.name
    return path


def is_shared_config(path: Path) -> bool:
    return path.resolve().parent == RESOURCE_ROOT.resolve()


def user_home() -> Path:
    override = os.environ.get("COC_AUTO_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if FROZEN or not SOURCE_CHECKOUT:
        if sys.platform == "darwin":
            return Path.home() / "Library/Application Support/CoC Auto"
        if os.name != "nt":
            return (
                Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
                / "CoC Auto"
            )
        return (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
            / "CoC Auto"
        )
    return REPO_ROOT


def config_root(config: Path) -> Path:
    return RESOURCE_ROOT if is_shared_config(config) else config.resolve().parent


def local_config(config: Path) -> Path:
    if is_shared_config(config):
        return user_home() / (config.stem + ".local.json")
    return config.with_name(config.stem + ".local.json")


def runtime_dir(config: Path | None = None, *, root: Path | None = None) -> Path:
    if config is not None and not is_shared_config(config):
        return config.parent / ".coc-auto"
    if root is not None and root.resolve() != RESOURCE_ROOT.resolve():
        return root / ".coc-auto"
    return user_home() if FROZEN or not SOURCE_CHECKOUT else user_home() / ".coc-auto"


def captures_dir(root: Path) -> Path:
    return (
        user_home() if root.resolve() == RESOURCE_ROOT.resolve() else root
    ) / "captures"


def bundled_ocr() -> Path | None:
    path = Path(getattr(sys, "_MEIPASS", REPO_ROOT)) / "ocr/bin/tesseract.exe"
    return path if FROZEN and path.is_file() else None


def worker_command(config: Path) -> list[str]:
    if FROZEN:
        return [sys.executable, "--config", str(config)]
    return [sys.executable, "-m", "coc_auto", "--config", str(config)]


def ocr_process_options(program: str) -> dict:
    """Use private OCR libraries/models without changing the dashboard's environment.

    Relative model paths also avoid native Windows filename-encoding limitations.
    """
    bundled = bundled_ocr()
    if bundled is None or Path(program).resolve() != bundled.resolve():
        return {}
    environment = dict(os.environ)
    environment["TESSDATA_PREFIX"] = "../tessdata"
    environment["PATH"] = str(bundled.parent) + os.pathsep + environment.get("PATH", "")
    return {
        "cwd": str(bundled.parent),
        "env": environment,
        "creationflags": 0x08000000 if os.name == "nt" else 0,
    }
