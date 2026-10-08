"""Package only committed, allowed files; never scoop up a developer workspace."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import zipfile

BASE = "bb4d339a9fe94c016e751998943fc0f366b6ea0a"
DENIED_PARTS = {".git", ".venv", "venv", "node_modules", "data", "__pycache__", ".pytest_cache", ".superpowers", "dist", ".ssh", ".aws"}
DENIED_NAMES = {"auth.json", "credentials.json", "credentials", "id_rsa", "id_ed25519", ".DS_Store"}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def allowed_path(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return False
    if any(part in DENIED_PARTS for part in path.parts):
        return False
    if path.name in DENIED_NAMES or path.suffix.lower() in {".pem", ".key", ".p12", ".sqlite3"}:
        return False
    if path.name.startswith(".env") and path.name != ".env.example":
        return False
    if "screenshots" in path.parts:
        return False
    return True


def _tree(repo: Path, revision: str):
    files = {}
    for raw in _git(repo, "ls-tree", "-rz", "--full-tree", revision).split(b"\0"):
        if not raw:
            continue
        metadata, name_bytes = raw.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        name = name_bytes.decode("utf-8")
        if not allowed_path(name):
            continue
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"Refusing symlink/submodule or unsupported Git object: {name}")
        files[name] = (mode, oid)
    return files


def package_source(repo: Path, output: Path, *, base: str = BASE) -> dict:
    repo, output = Path(repo).resolve(), Path(output).resolve()
    if _git(repo, "diff", "--name-only", "HEAD").strip():
        raise ValueError("Commit tracked changes before packaging; untracked/runtime files are never included")
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    base = _git(repo, "rev-parse", "--verify", base + "^{commit}").decode().strip()
    current, previous = _tree(repo, head), _tree(repo, base)
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / "forecastlab-source.zip"
    patch_path = output / "forecastlab.patch"
    manifest = {}
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, (mode, oid) in sorted(current.items()):
            content = _git(repo, "cat-file", "blob", oid)
            manifest[name] = hashlib.sha256(content).hexdigest()
            entry = zipfile.ZipInfo("forecastlab/" + name, date_time=(2026, 9, 30, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (int(mode, 8) & 0o777) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, content)
    patch_paths = sorted(set(current) | set(previous))
    patch = (_git(repo, "diff", "--binary", "--no-ext-diff", base, head, "--", *[":(literal)" + name for name in patch_paths]) if patch_paths else b"")
    patch_path.write_bytes(patch)
    result = {"base": base, "head": head, "source_zip": str(archive_path), "patch": str(patch_path), "files": manifest,
              "artifacts": {archive_path.name: file_sha256(archive_path), patch_path.name: file_sha256(patch_path)},
              "exclusions": sorted(DENIED_PARTS | DENIED_NAMES),
              "policy": "Committed regular files only; runtime, credentials, raw screenshots and symlinks excluded. .env.example permitted."}
    (output / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    sums = [f"{digest}  {name}" for name, digest in result["artifacts"].items()]
    sums.append(f"{file_sha256(output / 'manifest.json')}  manifest.json")
    (output / "SHA256SUMS").write_text("\n".join(sums)+"\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = package_source(args.repo, args.output, base=args.base)
    print(json.dumps({k: v for k, v in result.items() if k != "files"}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
