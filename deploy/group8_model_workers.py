#!/usr/bin/env python3
"""Start/status the four group8 Qwen workers without touching shared GPU settings.

The retained group8-qwen-tp4 container supplies the existing read-only driver
mounts. This command never stops an existing process. Use the saved rollback
procedure only after draining the loopback model router.
"""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import urllib.request

MODEL_ROOT = Path('/home/group8/work/qwen-text')
ALLOWED = {4:'0000:32:00.0',5:'0000:38:00.0',6:'0000:3b:00.0',7:'0000:3c:00.0'}

def inspect(name):
    return json.loads(subprocess.check_output(['docker','inspect',name]))[0]

def owned(data):
    if data['Config']['Labels'].get('owner') != 'group8' or data['HostConfig']['Privileged']:
        raise RuntimeError('Expected a non-privileged group8-owned container')

def healthy(port):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f'http://127.0.0.1:{port}/health',timeout=2) as r:
            return r.status == 200
    except OSError:
        return False

def expected_devices(gpu):
    if gpu not in ALLOWED:
        raise ValueError('Only physical GPUs 4, 5, 6, 7 are allowed')
    card=Path(f'/dev/dri/by-path/pci-{ALLOWED[gpu]}-card').resolve()
    render=Path(f'/dev/dri/by-path/pci-{ALLOWED[gpu]}-render').resolve()
    if (Path('/sys/class/drm')/card.name/'device').resolve().name != ALLOWED[gpu]:
        raise RuntimeError('Physical PCI/DRM mapping changed')
    devices={f'/dev/mtgpu.{gpu}',str(card),str(render)}
    if not all(Path(v).exists() for v in devices):
        raise RuntimeError('Expected GPU device nodes are missing')
    return devices

def verify(data,gpu):
    owned(data)
    if {d['PathOnHost'] for d in data['HostConfig']['Devices']} != expected_devices(gpu):
        raise RuntimeError('Unexpected GPU devices exposed to worker')
    env=data['Config']['Env']
    for item in [f'MTHREADS_VISIBLE_DEVICES={gpu}','MUSA_VISIBLE_DEVICES=0','CUDA_VISIBLE_DEVICES=0']:
        if item not in env:raise RuntimeError('Worker GPU visibility is not isolated')
    ports=data['HostConfig']['PortBindings'].get('8000/tcp',[])
    if ports != [{'HostIp':'127.0.0.1','HostPort':str(18050+gpu)}]:
        raise RuntimeError('Expected loopback-only worker endpoint')

def create(gpu):
    name=f'group8-qwen-tp1-gpu{gpu}-perf'
    if subprocess.run(['docker','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
        base=inspect('group8-qwen-tp4');owned(base)
        args=['docker','create','--name',name,'--label','owner=group8','--label',f'physical-gpus={gpu}','--label','purpose=forecastlab-model-worker','--shm-size','16g','--ulimit','memlock=-1:-1','--cap-add','IPC_LOCK','-p',f'127.0.0.1:{18050+gpu}:8000','-e',f'MTHREADS_VISIBLE_DEVICES={gpu}','-e','MUSA_VISIBLE_DEVICES=0','-e','CUDA_VISIBLE_DEVICES=0','-e','MTHREADS_DRIVER_CAPABILITIES=compute,utility','--entrypoint','/bin/bash']
        for device in sorted(expected_devices(gpu)):
            args += ['--device',f'{device}:{device}:rwm']
        for mount in base['Mounts']:
            if mount['Source']==str(MODEL_ROOT):
                args += ['-v',f'{MODEL_ROOT}:/workspace/group8']
            else:
                if mount['RW']:raise RuntimeError('Unexpected writable driver mount')
                args += ['--mount',f'type=bind,src={mount["Source"]},dst={mount["Destination"]},readonly']
        args += [base['Config']['Image'],'-lc','sleep infinity']
        subprocess.run(args,check=True,stdout=subprocess.DEVNULL)
    data=inspect(name);verify(data,gpu)
    return name,data

def start(gpu):
    name,data=create(gpu)
    if data['State']['Running'] and healthy(18050+gpu):return
    if data['State']['Running']:
        processes=subprocess.check_output(['docker','top',name,'-eo','comm']).decode().lower()
        if any(v in processes for v in ['vllm','python']):
            print(f'{name}: model process exists; awaiting health, no duplicate started')
            return
    # Do not allocate on a card already occupied by another process/container.
    usage=subprocess.check_output(['mthreads-gmi','-i',str(gpu)]).decode()
    match=re.search(r'(\d+)MiB\((\d+)MiB\)',usage)
    if not match or int(match.group(1)) != 0:
        raise RuntimeError(f'Physical GPU {gpu} is occupied or memory usage is unknown')
    if not data['State']['Running']:
        subprocess.run(['docker','start',name],check=True,stdout=subprocess.DEVNULL)
    command=['vllm','serve','/workspace/group8/models/Qwen3-8B','--served-model-name','qwen3-8b','--host','0.0.0.0','--port','8000','--tensor-parallel-size','1','--dtype','bfloat16','--max-model-len','16384','--block-size','32','--attention-backend','FLASH_ATTN','--gpu-memory-utilization','0.5','--max-num-seqs','8','--max-num-batched-tokens','4096','--default-chat-template-kwargs','{"enable_thinking":true}','--reasoning-parser','qwen3']
    log=f'/workspace/group8/logs/inference-tp1-gpu{gpu}-perf.log'
    shell='exec '+shlex.join(command)+' > '+shlex.quote(log)+' 2>&1'
    subprocess.run(['docker','exec','-d','-e','VLLM_DISABLE_COMPILE_CACHE=1',name,'bash','-lc',shell],check=True)

def status(gpu):
    name=f'group8-qwen-tp1-gpu{gpu}-perf';data=inspect(name);verify(data,gpu)
    return {'container':name,'physical_gpu':gpu,'pci':ALLOWED[gpu],'logical_gpu':0,'endpoint':f'http://127.0.0.1:{18050+gpu}','running':data['State']['Running'],'healthy':healthy(18050+gpu)}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['start','status'])
    parser.add_argument('--gpus',type=int,nargs='+',choices=sorted(ALLOWED),default=sorted(ALLOWED))
    args=parser.parse_args()
    for gpu in dict.fromkeys(args.gpus):
        if args.action=='start':start(gpu)
        print(json.dumps(status(gpu)),flush=True)

if __name__=='__main__':main()
