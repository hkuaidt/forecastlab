"""The deliverable must be a clean, reconstructable, credential-free Git snapshot."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import zipfile
import pytest

ROOT = Path(__file__).resolve().parents[2]


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


@pytest.fixture
def small_repo(tmp_path):
    repo = tmp_path / "sample"; repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@invalid.local")
    (repo / "README.md").write_text("baseline\n")
    (repo / ".env").write_text("NEVER_EXPORT=fixture-only\n")
    (repo / ".env.example").write_text("QWEN_API_KEY=\n")
    git(repo, "add", "."); git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD").decode().strip()
    (repo / "README.md").write_text("updated\n")
    (repo / "module.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "data").mkdir(); (repo / "data/private.json").write_text("private-runtime-data")
    git(repo, "add", "."); git(repo, "commit", "-qm", "feature")
    (repo / "untracked-secret.txt").write_text("do not package untracked content")
    return repo, base


def module():
    path = ROOT / "scripts/package_source.py"
    assert path.exists(), "safe packaging script not implemented"
    spec = importlib.util.spec_from_file_location("source_package", path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def test_package_excludes_runtime_and_credentials(tmp_path, small_repo):
    repo, base = small_repo
    result = module().package_source(repo, tmp_path / "out", base=base)
    with zipfile.ZipFile(result["source_zip"]) as archive:
        names = {name.removeprefix("forecastlab/") for name in archive.namelist()}
        assert ".env" not in names and "data/private.json" not in names
        assert "untracked-secret.txt" not in names
        assert ".env.example" in names
        assert not any(n.startswith(("data/", ".git/", "node_modules/", ".venv/")) for n in names)
    assert b"private-runtime-data" not in Path(result["patch"]).read_bytes()


def test_manifest_matches_members(tmp_path, small_repo):
    repo, base = small_repo
    result = module().package_source(repo, tmp_path / "out", base=base)
    with zipfile.ZipFile(result["source_zip"]) as archive:
        assert len(archive.namelist()) == len(result["files"])
        for name, digest in result["files"].items():
            assert hashlib.sha256(archive.read("forecastlab/"+name)).hexdigest() == digest
    assert result["head"] == git(repo, "rev-parse", "HEAD").decode().strip()


def test_patch_applies_to_clean_baseline(tmp_path, small_repo):
    repo, base = small_repo
    result = module().package_source(repo, tmp_path / "out", base=base)
    fresh = tmp_path / "fresh"
    subprocess.run(["git", "-c", "core.autocrlf=false", "clone", "--quiet", str(repo), str(fresh)], check=True, capture_output=True)
    git(fresh, "config", "core.autocrlf", "false")
    git(fresh, "checkout", "--quiet", base)
    check = subprocess.run(["git", "-C", str(fresh), "apply", "--check", result["patch"]], capture_output=True)
    assert check.returncode == 0, check.stderr.decode()
    git(fresh, "apply", result["patch"])
    for name, digest in result["files"].items():
        assert module().file_sha256(fresh / name) == digest
