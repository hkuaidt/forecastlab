#!/usr/bin/env python3
"""Start/status the four group8 Qwen workers without touching shared GPU settings.

The checked deployment manifest preserves the read-only driver mounts even if
the original TP4 container has been deleted. CLI start/status do not stop healthy
processes; the supervisor alone invokes recovery after sustained failed probes.
"""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import urllib.request
import time

MODEL_ROOT = Path('/home/group8/work/qwen-text')
ALLOWED = {4:'0000:32:00.0',5:'0000:38:00.0',6:'0000:3b:00.0',7:'0000:3c:00.0'}

def inspect(name):
    return json.loads(subprocess.check_output(['docker','inspect',name],timeout=10))[0]

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
    for key,value in {'MTHREADS_VISIBLE_DEVICES':str(gpu),'MUSA_VISIBLE_DEVICES':'0','CUDA_VISIBLE_DEVICES':'0'}.items():
        entries=[item.partition('=')[2] for item in env if item.partition('=')[0]==key]
        if entries != [value]:raise RuntimeError('Worker GPU visibility is not isolated')
    ports=data['HostConfig']['PortBindings'].get('8000/tcp',[])
    if ports != [{'HostIp':'127.0.0.1','HostPort':str(18050+gpu)}]:
        raise RuntimeError('Expected loopback-only worker endpoint')

def runtime_template():
    data=json.loads((Path(__file__).parent/'group8-worker-runtime.json').read_text())
    image=data['image']
    if not image.startswith('registry.mthreads.com/presale/devtech/'):
        raise RuntimeError('Expected the reviewed MThreads image')
    mounts=[{'Source':str(MODEL_ROOT),'Destination':'/workspace/group8','RW':True}]
    for mount in data['driver_mounts']:
        source=mount['source'];destination=mount['destination']
        if source != destination or not (source.startswith('/usr/lib/') or source == '/usr/bin/mthreads-gmi'):
            raise RuntimeError('Only reviewed read-only driver mounts are allowed')
        if not Path(source).is_file():
            raise RuntimeError('A required driver library is missing')
        mounts.append({'Source':source,'Destination':destination,'RW':False})
    return {'Config':{'Image':image},'Mounts':mounts}


def create(gpu):
    name=f'group8-qwen-tp1-gpu{gpu}-perf'
    if subprocess.run(['docker','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10).returncode:
        base=runtime_template()
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
        subprocess.run(args,check=True,stdout=subprocess.DEVNULL,timeout=30)
    data=inspect(name);verify(data,gpu)
    return name,data

def start(gpu):
    name,data=create(gpu)
    if data['State']['Running'] and healthy(18050+gpu):return
    if data['State']['Running']:
        processes=subprocess.check_output(['docker','top',name,'-eo','comm'],timeout=10).decode().lower()
        if any(v in processes for v in ['vllm','python']):
            print(f'{name}: model process exists; awaiting health, no duplicate started')
            return
    # Do not allocate on a card already occupied by another process/container.
    usage=subprocess.check_output(['mthreads-gmi','-i',str(gpu)],timeout=10).decode()
    match=re.search(r'(\d+)MiB\((\d+)MiB\)',usage)
    if not match or int(match.group(1)) != 0:
        raise RuntimeError(f'Physical GPU {gpu} is occupied or memory usage is unknown')
    if not data['State']['Running']:
        subprocess.run(['docker','start',name],check=True,stdout=subprocess.DEVNULL,timeout=30)
    command=['vllm','serve','/workspace/group8/models/Qwen3-8B','--served-model-name','qwen3-8b','--host','0.0.0.0','--port','8000','--tensor-parallel-size','1','--dtype','bfloat16','--max-model-len','16384','--block-size','32','--attention-backend','FLASH_ATTN','--gpu-memory-utilization','0.5','--max-num-seqs','8','--max-num-batched-tokens','4096','--default-chat-template-kwargs','{"enable_thinking":true}','--reasoning-parser','qwen3']
    log=f'/workspace/group8/logs/inference-tp1-gpu{gpu}-perf.log'
    shell='exec '+shlex.join(command)+' >> '+shlex.quote(log)+' 2>&1'
    subprocess.run(['docker','exec','-d','-e','VLLM_DISABLE_COMPILE_CACHE=1',name,'bash','-lc',shell],check=True,timeout=15)

def recover(gpu):
    """Restart only a verified group8 worker, then launch its actual model process.

    Called by the supervisor after sustained failed health probes, never for a
    busy but healthy worker. Ownership and PCI mappings are checked before stop.
    """
    name=f'group8-qwen-tp1-gpu{gpu}-perf'
    data=inspect(name);verify(data,gpu)
    if healthy(18050+gpu):
        return
    if data['State']['Running']:
        subprocess.run(['docker','stop','--time','20',name],check=True,timeout=35,stdout=subprocess.DEVNULL)
    # GPU memory may take a moment to drain after our container exits. start()
    # still refuses if any process occupies this physical card.
    for attempt in range(10):
        try:
            start(gpu)
            return
        except RuntimeError as exc:
            if 'occupied or memory usage' not in str(exc) or attempt == 9:
                raise
            time.sleep(2)


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
