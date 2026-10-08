#!/usr/bin/env python3
"""Best-effort cancellation before a guarded API process is terminated."""
import argparse
import json
import os
from pathlib import Path
import re
import time
import urllib.request


def owns_listener(pid: int, port: int) -> bool:
    try:
        process = Path('/proc') / str(pid)
        if process.stat().st_uid != os.getuid():
            return False
        sockets = {fd.readlink().name for fd in (process/'fd').iterdir()}
        address = f'0100007F:{port:04X}'
        rows = [line.split() for line in (process/'net/tcp').read_text().splitlines()[1:]]
        return any(row[1] == address and row[3] == '0A' and f'socket:[{row[9]}]' in sockets for row in rows)
    except (OSError, ValueError, IndexError):
        return False


def cancel_active_runs(pid: int, port: int, *, budget: float = 4, opener=None,
                       clock=time.monotonic, sleep=time.sleep) -> dict:
    if not owns_listener(pid, port):
        return {'cancel_requested': 0, 'drained': False, 'reason': 'listener_not_owned'}
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = clock()+budget
    requested = set()
    base = f'http://127.0.0.1:{port}'

    def request(path, method='GET'):
        remaining = deadline-clock()
        if remaining <= 0:
            raise TimeoutError('cancellation drain deadline')
        req = urllib.request.Request(base+path, method=method, data=b'' if method=='POST' else None)
        with opener.open(req, timeout=min(.75, remaining)) as response:
            return json.load(response)

    try:
        while clock() < deadline:
            rows = request('/api/runs?summary=true&limit=200')
            if not isinstance(rows, list):
                raise ValueError('Unexpected run directory')
            active = [row.get('run_id') for row in rows if row.get('status') in {'queued','running'}]
            active = [rid for rid in active if isinstance(rid,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}',rid)]
            if not active:
                return {'cancel_requested':len(requested),'drained':True}
            for run_id in active:
                if run_id not in requested:
                    request(f'/api/runs/{run_id}/cancel','POST')
                    requested.add(run_id)
            sleep(min(.1,max(0,deadline-clock())))
    except (OSError, ValueError, TypeError):
        return {'cancel_requested':len(requested),'drained':False,'reason':'http_unavailable'}
    return {'cancel_requested':len(requested),'drained':False,'reason':'drain_timeout'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pid',type=int)
    parser.add_argument('port',type=int)
    args=parser.parse_args()
    if args.pid<=0 or not 1<=args.port<=65535:
        parser.error('Invalid pid/port')
    print(json.dumps(cancel_active_runs(args.pid,args.port)))


if __name__=='__main__':
    main()
