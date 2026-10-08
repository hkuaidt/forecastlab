from pathlib import Path
import os
import socket
import subprocess
import sys
import time
import urllib.request
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture(scope="session")
def app_url(tmp_path_factory):
    folder = tmp_path_factory.mktemp("browser-data")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    env = dict(os.environ)
    for key in ("QWEN_API_KEY", "DEEPSEEK_API_KEY", "TAVILY_API_KEY"):
        env[key] = ""
    env["FORECASTLAB_BROWSER_TEST_DATA"] = str(folder)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "backend"), str(ROOT / "frontend/tests")])
    log = (folder / "server.log").open("w")
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "serve_test_app:make_app", "--factory",
        "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env, stdout=log, stderr=log)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError((folder / "server.log").read_text())
            try:
                urllib.request.urlopen(url + "/api/health", timeout=1).close(); break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError("Test backend did not start")
        yield url
    finally:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        log.close()

@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.getenv("FORECASTLAB_BROWSER_EXECUTABLE") or None)
        yield browser
        browser.close()

@pytest.fixture
def page(browser, request):
    context = browser.new_context(viewport={"width": 1365, "height": 1000})
    page = context.new_page(); page.set_default_timeout(5000)
    yield page
    directory = ROOT / "experiment/validation/screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(directory / (request.node.name + ".png")), full_page=True)
    context.close()
