"""Local service, JSON CLI and MCP entrypoints. No credentials or inference."""
import argparse
import json
import sqlite3
import os
from pathlib import Path
from ..corpus import DEFAULT_STORE
from .service import Service
from .contracts import OPERATIONS, openapi, envelope, Fault, strict_object


def main(argv=None):
    parser=argparse.ArgumentParser(prog='worldfuzz platform')
    parser.add_argument('--root',type=Path,default=Path('artifacts/platform'))
    parser.add_argument('--store',type=Path,default=DEFAULT_STORE)
    parser.add_argument('--authority',type=Path,help='Organization registry; token is read from WORLDFUZZ_SESSION_TOKEN')
    parser.add_argument('--placement',type=Path,help='Versioned local or remote common-protocol configuration')
    sub=parser.add_subparsers(dest='command',required=True)
    call=sub.add_parser('call');call.add_argument('operation',choices=list(OPERATIONS));inputs=call.add_mutually_exclusive_group();inputs.add_argument('--json',default='{}');inputs.add_argument('--json-file',type=Path)
    run_goal=sub.add_parser('run',help='Run one typed workflow without JSON or identifier transfers')
    run_goal.add_argument('workflow',choices=['navigation','recorded-state','sensor-recording'])
    run_goal.add_argument('--key',required=True,help='Idempotency key; repeat it to resume the same logical run')
    run_goal.add_argument('--resource',help='Exact catalog resource ID or source key')
    run_goal.add_argument('--case-index',type=int,default=0,help='Zero-based Moving AI case index (0..8); N selects movingai:N')
    run_goal.add_argument('--algorithm',choices=['astar','dijkstra'],default='astar')
    run_goal.add_argument('--episode-id')
    run_goal.add_argument('--asset-id')
    run_goal.add_argument('--start-index',type=int,default=0)
    run_goal.add_argument('--frame-count',type=int,default=3)
    run_goal.add_argument('--max-messages',type=int,default=10)
    run_goal.add_argument('--max-seconds',type=int,default=20)
    run_goal.add_argument('--cpu-seconds',type=int,default=10)
    run_goal.add_argument('--memory-mib',type=int,default=256)
    run_goal.add_argument('--artifact-mib',type=int,default=8)
    run_goal.add_argument('--wait-seconds',type=float,default=20)
    run_goal.add_argument('--export',type=Path,help='New directory for summary, JUnit and exact JSON artifacts')
    http=sub.add_parser('serve');http.add_argument('--port',type=int,default=8766)
    http.add_argument('--docs-base-url',default=os.environ.get('WORLDFUZZ_DOCS_BASE_URL'),help='Canonical documentation base URL; does not publish or change the loopback listener')
    documentation=sub.add_parser('docs',help='Export ordinary HTML, generated API reference, sitemap and llms.txt; does not publish')
    documentation.add_argument('--out',type=Path,required=True,help='New output directory')
    documentation.add_argument('--base-url',default=os.environ.get('WORLDFUZZ_DOCS_BASE_URL'),help='Explicit HTTPS publication base or HTTP loopback preview URL')
    mcp=sub.add_parser('mcp');mcp.add_argument('--read-only',action='store_true')
    sub.add_parser('openapi')
    entities=sub.add_parser('entity');entities.add_argument('action',choices=['list','schema','validate']);entities.add_argument('--type');entities.add_argument('--json-file',type=Path)
    upgrade=sub.add_parser('catalog-upgrade');upgrade.add_argument('--destination',type=Path,required=True)
    backup=sub.add_parser('workspace-backup');backup.add_argument('--destination',type=Path,required=True);backup.add_argument('--max-mib',type=int,default=1024)
    restore=sub.add_parser('workspace-restore');restore.add_argument('--backup',type=Path,required=True);restore.add_argument('--destination',type=Path,required=True);restore.add_argument('--max-mib',type=int,default=1024)
    args=parser.parse_args(argv)
    if args.command=='openapi':print(json.dumps(openapi(),indent=2));return 0
    if args.command=='docs':
        from .documentation import export
        if not args.base_url: parser.error('Set --base-url or WORLDFUZZ_DOCS_BASE_URL; no publication domain is assumed')
        try:
            print(json.dumps(export(args.out,args.base_url),indent=2));return 0
        except (ValueError,OSError) as exc:parser.error(str(exc))
    if args.command=='catalog-upgrade':
        from ..storage import upgrade_copy
        try:
            print(json.dumps(envelope(upgrade_copy(args.store,args.destination))));return 0
        except (ValueError,OSError,sqlite3.Error) as exc:
            print(json.dumps(envelope(error=Fault('MIGRATION_FAILED',str(exc)))));return 1
    if args.command in ('workspace-backup','workspace-restore'):
        if args.authority or args.placement:parser.error('Use scoped call create_backup or restore_backup for organization workspaces')
        if args.command=='workspace-backup' and args.store.exists():
            from contextlib import closing
            with closing(sqlite3.connect(args.store)) as check_db:
                if (check_db.execute("SELECT 1 FROM sqlite_master WHERE name='wf_metadata'").fetchone() and
                    check_db.execute("SELECT 1 FROM wf_metadata WHERE key='organization_binding'").fetchone()):
                    print(json.dumps(envelope(error=Fault('ACCESS_DENIED','Use the authenticated create_backup operation'))));return 1
        from .workspace import backup_workspace,restore_workspace
        try:
            if not 1<=args.max_mib<=65536:raise ValueError('max-mib must be between 1 and 65536')
            if args.command=='workspace-backup':result=backup_workspace(args.store,args.root,args.destination,args.max_mib*1024*1024)
            else:result=restore_workspace(args.backup,args.destination,args.max_mib*1024*1024)
            print(json.dumps(envelope(result)));return 0
        except Fault as exc:
            print(json.dumps(envelope(error=exc)));return 1
        except (ValueError,OSError,sqlite3.Error,KeyError,TypeError) as exc:
            print(json.dumps(envelope(error=Fault('WORKSPACE_INVALID',str(exc)))));return 1
    if args.command=='entity':
        from ..entities import loads, EntityError
        from ..entities.schemas import PROPERTIES, VERSION, schema
        try:
            if args.action=='list':result={'schema_version':VERSION,'entity_types':list(PROPERTIES)}
            elif args.action=='schema':result=schema(args.type)
            else:
                if not args.json_file:raise ValueError('--json-file is required')
                with args.json_file.open('rb') as stream:raw=stream.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:raise ValueError('Entity exceeds 2 MiB')
                value=loads(raw.decode('utf-8'),args.type)
                result={'valid':True,'entity_type':value['entity_type'],'schema_version':value['schema_version'],'scope':'schema and local invariants; not execution, reference resolution or authorization'}
            print(json.dumps(envelope(result),allow_nan=False));return 0
        except (ValueError,OSError,TypeError) as exc:
            print(json.dumps(envelope(error=Fault('ENTITY_INVALID',str(exc),getattr(exc,'path','')))));return 1
    if args.placement:
        if args.command not in ('call','run','mcp'):parser.error('--placement supports call, run or mcp only')
        from .placement import connect
        try:service=connect(args.placement)
        except Fault as exc:
            print(json.dumps(envelope(error=exc)));return 1
    elif args.authority:
        from .identity import IdentityStore, OrganizationGateway
        authority=IdentityStore(args.authority)
        try:
            service=(OrganizationGateway(authority) if args.command=='serve' or (args.command=='call' and args.operation=='organization_login') else
                     authority.session(os.environ.get('WORLDFUZZ_SESSION_TOKEN','')))
        except Fault as exc:
            print(json.dumps(envelope(error=exc)));return 1
    else:
        service=Service(args.root,args.store)
    if args.command=='call':
        try:
            raw=args.json
            if args.json_file:
                with args.json_file.open('rb') as stream:
                    payload=stream.read(2*1024*1024+1)
                if len(payload)>2*1024*1024:raise ValueError('Request exceeds 2 MiB')
                raw=payload.decode('utf-8')
            arguments=json.loads(raw,object_pairs_hook=strict_object)
        except (ValueError,OSError):
            print(json.dumps(envelope(error=Fault('SCHEMA_INVALID','Invalid JSON argument'))));return 1
        result=service.call(args.operation,arguments);print(json.dumps(result,allow_nan=False));return 1 if result['status']=='error' else 0
    if args.command=='run':
        try:
            arguments=_run_arguments(args)
            if args.export:
                destination=args.export.resolve()
                if destination.exists():raise Fault('DATA_INVALID','Export directory already exists; choose a new path','$.export')
            previous_timeout=getattr(service,'timeout',None)
            if previous_timeout is not None:service.timeout=max(previous_timeout,args.wait_seconds+15)
            try:result=service.call('run_goal',arguments)
            finally:
                if previous_timeout is not None:service.timeout=previous_timeout
            if result['status']=='ok' and args.export and result['data']['status']=='terminal':
                _export_run(destination,result['data'])
        except (Fault,OSError) as exc:
            if not isinstance(exc,Fault):exc=Fault('DATA_INVALID','Export could not be written: '+type(exc).__name__)
            result=envelope(error=exc)
        print(json.dumps(result,allow_nan=False))
        if result['status']=='error':return 1
        report=result['data'].get('report')
        return report['summary']['exit_code'] if report else 0
    if args.command=='mcp':
        from .mcp import serve
        serve(service,args.read_only);return 0
    from .http import make_server
    server=make_server(service,args.port,docs_base_url=args.docs_base_url)
    print(f'WorldFuzz workspace: http://127.0.0.1:{server.server_port}/',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
    return 0


def _run_arguments(args):
    limits={'cpu_seconds':args.cpu_seconds,'memory_bytes':args.memory_mib*1024*1024,
            'artifact_bytes':args.artifact_mib*1024*1024}
    if args.workflow=='navigation':
        goal={'operation':'solve_navigation','profile':'movingai.octile.v1',
              'parameters':{'case_indices':[args.case_index],'max_cases':1,'algorithm':args.algorithm},
              'verification':{'mode':'metric_recompute'}}
    elif args.workflow=='recorded-state':
        goal={'operation':'view_recorded_states','profile':'robomimic.can-paired.v1',
              'parameters':{'start_index':args.start_index,'frame_count':args.frame_count,'stride':1},
              'verification':{'mode':'integrity'}}
        if args.episode_id:goal['parameters']['episode_id']=args.episode_id
    else:
        goal={'operation':'inspect_sensor_recording','profile':'sensor.mcap.aerial.v1',
              'parameters':{'max_messages':args.max_messages},'verification':{'mode':'integrity'}}
        if args.asset_id:goal['parameters']['asset_id']=args.asset_id
    goal.update(max_seconds=args.max_seconds,resource_limits=limits)
    request={'goal':goal,'idempotency_key':args.key,
             'wait_seconds':args.wait_seconds,'include_artifacts':bool(args.export)}
    if args.resource is not None:request['resource_id']=args.resource
    return request


def _export_run(destination, data):
    destination.mkdir(parents=True)
    (destination/'summary.json').write_text(json.dumps(data['report']['summary'],indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (destination/'junit.xml').write_text(data['report']['junit_xml']+'\n',encoding='utf-8')
    (destination/'result.json').write_text(json.dumps(data['result'],indent=2,allow_nan=False)+'\n',encoding='utf-8')
    exact=destination/'exact-json';exact.mkdir()
    for item in data['artifacts']:
        name=Path(item['name']).name
        (exact/name).write_text(json.dumps(item,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (destination/'export-limitations.json').write_text(json.dumps(data['export_skipped'],indent=2,allow_nan=False)+'\n',encoding='utf-8')
