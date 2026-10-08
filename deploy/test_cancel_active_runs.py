"""CPU-only maintenance drain; production services are never stopped by tests."""
import io
import json
from pathlib import Path
import urllib.error
import cancel_active_runs as drain


class Clock:
    def __init__(self): self.value=0
    def now(self): return self.value
    def sleep(self, seconds): self.value+=seconds


def test_cancel_precedes_poll_and_only_targets_active_runs(monkeypatch):
    monkeypatch.setattr(drain,'owns_listener',lambda *args:True)
    calls=[];active={'queued_one','running_two'}
    class Opener:
        def open(self,request,timeout):
            calls.append((request.method,request.full_url))
            if request.method=='POST':
                active.remove(request.full_url.split('/')[-2]);payload={}
            else:
                payload=[{'run_id':r,'status':'running'} for r in sorted(active)]
                payload.append({'run_id':'done_three','status':'completed'})
            return io.BytesIO(json.dumps(payload).encode())
    clock=Clock()
    result=drain.cancel_active_runs(1,1234,opener=Opener(),clock=clock.now,sleep=clock.sleep)
    assert result=={'cancel_requested':2,'drained':True}
    assert [method for method,url in calls]==['GET','POST','POST','GET']
    assert all('done_three/cancel' not in url for method,url in calls)


def test_foreign_listener_is_not_contacted(monkeypatch):
    monkeypatch.setattr(drain,'owns_listener',lambda *args:False)
    assert drain.cancel_active_runs(1,1234,opener=object())['reason']=='listener_not_owned'


def test_http_failure_returns_for_termination_fallback(monkeypatch):
    monkeypatch.setattr(drain,'owns_listener',lambda *args:True)
    class Opener:
        def open(self,*args,**kwargs): raise urllib.error.URLError('offline')
    result=drain.cancel_active_runs(1,1234,opener=Opener())
    assert not result['drained'] and result['reason']=='http_unavailable'
    script=(Path(__file__).parent/'server-control.sh').read_text()
    stop=script[script.index('  stop)'):]
    assert stop.index('cancel_active_runs.py') < stop.index('kill "$pid"')
    assert '"$pid" "$port" || true' in stop
    assert 'timeout --signal=TERM 5' in stop


def test_drain_is_bounded_when_record_remains_running(monkeypatch):
    monkeypatch.setattr(drain,'owns_listener',lambda *args:True)
    clock=Clock();posts=[]
    class Opener:
        def open(self,request,timeout):
            assert timeout<=.75
            if request.method=='POST': posts.append(request.full_url)
            return io.BytesIO(json.dumps([{'run_id':'run_one','status':'running'}]).encode())
    result=drain.cancel_active_runs(1,1234,budget=4,opener=Opener(),clock=clock.now,sleep=clock.sleep)
    assert clock.value<=4
    assert result['reason']=='drain_timeout'
    assert len(posts)==1
