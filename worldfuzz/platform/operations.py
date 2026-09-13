"""Workspace-local operational audit, recovery and artifact retention."""
import json
from pathlib import Path
import uuid

from ..corpus import digest, encoded
from .contracts import Fault
from .workspace import backup_workspace, restore_workspace, file_hash

TERMINAL={'completed','failed','cancelled','budget_exhausted','incomplete'}


def initialize(service):
    with service.corpus.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS wf_service_audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,at REAL NOT NULL,
          workspace TEXT NOT NULL,principal TEXT NOT NULL,kind TEXT NOT NULL,operation TEXT NOT NULL,
          outcome TEXT NOT NULL,run_id TEXT);
        CREATE TABLE IF NOT EXISTS wf_retention(run_id TEXT PRIMARY KEY,state TEXT NOT NULL,body TEXT NOT NULL);
        ''')


def audit(service,db,kind,operation,outcome,run_id=None):
    db.execute('INSERT INTO wf_service_audit(at,workspace,principal,kind,operation,outcome,run_id) VALUES(?,?,?,?,?,?,?)',
        (service.clock(),service.authorization.workspace_id,service.authorization.principal_id,kind,operation,outcome,run_id))


def metrics(service):
    with service.corpus.connect() as db:
        counts=[dict(r) for r in db.execute("SELECT operation,outcome,COUNT(*) AS count FROM wf_service_audit WHERE kind='request' GROUP BY operation,outcome")]
        states=[dict(r) for r in db.execute('SELECT status,COUNT(*) AS count FROM wf_runs GROUP BY status')]
        retained=db.execute("SELECT COUNT(*) FROM wf_retention WHERE state='complete'").fetchone()[0]
        unknown=db.execute('SELECT COUNT(*) FROM wf_budget_attempts WHERE unknown_tail=1').fetchone()[0]
    return {'workspace_id':service.authorization.workspace_id,'requests':counts,'runs':states,
            'retained_out_runs':retained,'attempts_with_unknown_tail':unknown,
            'scope':'Persisted workspace counters; no fleet, billing or physical metrics'}


def run_audit(service,run_id):
    run=service.get_run(run_id)
    with service.corpus.connect() as db:
        entries=[dict(r) for r in db.execute('SELECT * FROM wf_service_audit WHERE run_id=? ORDER BY seq',(run_id,))]
        retained=db.execute('SELECT state,body FROM wf_retention WHERE run_id=?',(run_id,)).fetchone()
    return {'run_id':run_id,'events':run['events'],'attempts':run['attempts'],'entries':entries,
            'retention':None if not retained else {'state':retained['state'],**json.loads(retained['body'])}}


def backup(service,max_mib):
    with service.corpus.connect() as db:ids=[r['id'] for r in db.execute('SELECT id FROM wf_runs')]
    # Reconcile known worker loss before snapshotting; never silently retry it.
    # This is an already-authorized workspace operation, so do not re-authorize
    # historical terminal runs against an expired execution plan. Their stored
    # result and artifact integrity is checked by backup_workspace below.
    for key in ids:service.jobs.reconcile(key)
    key=uuid.uuid4().hex
    result=backup_workspace(service.corpus.path,service.root,service.root/'backups'/key,max_mib*1024**2)
    return {'backup_id':key,'manifest_sha256':result['manifest_sha256'],'files':result['files'],'bytes':result['bytes'],
            'scope':'Workspace metadata, history and registered result blobs; authority credentials and unregistered files excluded'}


def restore(service,backup_id,max_mib):
    destination=service.root/'restores'/uuid.uuid4().hex
    result=restore_workspace(service.root/'backups'/backup_id,destination,max_mib*1024**2)
    workspace_id=None
    if service.identity:
        workspace_id=service.identity.authority.adopt_restore(service.identity,result['database'],result['artifact_root'])
    return {'restore_id':destination.name,'workspace_id':workspace_id,'database':result['database'],
            'artifact_root':result['artifact_root'],'files_verified':result['files_verified'],
            'jobs_restarted':0,'scope':'Separate restored copy; organization access requires a fresh token for the recovery workspace'}


def retention(service,run_ids,before,dry_run):
    if len(set(run_ids))!=len(run_ids):raise Fault('SCHEMA_INVALID','Duplicate retention selection')
    for key in run_ids:service.get_run(key)
    with service.corpus.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        manifests={}
        for key in run_ids:
            prior=db.execute('SELECT state,body FROM wf_retention WHERE run_id=?',(key,)).fetchone()
            if prior:
                manifests[key]=json.loads(prior['body']);continue
            row=db.execute('SELECT * FROM wf_runs WHERE id=?',(key,)).fetchone()
            if row['status'] not in TERMINAL or row['updated']>=before:raise Fault('RETENTION_PROTECTED','Run is active or newer than cutoff')
            if 'wf_suites' in tables and any(key in r[0] for r in db.execute('SELECT body FROM wf_suites')):
                raise Fault('RETENTION_PROTECTED','Run is referenced by a suite')
            if 'wf_campaign_runs' in tables and db.execute('SELECT 1 FROM wf_campaign_runs WHERE run_id=?',(key,)).fetchone():
                raise Fault('RETENTION_PROTECTED','Run is referenced by a campaign')
            body=json.loads(row['result']) if row['result'] else {}
            if body and digest(body)!=row['result_hash']:raise Fault('INTEGRITY_FAILED','Retention result hash differs')
            # Include partial files from every attempt, not just the final result.
            artifacts={x.get('storage_name',x['name']):x for x in body.get('artifacts',[])}
            for r in db.execute('SELECT body FROM wf_attempt_artifacts WHERE attempt_id IN (SELECT attempt_id FROM wf_attempts WHERE run_id=?)',(key,)):
                x=json.loads(r[0]);artifacts[x.get('storage_name',x['name'])]=x
            manifests[key]={'at':service.clock(),'before':before,'artifacts':list(artifacts.values()),'result_sha256':row['result_hash']}
        if not dry_run:
            for key,body in manifests.items():
                db.execute('INSERT OR IGNORE INTO wf_retention VALUES(?,?,?)',(key,'pending',encoded(body)))
    if dry_run:return {'run_ids':run_ids,'deleted_files':0,'dry_run':True,'state':'preview'}
    deleted=0
    for key,body in manifests.items():
        with service.corpus.connect() as db:
            if db.execute('SELECT state FROM wf_retention WHERE run_id=?',(key,)).fetchone()[0]=='complete':continue
        directory=service.root/key
        if directory.is_symlink() or directory.resolve()!=directory:raise Fault('INTEGRITY_FAILED','Linked retention directory')
        for item in body['artifacts']:
            name=item.get('storage_name',item['name']);path=directory/name
            if Path(name).name!=name or path.is_symlink() or path.resolve().parent!=directory:raise Fault('INTEGRITY_FAILED','Invalid retention path')
            if path.exists():
                if file_hash(path)!=item['sha256'] or path.stat().st_size!=item['bytes']:raise Fault('INTEGRITY_FAILED','Retention artifact changed')
                path.unlink();deleted+=1
        with service.corpus.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE wf_retention SET state='complete' WHERE run_id=?",(key,))
            audit(service,db,'retention','apply_retention','complete',key)
    return {'run_ids':run_ids,'deleted_files':deleted,'dry_run':False,'state':'complete'}
