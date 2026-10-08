"""Deployment tests may use loopback CPU servers, never Docker or GPU commands."""
import re
import subprocess
import pytest


@pytest.fixture(autouse=True)
def forbid_real_gpu_and_container_commands(monkeypatch):
    original_run = subprocess.run
    original_popen = subprocess.Popen
    def validate(args):
        text = args if isinstance(args, str) else ' '.join(str(item) for item in args)
        if re.search(r'(?<![\w-])(?:docker|mthreads-gmi|nvidia-smi)(?![\w-])', text):
            raise AssertionError('Real Docker/GPU commands are forbidden in deployment tests')
    def run(args, *a, **kw):
        validate(args)
        return original_run(args, *a, **kw)
    def popen(args, *a, **kw):
        validate(args)
        return original_popen(args, *a, **kw)
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(subprocess, 'Popen', popen)
