"""Create an installable release asset from public integration files only."""

import argparse
import json
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "ps5_local"
ROOT_FILES = {
    "__init__.py",
    "button.py",
    "config_flow.py",
    "const.py",
    "coordinator.py",
    "diagnostics.py",
    "entity.py",
    "icons.json",
    "manifest.json",
    "sensor.py",
    "switch.py",
    "transport.py",
}


def build(destination: Path, tag: str | None = None) -> Path:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    hacs = json.loads((ROOT / "hacs.json").read_text())
    if manifest["domain"] != COMPONENT.name or hacs["filename"] != "ps5_local.zip":
        raise ValueError("Domain and release asset must match")
    if tag is not None and tag.removeprefix("v") != manifest["version"]:
        raise ValueError("Tag and integration version must match")
    public_files = ROOT_FILES | {"brand/icon.png", "brand/icon@2x.png"}
    public_files.update(
        str(file.relative_to(COMPONENT))
        for file in (COMPONENT / "translations").glob("*.json")
        if re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?\.json", file.name)
    )
    if "translations/en.json" not in public_files:
        raise ValueError("English translations are required")

    destination.mkdir(parents=True, exist_ok=True)
    asset = destination / hacs["filename"]
    with ZipFile(asset, "w") as archive:
        for name in sorted(public_files):
            # Fixed timestamps and a closed list keep builds repeatable and
            # exclude development caches, HA configuration and credentials.
            source = COMPONENT / name
            if source.resolve() != source:
                raise ValueError("Release inputs must be ordinary files")
            archive.writestr(
                ZipInfo(name), source.read_bytes(), compress_type=ZIP_DEFLATED
            )
        archive.writestr(
            ZipInfo("LICENSE"),
            (ROOT / "LICENSE").read_bytes(),
            compress_type=ZIP_DEFLATED,
        )
    return asset


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--tag")
    args = parser.parse_args()
    print(build(args.output, args.tag))
