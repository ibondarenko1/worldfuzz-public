"""Local, versioned execution protocol and enforced per-process resource limits.

Installed adapters are trusted code. This is a resource boundary, not a network
or filesystem sandbox. Windows uses a one-process Job Object; Linux uses rlimits.
"""
from copy import deepcopy
import ctypes
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import uuid

from .contracts import Fault

VERSION = '1.0.0'
_job = None


def environment():
    return {'python': sys.version, 'executable_sha256': hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            'platform': sys.platform, 'machine': platform.machine(),
            'packages': sorted([d.metadata['Name'], d.version] for d in metadata.distributions())}


def install_limits(limits):
    """Called only inside the isolated worker, before any adapter invocation."""
    global _job
    if os.name == 'nt':
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', w.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                        ('processes', w.DWORD), ('affinity', ctypes.c_size_t),
                        ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_ulonglong * 6),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.GetCurrentProcess.restype = w.HANDLE
        kernel.CloseHandle.argtypes = [w.HANDLE]
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        info = Extended()
        info.basic.flags = 0x8 | 0x4 | 0x200 | 0x2000
        info.basic.processes = 1
        info.basic.job_time = max(1, int(limits['cpu_seconds'] * 10_000_000))
        info.job_memory = limits['memory_bytes']
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.WinError(ctypes.get_last_error()); kernel.CloseHandle(job); raise error
        if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
            error = ctypes.WinError(ctypes.get_last_error()); kernel.CloseHandle(job); raise error
        # Keep the only job handle for the life of this worker. Closing it kills it.
        _job = job
        mechanism = 'windows_job_object'
        memory_kind = 'job_committed_bytes'
    elif sys.platform == 'linux':
        import resource
        cpu = max(1, math.ceil(limits['cpu_seconds']))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_AS, (limits['memory_bytes'], limits['memory_bytes']))
        mechanism = 'linux_rlimit_cpu_as'
        memory_kind = 'process_virtual_address_space'
    else:
        raise Fault('RUNNER_UNSUPPORTED', 'No enforced CPU/memory backend on this platform')
    return {'mechanism': mechanism, 'cpu_seconds': limits['cpu_seconds'],
            'memory_bytes': limits['memory_bytes'], 'memory_semantics': memory_kind,
            'wall_seconds': limits['wall_seconds'], 'wall_mechanism': 'independent_worker_watchdog',
            'artifact_bytes': limits['artifact_bytes'], 'artifact_mechanism': 'checked_atomic_collector',
            'scope': 'one trusted offline worker; not a filesystem/network sandbox'}


def capabilities():
    """Probe actual OS permission to attach limits in a short isolated process."""
    child = subprocess.Popen([sys.executable, '-m', 'worldfuzz.platform.runner', '--probe'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, _ = child.communicate(timeout=5)
        if child.returncode:
            raise Fault('RUNNER_UNSUPPORTED', 'OS resource-limit probe failed')
        result = json.loads(stdout)
    except (subprocess.TimeoutExpired, ValueError) as exc:
        raise Fault('RUNNER_UNSUPPORTED', 'OS resource-limit probe did not complete') from exc
    finally:
        if child.poll() is None: child.kill()
        child.wait()
    return {'runner_id': 'worldfuzz.local-process', 'version': VERSION,
            'supported': ['cpu_seconds', 'memory_bytes', 'wall_seconds', 'artifact_bytes'],
            'enforcement': result, 'probe_pid': child.pid,
            'unsupported': ['network_isolation', 'filesystem_isolation', 'devices', 'cloud', 'replay']}


def prepare(service, request, contract):
    caps = capabilities()
    wanted = request.get('required_runner_capabilities', [])
    if not set(wanted) <= set(caps['supported']):
        raise Fault('RUNNER_UNSUPPORTED', 'Mandatory runner capability is unavailable')
    limits = contract['resource_limits']
    if limits['cpu_seconds'] is None or limits['memory_bytes'] is None:
        raise Fault('RUNNER_UNSUPPORTED', 'Finite CPU and memory limits are required')
    return {'runner_id': caps['runner_id'], 'version': VERSION, 'environment': environment(),
            'supported': caps['supported'], 'limits': deepcopy(limits),
            'adapter': service.adapters.binding(request['profile']),
            'probe': caps['enforcement'], 'input_versions': deepcopy(contract['input_versions'])}


def check(service, plan):
    spec = plan.get('runner_contract')
    if not spec or spec['version'] != VERSION:
        raise Fault('RUNNER_UNSUPPORTED', 'Prepare a plan with the current runner protocol')
    if spec['environment'] != environment() or spec['adapter'] != service.adapters.binding(plan['request']['profile']):
        raise Fault('PLAN_CHANGED', 'Pinned runner environment or adapter changed')
    if spec['limits'] != plan['contract_plan']['resource_limits'] or spec['input_versions'] != plan['contract_plan']['input_versions']:
        raise Fault('PLAN_CHANGED', 'Runner resources or source versions changed')
    return spec


def worker_environment(service):
    env = os.environ.copy()
    for name in ('WORLDFUZZ_WORKER_AUTHORITY','WORLDFUZZ_WORKER_SESSION'):
        env.pop(name,None)
    if service.identity:
        env['WORLDFUZZ_WORKER_AUTHORITY']=str(service.identity.authority.path)
        env['WORLDFUZZ_WORKER_SESSION']=service.identity.key
    if service.entity_catalog is not None:
        env['WORLDFUZZ_WORKER_POSTGRES_DSN'] = service.entity_catalog.repository._dsn
        env['WORLDFUZZ_WORKER_BLOB_ROOT'] = str(service.entity_catalog.blobs.root)
    return env


def spawn(service, run_id, *, wait=False):
    launch_id=uuid.uuid4().hex
    child = subprocess.Popen([sys.executable, '-m', 'worldfuzz.platform.worker', str(service.root),
                              str(service.corpus.path.resolve()), run_id, launch_id],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, env=worker_environment(service))
    service.worker_processes.append(child)
    with service.corpus.connect() as db:
        db.execute('INSERT INTO wf_worker_processes(run_id,pid,started,exit_code) VALUES (?,?,?,NULL)',
                   (run_id, child.pid, service.clock()))
    def reap():
        try:
            plan = service._plan(service._run_record(run_id, authorize=False)['plan_id'])
            try: code = child.wait(timeout=plan['request']['max_seconds'] + 10)
            except subprocess.TimeoutExpired:
                child.kill(); code = child.wait()
            with service.corpus.connect() as db:
                db.execute('UPDATE wf_worker_processes SET exit_code=? WHERE pid=? AND run_id=?',
                           (code, child.pid, run_id))
            service.jobs.reconcile(run_id,exit_code=code,expected_launch_id=launch_id)
        finally:
            if child.poll() is None: child.kill(); child.wait()
    if wait: reap()
    else: threading.Thread(target=reap, daemon=True).start()
    return child


def watchdog(seconds, cpu_seconds=None):
    stop = threading.Event()
    def watch():
        deadline=time.monotonic()+max(.01,seconds)
        cpu_start=time.process_time()
        while not stop.wait(.05):
            if time.monotonic()>=deadline or (cpu_seconds is not None and time.process_time()-cpu_start>=cpu_seconds):
                os._exit(124)
    threading.Thread(target=watch, daemon=True).start()
    return stop


if __name__ == '__main__':
    if sys.argv[1:] != ['--probe']: raise SystemExit(2)
    print(json.dumps(install_limits({'cpu_seconds': 2, 'memory_bytes': 256*1024*1024,
                                    'wall_seconds': 5, 'artifact_bytes': 1024})), flush=True)
