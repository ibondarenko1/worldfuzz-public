"""Durable local task orchestration; no cloud calls, shell commands or device IO."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from ..corpus import Corpus, DEFAULT_STORE, digest, encoded
from .contracts import Fault, OPERATIONS, VERSION, envelope, validate, validate_output
from .adapters import load_registry

TERMINAL = {'completed','failed','cancelled','budget_exhausted','incomplete'}


class Service:
    def __init__(self, root='artifacts/platform', corpus_path=DEFAULT_STORE, launch=True, *,
                 entity_catalog=None, workspace_id='local-workspace', principal_id='local-owner',
                 allowed_actions=None, clock=time.time, identity=None):
        self.identity = identity
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.corpus = Corpus(corpus_path)
        self.launch = launch
        self.worker_processes = []
        self.clock = clock
        self.entity_catalog = entity_catalog
        self.adapters = load_registry()
        with self.corpus.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS wf_assets(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS wf_plans(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS wf_goal_requests(request_key TEXT PRIMARY KEY, request_digest TEXT NOT NULL, plan_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS wf_runs(
                    id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, idem TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                    result TEXT, result_hash TEXT, progress TEXT NOT NULL, error TEXT);
                CREATE TABLE IF NOT EXISTS wf_events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    at REAL NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS wf_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS wf_worker_processes(
                    run_id TEXT NOT NULL,pid INTEGER NOT NULL,started REAL NOT NULL,exit_code INTEGER);
                CREATE TABLE IF NOT EXISTS wf_verifications(
                    id TEXT PRIMARY KEY,run_id TEXT NOT NULL,body TEXT NOT NULL,sha256 TEXT NOT NULL);
                INSERT OR IGNORE INTO wf_metadata VALUES('schema_version','1');
            ''')
            bound=db.execute("SELECT value FROM wf_metadata WHERE key='artifact_root'").fetchone()
            if bound and bound['value']!=str(self.root):
                raise Fault('WORKSPACE_MISMATCH','This catalog is bound to another artifact root')
            db.execute("INSERT OR IGNORE INTO wf_metadata VALUES('artifact_root',?)",(str(self.root),))
            binding=db.execute("SELECT value FROM wf_metadata WHERE key='organization_binding'").fetchone()
            if binding:
                bound=json.loads(binding['value'])
                if identity is None: raise Fault('ACCESS_DENIED','Organization workspace requires an authenticated session')
                ctx=identity.check()
                if (bound['workspace'],bound['organization'],bound['authority'],str(self.root),str(self.corpus.path.resolve())) != (ctx['id'],ctx['org'],str(identity.authority.path),ctx['root'],ctx['store']):
                    raise Fault('WORKSPACE_MISMATCH','Authenticated workspace binding differs')
        from .authorization import ServiceAuthorization
        self.authorization = ServiceAuthorization(
            self, catalog=entity_catalog, workspace_id=workspace_id,
            principal_id=principal_id, allowed_actions=allowed_actions)
        from .jobs import Jobs
        self.jobs=Jobs(self)
        from .budgets import Budgets
        self.budgets=Budgets(self)
        from .operations import initialize
        initialize(self)

    def call(self, operation, arguments=None):
        result=self._call(operation,arguments)
        from .operations import audit
        # Never persist arguments, credentials, token fingerprints or result bodies.
        with self.corpus.connect() as db:
            audit(self,db,'request',operation if operation in OPERATIONS else 'unknown',
                  result.get('error',{}).get('code',result['status']))
        return result

    def _call(self, operation, arguments=None):
        try:
            if operation not in OPERATIONS:
                raise Fault('UNSUPPORTED_OPERATION','Unknown operation')
            arguments = {} if arguments is None else arguments
            validate(arguments, OPERATIONS[operation]['inputSchema'])
            if self.identity: self.identity.check(operation)
            data = getattr(self, operation)(**arguments)
            from .exact_json import project
            validate_output(operation, project(data))
            return envelope(data)
        except Fault as exc:
            response=envelope(error=exc)
            response['error']['failed_operation']=operation
            if exc.code=='SCHEMA_INVALID' and operation in OPERATIONS:
                response['error']['input_schema']=OPERATIONS[operation]['inputSchema']
            return response
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
            return envelope(error=Fault('DATA_INVALID','Local data could not be validated: '+type(exc).__name__))

    def get_capabilities(self):
        return {'profiles':self.adapters.profiles(),'adapters':self.adapters.descriptors(),'operations':list(OPERATIONS),
                'discovery':{'service_tools':list(OPERATIONS),'tool_schema_operation':'describe_operation',
                    'adapter_operations':{p['id']:p['operations'] for p in self.adapters.profiles()},
                    'adapter_invocation':'compile_goal with goal.operation and goal.profile; adapter operations are not MCP tool names'},
                'evidence_rules':['A requested campaign requires create_campaign, launch_campaign_case and finalize_campaign; standalone runs do not satisfy it.',
                    'Unsupported conversion, unavailable replay and held-out training must be refused without substituting a different operation.',
                    'Unknown facts remain unknown. Stored integrity does not prove physics, frame conversion or flight success.',
                    'Preserve caller budgets including max_seconds; request missing fields rather than weakening the goal.',
                    'Nanoseconds use decimal strings in nanoseconds-decimal.v1 responses; stored hashes refer to original bytes.'],
                'limits':{'max_seconds':180,'max_navigation_cases':9,'max_trajectory_points':10000,'max_active_jobs':1,'provider_calls':0},
                'schema_version':VERSION,'deployment':'isolated organization workspace' if self.identity else 'single local workspace',
                'unsupported':['physical control','physical robot replay','training','public hosting and external SSO','automatic cloud execution']}

    def invoke(self, tool, **arguments):
        """Internal compositions must cross the same scoped dispatcher as clients."""
        response=self.call(tool,arguments)
        if response['status']=='error':
            error=response['error']
            raise Fault(error['code'],error['message'],error['field_path'],error['next_action'])
        return response['data']

    def describe_operation(self, name):
        from .contracts import output_schema
        if name not in OPERATIONS:
            if '/' in name or name.startswith('mcp__'):
                raise Fault('UNSUPPORTED_OPERATION','Use a bare tool name from tools/list; select the MCP server separately. Do not repeat the server prefix.',
                            '$.name',next_action='revise_request')
            raise Fault('UNSUPPORTED_OPERATION','Name is not a callable service/MCP tool. Adapter operations belong in goal.operation.',
                        '$.name',next_action='compile_goal')
        spec=OPERATIONS[name]
        return {'name':name,'namespace':'service_tool','input_schema':spec['inputSchema'],
                'output_schema':output_schema(name),'description':spec['description']}

    def get_artifact_json(self, result_id, name):
        from .exact_json import project, CONTRACT
        # Cross both scoped checks; never expose an artifact via an unguarded method.
        if self.identity:self.identity.check('get_artifact_json')
        self.invoke('get_result',result_id=result_id)
        raw=self.artifact(result_id,name)
        if len(raw)>8*1024*1024:raise Fault('DATA_INVALID','JSON view is limited to 8 MiB')
        try:content=json.loads(raw)
        except (ValueError,UnicodeError):raise Fault('PROFILE_INCOMPATIBLE','Artifact is not one JSON document; use the original artifact download')
        if not isinstance(content,dict):raise Fault('PROFILE_INCOMPATIBLE','JSON view requires an object artifact')
        view=project(content)
        return {'result_id':result_id,'name':name,'original_sha256':hashlib.sha256(raw).hexdigest(),
                'representation_sha256':digest(view),'representation':dict(CONTRACT),'content':view}

    def organization_login(self, **arguments):
        raise Fault('ACCESS_DENIED','Login is available at an organization gateway')

    def get_service_metrics(self):
        from .operations import metrics
        return metrics(self)

    def get_workspace_usage(self):
        from .quotas import read
        return read(self)

    def set_workspace_quota(self,limits,expected_version):
        from .quotas import update
        return update(self,limits,expected_version)

    def get_run_audit(self,run_id):
        from .operations import run_audit
        return run_audit(self,run_id)

    def create_backup(self,max_mib=256):
        from .operations import backup
        return backup(self,max_mib)

    def restore_backup(self,backup_id,max_mib=256):
        from .operations import restore
        return restore(self,backup_id,max_mib)

    def apply_retention(self,run_ids,before,dry_run=True):
        from .operations import retention
        return retention(self,run_ids,before,dry_run)

    def create_webhook(self, name):
        from .webhooks import create
        return create(self,name)

    def read_webhook(self, webhook_id, after=0, limit=25):
        from .webhooks import read
        return read(self,webhook_id,after,limit)

    def read_signed_webhook(self, webhook_id, after=0, limit=25):
        from .event_delivery import sign_events
        return sign_events(self,webhook_id,after,limit)

    def get_result_report(self, result_id):
        from .reporting import result_report
        return result_report(self,result_id)

    def find_resources(self, profile=None, limit=25, cursor=0):
        profiles = self.adapters.profiles()
        if profile:
            self.adapters.resolve(profile)
            profiles = [p for p in profiles if p['id']==profile]
        resources = []
        for descriptor in profiles:
            resources.extend(self.adapters.resolve(descriptor['id']).resources(self))
        return {'matches':resources[cursor:cursor+limit],'next_cursor':cursor+limit if cursor+limit<len(resources) else None,
                'total':len(resources),'profiles':profiles}

    def compile_goal(self,goal,provider_mode='disabled'):
        from .goal_planning import compile_goal
        return compile_goal(self,goal,provider_mode)

    def run_goal(self, **request):
        from .workflow import run_goal
        return run_goal(self, **request)

    def create_campaign(self,**request):
        from .campaigns import create
        return create(self,**request)

    def launch_campaign_case(self,campaign_id,case_id,retry_key):
        from .campaigns import launch_case
        return launch_case(self,campaign_id,case_id,retry_key)

    def get_campaign(self,campaign_id):
        from .campaigns import get
        return get(self,campaign_id)

    def finalize_campaign(self,campaign_id):
        from .campaigns import finalize
        return finalize(self,campaign_id)

    def find_verified_results(self,profile,operation,dataset_result_id=None,purpose='evaluation',limit=25,cursor=0):
        from .reuse import find
        return find(self,profile,operation,dataset_result_id,purpose,limit,cursor)

    def create_suite(self,name,purpose,selected_result_ids,dataset_result_id):
        from .reuse import create_suite
        return create_suite(self,name,purpose,selected_result_ids,dataset_result_id)

    def get_suite(self,suite_id):
        from .reuse import get_suite
        return get_suite(self,suite_id)

    def register_asset(self, **asset):
        validate(asset, OPERATIONS['register_asset']['inputSchema'])
        if len(asset['prediction']) != len(asset['reference']):
            raise Fault('PROFILE_INCOMPATIBLE','Trajectory lengths differ')
        previous = -float('inf')
        for predicted, reference in zip(asset['prediction'],asset['reference']):
            if predicted[0] != reference[0] or predicted[0] <= previous:
                raise Fault('PROFILE_INCOMPATIBLE','Timestamps must match exactly and strictly increase; no implicit alignment')
            previous = predicted[0]
        return self._store_asset(asset)

    def _store_asset(self, asset):
        identifier = digest(asset)
        with self.corpus.connect() as db:
            db.execute('INSERT OR IGNORE INTO wf_assets VALUES (?,?)',(identifier,encoded(asset)))
        return {'asset_id':identifier,'sha256':identifier,'evidence':'structure_validated',
                'label_origin':'user_supplied_reference','control_actions_present':False}

    def register_trajectory(self, **request):
        from .trajectories import parse_tum, exact_timestamps
        validate(request, OPERATIONS['register_trajectory']['inputSchema'])
        prediction, p_source = parse_tum(request['prediction_text'], 'prediction_text')
        reference, r_source = parse_tum(request['reference_text'], 'reference_text')
        if exact_timestamps(request['prediction_text']) != exact_timestamps(request['reference_text']):
            raise Fault('PROFILE_INCOMPATIBLE', 'Timestamps must match exactly; explicitly associate trajectories before import')
        asset = {k:request[k] for k in ('name','source_url','data_license','code_license','frame','units')}
        asset.update(prediction=prediction, reference=reference,
                     import_provenance={'adapter':'tum.xyz.v1', 'format_documentation':
                         'https://cvg.cit.tum.de/data/datasets/rgbd-dataset/file_formats',
                         'prediction':p_source,'reference':r_source,
                         'orientation':'preserved in source text; not evaluated',
                         'association':'exact timestamps; no interpolation, alignment or scale estimation'})
        return self._store_asset(asset)

    def register_sensor_recording(self, **request):
        from .mcap_records import parse_mcap
        validate(request,OPERATIONS['register_sensor_recording']['inputSchema'])
        recording=parse_mcap(request['mcap_base64'],request['ros_version'])
        asset={key:request[key] for key in ('name','source_url','source_revision','data_license','code_license',
                                            'platform','frame','units','ros_version')}
        asset.update(recording=recording,import_provenance={'adapter':'mcap.ros.recording.v1',
            'format_specification':'https://mcap.dev/spec','time_semantics':'unsigned nanoseconds from MCAP records',
            'payload_semantics':'opaque ROS serialization; no commands or outcomes inferred'})
        stored=self._store_asset(asset)
        stored.update(evidence='mcap_structure_validated',label_origin='published_or_caller_supplied_recording')
        return stored

    def get_asset(self, asset_id):
        return {'asset_id':asset_id,'sha256':asset_id,'asset':self._asset(asset_id)}

    def _asset(self, identifier):
        with self.corpus.connect() as db:
            row=db.execute('SELECT body FROM wf_assets WHERE id=?',(identifier,)).fetchone()
        if not row: raise Fault('NOT_FOUND','Asset not found')
        body=json.loads(row['body'])
        if digest(body)!=identifier: raise Fault('INTEGRITY_FAILED','Asset digest mismatch')
        return body

    def _code(self):
        paths=[Path(__file__),Path(__file__).with_name('authorization.py'),Path(__file__).with_name('source_contracts.py'),Path(__file__).with_name('contracts.py'),Path(__file__).with_name('worker.py'),Path(__file__).with_name('trajectories.py'),Path(__file__).with_name('verification.py'),
               Path(__file__).with_name('mcap_records.py'),Path(__file__).parents[1]/'corpus.py',Path(__file__).parents[1]/'storage.py',Path(__file__).parents[1]/'m1_catalog.py',Path(__file__).parents[1]/'published_benchmarks.py',
               Path(__file__).parents[1]/'entities'/'__init__.py',Path(__file__).parents[1]/'entities'/'schemas.py',
               Path(__file__).parents[1]/'entities'/'compatibility.py',Path(__file__).parents[1]/'entities'/'policy.py']
        paths.append(Path(__file__).with_name('runner.py'))
        paths.append(Path(__file__).with_name('jobs.py'))
        paths.append(Path(__file__).with_name('verifier.py'))
        paths.append(Path(__file__).with_name('budgets.py'))
        paths.append(Path(__file__).with_name('goal_planning.py'))
        paths.append(Path(__file__).with_name('exact_json.py'))
        paths.append(Path(__file__).with_name('workflow.py'))
        paths.extend(Path(__file__).with_name(name) for name in ('identity.py','webhooks.py'))
        paths.append(Path(__file__).with_name('operations.py'))
        paths.append(Path(__file__).with_name('quotas.py'))
        paths.append(Path(__file__).with_name('campaigns.py'))
        paths.append(Path(__file__).with_name('reuse.py'))
        result={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        base=Path(__file__).parent
        for p in sorted((base/'adapters').glob('*.py')):
            result[p.relative_to(base).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
        result['adapter_registry']=self.adapters.identity()
        return result

    def _episodes(self, filters):
        items=[json.loads(line) for line in self.corpus.export(**filters).splitlines()]
        expected='octile_shortest_path' if filters['domain']=='navigation' else 'PickPlaceCan'
        adapter='movingai-v1' if filters['domain']=='navigation' else 'robomimic-lowdim-v1'
        for item in items:
            if item['episode']['task']!=expected or item['source'].get('adapter')!=adapter:
                raise Fault('PROFILE_INCOMPATIBLE','Episode is not supported by the selected adapter')
        return items

    def _episode(self, episode_id, domain):
        try:item=self.corpus.episode(episode_id,domain)
        except KeyError as exc:raise Fault('SOURCE_UNAVAILABLE','Episode is absent') from exc
        expected='octile_shortest_path' if domain=='navigation' else 'PickPlaceCan'
        adapter='movingai-v1' if domain=='navigation' else 'robomimic-lowdim-v1'
        if item['episode']['task']!=expected or item['source'].get('adapter')!=adapter:
            raise Fault('PROFILE_INCOMPATIBLE','Episode is not supported by the selected adapter')
        return item

    def _snapshot(self, request):
        request=self.adapters.normalized(request)
        adapter=self.adapters.resolve(request['profile'],request['operation'])
        return adapter.snapshot(self, request)

    def prepare_plan(self, **request):
        validate(request, OPERATIONS['prepare_plan']['inputSchema'])
        request=self.adapters.normalized(request)
        request.setdefault('max_seconds',180)
        from .verifier import prepare as prepare_verification
        verification_contract=prepare_verification(request)
        snapshot=self._snapshot(request)
        created_at=self.clock();expires_at=created_at+3600
        contract_plan,decision,authorization_contract=self.authorization.contracts(request,snapshot,expires_at)
        plan={'schema_version':VERSION,'request':request,'snapshot':snapshot,'code':self._code(),
              'adapter':self.adapters.binding(request['profile']),
              'python':sys.version.split()[0],'expires_at':expires_at,'provider_calls':0,
              'execution':'local','created_at':created_at,
              'contract_plan':contract_plan,'compatibility_decision':decision,
              'authorization_contract':authorization_contract,
              'preparation':{'stage':'prepared','execution_started':False,'run_id':None,'run':None,
                  'result_id':None,'export_artifact_created':False}}
        if self.identity:
            ctx=self.identity.check('prepare_plan')
            plan['actor']={'organization':ctx['org'],'workspace':ctx['id'],'principal':ctx['principal'],
                           'token_id':self.identity.key,'authority':str(self.identity.authority.path)}
        plan['verification_contract']=verification_contract
        from .verification import evaluation_contract
        contract=evaluation_contract(request,snapshot)
        if contract is not None:plan['evaluation_contract']=contract
        from .runner import prepare as prepare_runner
        plan['runner_contract']=prepare_runner(self,request,contract_plan)
        authorization=self.authorization.authorize_operation(plan,'prepare')
        identifier=digest(plan)
        with self.corpus.connect() as db:
            db.execute('INSERT INTO wf_plans VALUES (?,?)',(identifier,encoded(plan)))
        return {'plan_id':identifier,'plan_digest':identifier,'compatibility':'compatible','plan':plan,
                'run_plan':contract_plan,'compatibility_decision':decision,
                'authorization':authorization}

    def _plan(self, identifier):
        with self.corpus.connect() as db:
            row=db.execute('SELECT body FROM wf_plans WHERE id=?',(identifier,)).fetchone()
        if not row:raise Fault('NOT_FOUND','Plan not found')
        plan=json.loads(row['body'])
        if digest(plan)!=identifier:raise Fault('INTEGRITY_FAILED','Plan digest mismatch')
        return plan

    def _event(self, db, run_id, status):
        db.execute('INSERT INTO wf_events(run_id,at,status) VALUES (?,?,?)',(run_id,time.time(),status))
        from .operations import audit
        audit(self,db,'run','transition',status,run_id)

    def execute_plan(self, plan_id, plan_digest, idempotency_key):
        if plan_digest!=plan_id:raise Fault('PLAN_CHANGED','Plan digest does not match')
        plan=self._plan(plan_id)
        with self.corpus.connect() as db:
            prior=db.execute('SELECT id,plan_id FROM wf_runs WHERE idem=?',(idempotency_key,)).fetchone()
        if prior:
            if prior['plan_id']!=plan_id:raise Fault('IDEMPOTENCY_CONFLICT','Key belongs to another plan')
            self.authorization.authorize_operation(plan,'inspect')
            return self.get_run(prior['id'])
        if self.clock()>=plan['expires_at']:raise Fault('PLAN_EXPIRED','Prepare a new plan')
        if plan['code']!=self._code() or plan['python']!=sys.version.split()[0] or plan['snapshot']!=self._snapshot(plan['request']):
            raise Fault('PLAN_CHANGED','Code, environment or inputs changed since preparation')
        self.authorization.authorize_operation(plan,'execute')
        from .runner import check as check_runner
        check_runner(self,plan)
        identifier=uuid.uuid4().hex
        try:
            with self.corpus.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                prior=db.execute('SELECT id,plan_id FROM wf_runs WHERE idem=?',(idempotency_key,)).fetchone()
                if prior:
                    if prior['plan_id']!=plan_id:raise Fault('IDEMPOTENCY_CONFLICT','Key belongs to another plan')
                    db.commit()
                    return self.get_run(prior['id'])
                self.jobs.admit(db)
                self.budgets.reserve(db,identifier,plan)
                db.execute('INSERT INTO wf_runs VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (identifier,plan_id,idempotency_key,'queued',self.clock(),self.clock(),None,None,'{}',None))
                self._event(db,identifier,'queued')
        except sqlite3.IntegrityError:
            return self.execute_plan(plan_id,plan_digest,idempotency_key)
        if self.launch:
            try:
                from .runner import spawn
                spawn(self,identifier)
            except OSError:
                self.finish(identifier,'failed',{'records':[]},'Worker could not start')
        return self.get_run(identifier)

    def _run_record(self, run_id, *, authorize):
        self.jobs.reconcile(run_id)
        with self.corpus.connect() as db:
            row=db.execute('SELECT * FROM wf_runs WHERE id=?',(run_id,)).fetchone()
            if not row:raise Fault('NOT_FOUND','Run not found')
            events=[dict(r) for r in db.execute('SELECT seq,at,status FROM wf_events WHERE run_id=? ORDER BY seq',(run_id,))]
        plan=self._plan(row['plan_id'])
        if authorize:self.authorization.authorize_operation(plan,'inspect')
        if row['status'] not in TERMINAL and self.clock()>row['created']+plan['request']['max_seconds']+15:
            partial=json.loads(row['result']) if row['result'] else {'records':[],'artifacts':[]}
            partial.pop('execution_status',None)
            self.finish(run_id,'incomplete',partial,'Worker deadline exceeded; not automatically retried')
            return self.get_run(run_id)
        return {'run_id':run_id,'plan_id':row['plan_id'],'status':row['status'],'progress':json.loads(row['progress']),
                'created_at':row['created'],'updated_at':row['updated'],'result_id':run_id if row['result'] else None,
                'error':row['error'],'events':events,'attempts':self.jobs.attempts(run_id)}

    def get_run(self, run_id):
        return self._run_record(run_id, authorize=True)

    def reconcile_run(self, run_id):
        self.get_run(run_id)
        self.jobs.reconcile(run_id)
        return self.get_run(run_id)

    def resume_run(self, run_id, idempotency_key):
        resumed=self.jobs.resume(run_id,idempotency_key)
        if resumed and self.launch:
            from .runner import spawn
            spawn(self,run_id)
        return self.get_run(run_id)

    def get_budget(self, run_id):
        self.get_run(run_id)
        return self.budgets.report(run_id)

    def cancel_run(self, run_id):
        record=self._run_record(run_id,authorize=False)
        plan=self._plan(record['plan_id'])
        self.authorization.authorize_operation(plan,'cancel')
        with self.corpus.connect() as db:
            changed=db.execute("UPDATE wf_runs SET status='cancel_requested',updated=? WHERE id=? AND status IN ('queued','running')",(time.time(),run_id)).rowcount
            if changed:self._event(db,run_id,'cancel_requested')
        return self.get_run(run_id)

    def checkpoint(self, run_id, progress, partial=None):
        record=self._run_record(run_id,authorize=False)
        if record['status']=='cancel_requested':raise Fault('CANCELLED','Cancellation requested')
        if record['status'] in TERMINAL:raise Fault('STOPPED','Run already terminal')
        plan=self._plan(record['plan_id'])
        self.authorization.authorize_operation(plan,'execute')
        with self.corpus.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.jobs.fence(db,run_id)
            db.execute('UPDATE wf_runs SET progress=?,updated=? WHERE id=?',(encoded(progress),time.time(),run_id))
            if partial is not None:
                data={'schema_version':VERSION,'execution_status':'running','provider_calls':0,**partial}
                db.execute('UPDATE wf_runs SET result=?,result_hash=? WHERE id=?',(encoded(data),digest(data),run_id))

    def finish(self, run_id, status, result, error=None):
        result={'schema_version':VERSION,'execution_status':status,'provider_calls':0,**result}
        with self.corpus.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            attempt=db.execute("SELECT 1 FROM wf_attempts WHERE run_id=? AND status='running'",(run_id,)).fetchone()
            if attempt or run_id in self.jobs.tokens:
                owned=self.jobs.fence(db,run_id,allow_cancel=status=='cancelled')
                self.budgets.close(db,run_id,owned['attempt_id'])
                db.execute('UPDATE wf_attempts SET status=?,finished=?,error=? WHERE attempt_id=?',
                           (status,self.clock(),error,owned['attempt_id']))
            changed=db.execute("UPDATE wf_runs SET status=?,updated=?,result=?,result_hash=?,error=? WHERE id=? AND status NOT IN ('completed','failed','cancelled','budget_exhausted','incomplete')",
                               (status,time.time(),encoded(result),digest(result),error,run_id)).rowcount
            if changed:
                self.budgets.state(db,run_id,'released')
                self._event(db,run_id,status)

    def get_result(self, result_id):
        self.get_run(result_id)
        with self.corpus.connect() as db:
            retained=db.execute('SELECT state FROM wf_retention WHERE run_id=?',(result_id,)).fetchone()
            if retained:raise Fault('RESULT_RETAINED_OUT' if retained['state']=='complete' else 'RETENTION_PENDING',
                                    'Artifacts require a separate backup restore',next_action='get_run_audit')
            row=db.execute('SELECT result,result_hash,plan_id FROM wf_runs WHERE id=?',(result_id,)).fetchone()
        if not row['result']:raise Fault('RESULT_PENDING','Result not available',next_action='get_run')
        result=json.loads(row['result'])
        if digest(result)!=row['result_hash']:raise Fault('INTEGRITY_FAILED','Result digest mismatch')
        for artifact in result.get('artifacts',[]):
            self.artifact(result_id,artifact['name'],result=result)
        return {'result_id':result_id,'result_sha256':row['result_hash'],'plan_id':row['plan_id'],'result':result}

    def get_dataset_generation(self,result_id):
        from .adapters.dataset_build import get_generation
        return get_generation(self,result_id)

    def verify_run_result(self, result_id, mode='integrity', protocol=None):
        import copy
        from .verifier import prepare,verify
        result=self.get_result(result_id)
        plan=self._plan(result['plan_id'])
        self.authorization.authorize_operation(plan,'verify')
        spec={'mode':mode}
        if protocol is not None:spec['protocol']=protocol
        contract=prepare({**plan['request'],'verification':spec})
        records=copy.deepcopy(result['result'].get('records',[]))
        def check():self.authorization.authorize_operation(plan,'verify')
        report=verify(self,result_id,plan,records,result['result'].get('artifacts',[]),check,spec=contract)
        with self.corpus.connect() as db:
            db.execute('INSERT OR IGNORE INTO wf_verifications VALUES (?,?,?,?)',
                       (report['verification_id'],result_id,encoded(report),digest(report)))
        return report

    def artifact(self, run_id, name, result=None):
        if result is None:result=self.get_result(run_id)['result']
        item=next((x for x in result.get('artifacts',[]) if x['name']==name),None)
        if not item or Path(name).name!=name:raise Fault('NOT_FOUND','Artifact not found')
        stored=item.get('storage_name',name)
        if Path(stored).name!=stored:raise Fault('INTEGRITY_FAILED','Artifact storage name changed')
        path=self.root/run_id/stored
        if path.resolve().parent!=(self.root/run_id).resolve():raise Fault('INTEGRITY_FAILED','Artifact location changed')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=item['sha256']:raise Fault('INTEGRITY_FAILED','Artifact digest mismatch')
        return data

    def ingest_verified(self,run_id,source,episodes):
        return self.corpus.ingest(source,episodes,transaction_guard=lambda db:self.jobs.fence(db,run_id))

    def compare_results(self, left_id, right_id, mode="repeat"):
        left,right=self.get_result(left_id),self.get_result(right_id)
        a,b=self._plan(left['plan_id']),self._plan(right['plan_id'])
        if any(x['result']['execution_status']!='completed' for x in (left,right)):
            raise Fault('INCOMPARABLE','Both runs must be completed')
        if mode=='algorithms':
            from .verification import compare_navigation
            return {'left_id':left_id,'right_id':right_id,**compare_navigation(left,right,a,b)}
        if mode!='repeat':raise Fault('SCHEMA_INVALID','Unsupported comparison mode')
        if a['request']!=b['request'] or a['snapshot']!=b['snapshot'] or a['code']!=b['code'] or a['python']!=b['python']:
            raise Fault('INCOMPARABLE','Inputs, procedure or evaluation protocol differ')
        def measurements(value):
            return [{k:v for k,v in r.items() if k!='accumulation'} for r in value['result'].get('records',[])]
        return {'left_id':left_id,'right_id':right_id,'comparable':True,
                'same_records':measurements(left)==measurements(right),
                'scope':'same-procedure repeat comparison; not a comparison of different robot policies'}

    def verify_result(self, profile, filters):
        return self.prepare_plan(operation='verify_episodes',profile=profile,filters=filters)

    def export_result(self, profile, filters):
        return self.prepare_plan(operation='export_dataset',profile=profile,filters=filters)
