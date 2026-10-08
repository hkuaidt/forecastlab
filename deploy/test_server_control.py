"""CPU-only smoke tests; all servers use temporary ports, records and data."""
from pathlib import Path
import os
import socket
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "deploy" / "server-control.sh"


def run(env, action, expected=0):
    result = subprocess.run(["bash", str(CONTROL), action], env=env, capture_output=True,
                            text=True, timeout=40)
    assert result.returncode == expected, (action, result.returncode, result.stdout, result.stderr)
    return result


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def main():
    old_record = ROOT.parent / "forecastlab-server.pid"
    old_bytes = old_record.read_bytes() if old_record.exists() else None
    with tempfile.TemporaryDirectory(prefix="forecastlab-control-") as directory:
        scratch = Path(directory)
        env = {**os.environ, "FORECASTLAB_DATA_DIR": str(scratch / "data"),
               "FORECASTLAB_PIDFILE": str(scratch / "server.pid"),
               "FORECASTLAB_LOGFILE": str(scratch / "server.log"),
               "FORECASTLAB_PORT": str(free_port()),
               "QWEN_API_KEY": "", "DEEPSEEK_API_KEY": "",
               "BRAVE_SEARCH_API_KEY": "", "TAVILY_API_KEY": ""}
        pidfile = Path(env["FORECASTLAB_PIDFILE"])
        try:
            run(env, "status", 1)
            assert "started" in run(env, "start").stdout
            pid = int(pidfile.read_text())
            assert '"ok": true' in run(env, "status").stdout
            assert "already running" in run(env, "start").stdout
            assert int(pidfile.read_text()) == pid
            # A PID belonging to the same checkout but a different requested
            # port is still not this invocation's server.
            wrong_port = {**env, "FORECASTLAB_PORT": str(free_port())}
            run(wrong_port, "stop", 1)
            run(env, "status")
            # Only the temporary CPU API/data are used. An unowned queued record
            # must be cancelled through HTTP before the controller sends TERM.
            import sys
            sys.path.insert(0, str(ROOT / "backend"))
            from app.demo import DEMO_QUESTION
            from app.schemas import RunRecord
            from app.storage import RunStore
            fixture_store = RunStore(scratch / "data")
            fixture_store.save(RunRecord(run_id="shutdown_queued", question=DEMO_QUESTION,
                                         evidence_mode="demo", demo=True, model="fixture"))
            run(env, "stop")
            assert fixture_store.get("shutdown_queued").status == "cancelled"
            assert not pidfile.exists()
            run(env, "status", 1)
            run(env, "stop")
            print("PASS start / status / duplicate start / port identity / stop")

            # Port occupancy must fail before replacing even a stale PID record.
            with socket.socket() as occupied:
                occupied.bind(("127.0.0.1", 0))
                occupied.listen()
                blocked = {**env, "FORECASTLAB_PORT": str(occupied.getsockname()[1])}
                sentinel = "999999999\n"
                pidfile.write_text(sentinel)
                result = run(blocked, "start", 1)
                assert "unavailable" in result.stderr
                assert pidfile.read_text() == sentinel
                assert "started" not in result.stdout
            print("PASS occupied port preserves PID record")

            # The smoke-test process itself is foreign to the server controller.
            sentinel = f"{os.getpid()}\n"
            pidfile.write_text(sentinel)
            run(env, "start", 1)
            run(env, "stop", 1)
            assert pidfile.read_text() == sentinel
            print("PASS foreign live PID is neither replaced nor signalled")

            # App initialization failure must not report success or publish PID.
            sentinel = "999999999\n"
            pidfile.write_text(sentinel)
            invalid = {**env, "FORECASTLAB_MODEL_TEMPERATURE": "invalid"}
            result = run(invalid, "start", 1)
            assert "failed to start" in result.stderr
            assert pidfile.read_text() == sentinel
            assert "started" not in result.stdout
            with socket.socket() as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", int(env["FORECASTLAB_PORT"])))
            print("PASS failed app startup preserves PID record")
            pidfile.unlink()
        finally:
            # stop's UID/cwd/argv guards also protect the foreign-PID test case.
            subprocess.run(["bash", str(CONTROL), "stop"], env=env, capture_output=True, timeout=20)
    assert (old_record.read_bytes() if old_record.exists() else None) == old_bytes
    print("PASS original shared PID record unchanged; smoke tests complete")


if __name__ == "__main__":
    main()
