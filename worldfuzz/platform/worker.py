"""Registered offline adapters. Called as a separate, bounded local process."""
import hashlib
import os
from pathlib import Path
import sys
import time

from .contracts import Fault
from .service import Service


def run(service, run_id, *, isolated=False, launch_id=None):
    if not isolated:
        from .runner import spawn
        spawn(service,run_id,wait=True)
        return
    records=[];artifacts=[];started=time.monotonic();meter=None;attempt=None
    record=service._run_record(run_id,authorize=False)
    if record['status']=='cancel_requested':
        service.finish(run_id,'cancelled',{'records':[]});return
    plan=service._plan(record['plan_id']);request=plan['request']
    from .runner import check as runner_check, install_limits, watchdog
    watchdog_stop=None;heartbeat_stop=None;heartbeat_thread=None
    def check():
        if meter is not None:meter.sample()
        service.checkpoint(run_id,{'completed_units':len(records),'elapsed_seconds':round(time.monotonic()-started,4)},
                                  {'records':records,'artifacts':artifacts})
    def artifact(name, data):
        check()
        artifacts.append(service.jobs.collect(run_id,name,data,plan['runner_contract']['limits']['artifact_bytes']))
        if meter is not None:meter.artifact(len(data))
        check()
    try:
        runner_check(service,plan)
        if plan['code']!=service._code() or plan['python']!=sys.version.split()[0] or plan['snapshot']!=service._snapshot(request):
            raise Fault('PLAN_CHANGED','Pinned inputs or code changed before execution')
        # This is the last gate before the worker changes state or invokes an
        # adapter. It observes revocation and catalog changes after queuing.
        service.authorization.authorize_operation(plan,'execute')
        attempt=service.jobs.claim(run_id,launch_id=launch_id)
        if attempt is None:return
        enforcement=install_limits(attempt['limits'])
        watchdog_stop=watchdog(attempt['limits']['wall_seconds'],attempt['limits']['cpu_seconds'])
        from .budgets import Meter
        meter=Meter(service,run_id)
        heartbeat_stop,heartbeat_thread=service.jobs.heartbeat_thread(run_id)
        check()
        operation=request['operation']
        adapter=service.adapters.resolve(request['profile'],request['operation'])
        if operation=='replay_can_actions':meter.switch('replay')
        adapter.execute(service, plan, records, artifacts, check, artifact)
        meter.switch('reduction')
        meter.sample()  # No reduction is implemented; preserve an observed zero-duration phase.
        meter.switch('verification')
        from .verifier import verify
        verification=verify(service,run_id,plan,records,artifacts,check,accumulate=True)
        check()
        service.finish(run_id,'completed',{'records':records,'artifacts':artifacts,'operation':operation,
                       'runner':{'pid':os.getpid(),'contract_version':'1.0.0','enforcement':enforcement},
                       'verification':verification,
                       'elapsed_seconds':time.monotonic()-started,'input_snapshot':plan['snapshot']})
    except Exception as exc:
        code='BUDGET_EXCEEDED' if isinstance(exc,MemoryError) else exc.code if isinstance(exc,Fault) else type(exc).__name__
        state={'CANCELLED':'cancelled','BUDGET_EXCEEDED':'budget_exhausted','STOPPED':'incomplete'}.get(code,'failed')
        if code!='LEASE_LOST':
            try:
                if meter is not None:meter.sample()
                service.finish(run_id,state,{'records':records,'artifacts':artifacts,'elapsed_seconds':time.monotonic()-started},code)
            except Fault as lost:
                if lost.code not in ('LEASE_LOST','CANCELLED'):raise
    finally:
        if watchdog_stop:watchdog_stop.set()
        if heartbeat_stop:heartbeat_stop.set();heartbeat_thread.join(timeout=2)


if __name__=='__main__':
    catalog=None
    dsn=os.environ.get('WORLDFUZZ_WORKER_POSTGRES_DSN')
    blob_root=os.environ.get('WORLDFUZZ_WORKER_BLOB_ROOT')
    if dsn or blob_root:
        if not dsn or not blob_root:raise SystemExit('Incomplete external catalog worker configuration')
        from ..m1_catalog import LocalBlobStore,VersionedCatalog
        from ..storage import PostgreSQLRepository
        catalog=VersionedCatalog(PostgreSQLRepository(dsn),LocalBlobStore(blob_root))
    if os.environ.get('WORLDFUZZ_WORKER_AUTHORITY'):
        from .identity import IdentityStore
        service=IdentityStore(os.environ['WORLDFUZZ_WORKER_AUTHORITY']).worker_session(os.environ['WORLDFUZZ_WORKER_SESSION'])
        if service.root!=Path(sys.argv[1]).resolve() or service.corpus.path.resolve()!=Path(sys.argv[2]).resolve():
            raise SystemExit('Worker workspace mismatch')
        if catalog is not None: raise SystemExit('Organization external catalog requires an isolated workspace resolver')
    else:
        service=Service(sys.argv[1],sys.argv[2],launch=False,entity_catalog=catalog)
    run(service,sys.argv[3],
        isolated=True,launch_id=sys.argv[4] if len(sys.argv)>4 else None)
