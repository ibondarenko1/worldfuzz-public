"""Render the documented Nebius Jobs REST request without sending it."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re


def render(image, project, subnet, input_bytes):
    if not re.fullmatch(r'[a-z0-9./:_-]+@sha256:[0-9a-f]{64}',image):
        raise ValueError('Use a built image digest, not a mutable tag')
    for value in (project,subnet):
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,128}',value):raise ValueError('Actual project/subnet ID required')
    if len(input_bytes)>65536:raise ValueError('Nebius injected file limit is 64 KiB')
    return {'metadata':{'parentId':project,'name':'worldfuzz-physical-ai-nav-20260913-001'},
        'spec':{'image':image,'containerCommand':'timeout',
            'args':'--signal=TERM --kill-after=10s 300s python /opt/recipe/competition.py execute --input /input/approved-input.json --workspace /tmp/worldfuzz-job',
            'workingDir':'/opt/recipe','timeout':'3600s','platform':'cpu-d3','preset':'4vcpu-16gb',
            'disk':{'type':'NETWORK_SSD','sizeBytes':'107374182400'},'shmSizeBytes':'67108864',
            'subnetId':subnet,'preemptible':False,
            'environmentVariables':[{'name':'WORLDFUZZ_APPROVED_INPUT_SHA256','value':hashlib.sha256(input_bytes).hexdigest()},
                                    {'name':'OMP_NUM_THREADS','value':'1'},{'name':'OPENBLAS_NUM_THREADS','value':'1'}],
            'injectedFiles':[{'containerPath':'/input/approved-input.json','content':base64.b64encode(input_bytes).decode()}]}}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('image','project','subnet','input','out'):p.add_argument('--'+name,required=True)
    a=p.parse_args();result=render(a.image,a.project,a.subnet,Path(a.input).read_bytes())
    with Path(a.out).open('x',encoding='utf-8',newline='\n') as f:json.dump(result,f,indent=2);f.write('\n')
