"""Build a private snapshot of the user's Crucible clone without changing the clone."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import requests

from mtg_pipeline.config import load_config
from mtg_pipeline.renderers.crucible import cache_root, node_path
from mtg_pipeline.utils.cache import file_digest, write_json


def download_windows_node(root: Path) -> Path:
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Portable Node installation currently supports Windows x64. Install Node.js through your platform's installer.")
    base = "https://nodejs.org/dist/latest-v22.x/"
    response = requests.get(base + "SHASUMS256.txt", timeout=60)
    response.raise_for_status()
    match = next((line.split() for line in response.text.splitlines() if line.endswith("-win-x64.zip")), None)
    if not match:
        raise RuntimeError("The official Node.js download manifest did not contain a Windows x64 ZIP.")
    checksum, filename = match
    if Path(filename).name != filename or not filename.startswith("node-v"):
        raise RuntimeError("Unexpected filename in the Node.js download manifest.")
    response = requests.get(base + filename, timeout=180)
    response.raise_for_status()
    if hashlib.sha256(response.content).hexdigest() != checksum:
        raise RuntimeError("Official Node.js archive checksum verification failed.")
    target = (root / ".cache/node").resolve()
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        for entry in archive.infolist():
            if not (target / entry.filename).resolve().is_relative_to(target):
                raise RuntimeError("Node.js archive contains an unsafe path.")
        archive.extractall(target)
    return target / filename.removesuffix(".zip") / "node.exe"


def prepare(config, root: Path, download_node: bool = False) -> Path:
    root = root.resolve()
    source = (root / config.crucible.repository_path).resolve()
    if not (source / "package.json").is_file() or not (source / "src/index.ts").is_file():
        raise RuntimeError(f"Crucible clone not found at {source}. Set crucible.repository_path in your config.")
    package = json.loads((source / "package.json").read_text(encoding="utf-8"))
    if package.get("name") != "mtg-crucible":
        raise RuntimeError("The selected repository is not mtg-crucible.")
    try:
        node = Path(node_path(config, root))
    except RuntimeError:
        if not download_node:
            raise
        node = download_windows_node(root)
    runtime = cache_root(config, root) / "runtime"
    if source == runtime or runtime.is_relative_to(source) or source.is_relative_to(runtime):
        raise ValueError("Crucible clone and prepared runtime must be separate directories.")
    runtime.mkdir(parents=True, exist_ok=True)
    marker = runtime / "ready.json"
    marker.unlink(missing_ok=True)
    inputs = [source / "package.json", source / "package-lock.json", source / "tsconfig.json"]
    for folder in ("src", "assets"):
        inputs.extend(sorted(path for path in (source / folder).rglob("*") if path.is_file()))
    fingerprint = hashlib.sha256()
    # Build a clean content-addressed snapshot; no npm scripts or writes in the clone.
    for path in inputs:
        relative = path.relative_to(source)
        fingerprint.update(relative.as_posix().encode("utf-8"))
        fingerprint.update(file_digest(path).encode("ascii"))
    for folder in ("src", "assets", "dist"):
        path = (runtime / folder).resolve()
        if not path.is_relative_to(runtime.resolve()):
            raise ValueError("Runtime build path escaped the workspace cache.")
        if path.exists():
            shutil.rmtree(path)
    for name in ("package.json", "package-lock.json", "tsconfig.json"):
        shutil.copy2(source / name, runtime / name)
    for folder in ("src", "assets"):
        shutil.copytree(source / folder, runtime / folder)
    (runtime / "src/version.ts").write_text(f"export const VERSION = {json.dumps(package['version'])};\n", encoding="utf-8")
    npm_candidates = [node.parent / "node_modules/npm/bin/npm-cli.js", node.parent.parent / "lib/node_modules/npm/bin/npm-cli.js"]
    npm = next((path for path in npm_candidates if path.is_file()), None)
    if npm is None:
        raise RuntimeError("npm was not found beside Node.js. Install a Node.js distribution that includes npm.")
    env = os.environ.copy()
    env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
    env["npm_config_cache"] = str(root / ".cache/npm")
    subprocess.run([str(node), str(npm), "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=runtime, env=env, check=True)
    subprocess.run([str(node), str(runtime / "node_modules/typescript/bin/tsc")], cwd=runtime, env=env, check=True)
    # Fail setup immediately if the canvas addon is missing for this platform.
    subprocess.run([str(node), "-e", "require('./dist/index.js')"], cwd=runtime, env=env, check=True)
    fingerprint.update(file_digest(Path(__file__).with_name("renderers") / "crucible_bridge.cjs").encode("ascii"))
    write_json(marker, {"fingerprint": fingerprint.hexdigest(), "version": package["version"], "repository": str(source)})
    return runtime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.example.yaml"))
    parser.add_argument("--download-node", action="store_true", help="Install checksum-verified official Node.js 22 inside .cache (Windows x64).")
    args = parser.parse_args()
    path = prepare(load_config(args.config), Path.cwd(), args.download_node)
    print(f"Crucible runtime ready: {path}")


if __name__ == "__main__":
    main()
