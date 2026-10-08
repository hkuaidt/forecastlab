#!/usr/bin/env python3
"""Account-local supervision; never sends generation requests or changes host settings."""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import pwd
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request

import group8_model_workers as workers

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.forecastlab'
MAINTENANCE = STATE / 'maintenance'
STOP = threading.Event()


def require_owner():
    if pwd.getpwuid(os.getuid()).pw_name != 'group8':
        raise RuntimeError('Only the group8 account may manage these services')


def probe(url: str, *, required_ok=False) -> bool:
    """A responding router with 503 is alive; unavailable workers are separate."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            response = opener.open(url, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            data = json.load(response)
        return isinstance(data, dict) and (not required_ok or data.get('ok') is True)
    except (OSError, ValueError):
        return False


def command(args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=70)
    if result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} {args[-1]} exited {result.returncode}')


@dataclass
class RecoveryPolicy:
    grace: float
    backoff: float = 30
    failed_since: float | None = None
    next_recovery: float = 0

    def due(self, healthy: bool, now: float) -> bool:
        if healthy:
            self.failed_since = None
            return False
        if self.failed_since is None:
            self.failed_since = now
        if now - self.failed_since < self.grace or now < self.next_recovery:
            return False
        self.next_recovery = now + self.backoff
        return True


class Supervisor:
    def __init__(self):
        self.policies = {f'gpu{gpu}': RecoveryPolicy(180, 180) for gpu in workers.ALLOWED}
        self.policies.update({name: RecoveryPolicy(15, 30) for name in ('router', 'api')})
        self.logger = logging.getLogger('forecastlab-supervisor')

    def worker(self, gpu):
        # Health is not enough: verify every container's explicit physical mapping.
        name = f'group8-qwen-tp1-gpu{gpu}-perf'
        try:
            data = workers.inspect(name)
        except subprocess.CalledProcessError:
            # Initial provisioning/start still enforces ownership and zero memory.
            workers.start(gpu)
            return False
        workers.verify(data, gpu)
        healthy = data['State']['Running'] and workers.healthy(18050+gpu)
        if self.policies[f'gpu{gpu}'].due(healthy, time.monotonic()):
            self.logger.warning('recovering verified worker physical GPU %s', gpu)
            workers.recover(gpu)
        return healthy

    def service(self, name, url, required_ok):
        healthy = probe(url, required_ok=required_ok)
        if self.policies[name].due(healthy, time.monotonic()):
            control = ROOT / 'deploy' / ('server-control.sh' if name == 'api' else 'model-router-control.sh')
            self.logger.warning('recovering owned %s process', name)
            command(['bash', str(control), 'stop'])
            command(['bash', str(control), 'start'])
        return healthy

    def tick(self):
        state = {'updated_at': time.time(), 'paused': MAINTENANCE.exists(), 'services': {}}
        if not state['paused']:
            jobs = [(f'gpu{gpu}', lambda gpu=gpu: self.worker(gpu)) for gpu in workers.ALLOWED]
            jobs += [('router', lambda: self.service('router', 'http://127.0.0.1:18048/health', False)),
                     ('api', lambda: self.service('api', 'http://127.0.0.1:18765/api/live', True))]
            for name, work in jobs:
                if MAINTENANCE.exists() or STOP.is_set():
                    break
                try:
                    state['services'][name] = {'healthy': work()}
                except Exception as exc:
                    # No raw docker metadata, HTTP bodies, or environment in logs.
                    state['services'][name] = {'healthy': False, 'error_type': type(exc).__name__}
                    self.logger.error('%s supervision failed: %s', name, type(exc).__name__)
        else:
            # A maintenance interval is not a period of unhealthy service.
            for policy in self.policies.values():
                policy.failed_since = None
        pending = STATE / 'supervisor-state.tmp'
        pending.write_text(json.dumps(state, indent=2))
        pending.replace(STATE / 'supervisor-state.json')
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run', 'once', 'pause', 'resume', 'status'])
    args = parser.parse_args()
    require_owner()
    STATE.mkdir(mode=0o700, exist_ok=True)
    if args.action == 'pause':
        MAINTENANCE.touch(mode=0o600)
        print('Supervision paused; existing API/router/workers remain running.')
        return
    if args.action == 'resume':
        MAINTENANCE.unlink(missing_ok=True)
        print('Supervision resumed.')
        return
    if args.action == 'status':
        path = STATE / 'supervisor-state.json'
        print(path.read_text() if path.exists() else '{"status":"not_started"}')
        return
    with (STATE / 'supervisor.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        handler = RotatingFileHandler(STATE / 'supervisor.log', maxBytes=2_000_000, backupCount=3)
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logging.getLogger('forecastlab-supervisor').addHandler(handler)
        logging.getLogger('forecastlab-supervisor').setLevel(logging.INFO)
        signal.signal(signal.SIGTERM, lambda *_: STOP.set())
        signal.signal(signal.SIGINT, lambda *_: STOP.set())
        supervisor = Supervisor()
        while not STOP.is_set():
            supervisor.tick()
            if args.action == 'once':
                break
            STOP.wait(10)


if __name__ == '__main__':
    main()
