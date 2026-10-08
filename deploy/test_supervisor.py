"""Supervisor recovery uses mocked CPU probes/commands, never GPU computation."""
import io
import subprocess
import urllib.error

import pytest
import supervisor
import group8_model_workers as workers


def worker_data(gpu=4):
    return {'Config': {'Labels': {'owner':'group8'}, 'Env': [f'MTHREADS_VISIBLE_DEVICES={gpu}', 'MUSA_VISIBLE_DEVICES=0','CUDA_VISIBLE_DEVICES=0']},
            'HostConfig': {'Privileged':False,'Devices':[{'PathOnHost':'/dev/fixture'}],
                           'PortBindings':{'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':str(18050+gpu)}]}},
            'State':{'Running':True}}


def test_recovery_policy_requires_sustained_failure_and_backoff():
    policy=supervisor.RecoveryPolicy(180,180)
    assert not policy.due(False,0)
    assert not policy.due(False,179)
    assert policy.due(False,180)
    assert not policy.due(False,181)
    assert not policy.due(True,200)
    assert not policy.due(False,201)
    assert policy.due(False,381)


def test_maintenance_skips_every_probe_and_recovery(tmp_path,monkeypatch):
    monkeypatch.setattr(supervisor,'STATE',tmp_path)
    monkeypatch.setattr(supervisor,'MAINTENANCE',tmp_path/'maintenance')
    supervisor.MAINTENANCE.touch()
    runner=supervisor.Supervisor()
    monkeypatch.setattr(runner,'worker',lambda *_:pytest.fail('GPU work during maintenance'))
    monkeypatch.setattr(runner,'service',lambda *_:pytest.fail('service work during maintenance'))
    assert runner.tick()['paused']


def test_degraded_router_http_503_is_alive(monkeypatch):
    class Opener:
        def open(self,*a,**kw):
            raise urllib.error.HTTPError('http://127.0.0.1',503,'unavailable',{},io.BytesIO(b'{"ok":false,"workers":[]}'))
    monkeypatch.setattr(supervisor.urllib.request,'build_opener',lambda *_:Opener())
    assert supervisor.probe('http://127.0.0.1/health')
    assert not supervisor.probe('http://127.0.0.1/health',required_ok=True)


def test_healthy_services_are_adopted_without_restart(monkeypatch):
    monkeypatch.setattr(supervisor,'probe',lambda *a,**kw:True)
    monkeypatch.setattr(supervisor,'command',lambda *_:pytest.fail('unexpected restart'))
    runner=supervisor.Supervisor()
    assert runner.service('api','http://127.0.0.1/api/live',True)
    assert runner.service('router','http://127.0.0.1/health',False)


def test_persistent_api_failure_restarts_only_its_controller(monkeypatch):
    now=[0];calls=[]
    monkeypatch.setattr(supervisor.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(supervisor,'probe',lambda *a,**kw:False)
    monkeypatch.setattr(supervisor,'command',lambda args:calls.append(args))
    runner=supervisor.Supervisor()
    runner.service('api','http://127.0.0.1/api/live',True)
    assert not calls
    now[0]=15
    runner.service('api','http://127.0.0.1/api/live',True)
    assert [args[-1] for args in calls]==['stop','start']
    assert all(args[0]=='bash' and args[1].endswith('/server-control.sh') for args in calls)


def test_worker_recovery_launches_model_not_only_sleep_container(monkeypatch):
    commands=[];started=[]
    monkeypatch.setattr(workers,'inspect',lambda _:worker_data())
    monkeypatch.setattr(workers,'expected_devices',lambda gpu:{'/dev/fixture'})
    monkeypatch.setattr(workers,'healthy',lambda _:False)
    monkeypatch.setattr(workers.subprocess,'run',lambda args,**kw:commands.append(args))
    monkeypatch.setattr(workers,'start',lambda gpu:started.append(gpu))
    workers.recover(4)
    assert commands==[['docker','stop','--time','20','group8-qwen-tp1-gpu4-perf']]
    assert started==[4]


def test_worker_recovery_rejects_foreign_owner_before_stop(monkeypatch):
    data=worker_data();data['Config']['Labels']['owner']='someone-else'
    monkeypatch.setattr(workers,'inspect',lambda _:data)
    monkeypatch.setattr(workers.subprocess,'run',lambda *a,**kw:pytest.fail('foreign stop'))
    with pytest.raises(RuntimeError,match='group8-owned'):
        workers.recover(4)


@pytest.mark.parametrize('gpu',[0,1,2,3,8])
def test_disallowed_physical_devices_are_rejected(gpu):
    with pytest.raises(ValueError,match='4, 5, 6, 7'):
        workers.expected_devices(gpu)


def test_duplicate_visibility_cannot_smuggle_all_devices(monkeypatch):
    data=worker_data();data['Config']['Env'].append('MTHREADS_VISIBLE_DEVICES=all')
    monkeypatch.setattr(workers,'expected_devices',lambda gpu:{'/dev/fixture'})
    with pytest.raises(RuntimeError,match='visibility'):
        workers.verify(data,4)


def test_controller_recovery_works_with_non_executable_script(tmp_path, monkeypatch):
    """SCP/checkouts may lose the executable bit; bash remains the contract."""
    deploy=tmp_path/'deploy';deploy.mkdir()
    control=deploy/'server-control.sh'
    control.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$1" >> calls.txt\n')
    control.chmod(0o644)
    monkeypatch.setattr(supervisor,'ROOT',tmp_path)
    monkeypatch.setattr(supervisor,'probe',lambda *a,**kw:False)
    now=[0]
    monkeypatch.setattr(supervisor.time,'monotonic',lambda:now[0])
    runner=supervisor.Supervisor()
    runner.service('api','http://127.0.0.1/api/live',True)
    now[0]=15
    runner.service('api','http://127.0.0.1/api/live',True)
    assert (tmp_path/'calls.txt').read_text().splitlines()==['stop','start']
