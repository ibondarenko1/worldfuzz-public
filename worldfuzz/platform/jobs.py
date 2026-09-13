"""Durable attempts with expiring leases and transaction-time fencing."""
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid

from ..corpus import digest, encoded
from .contracts import Fault

LEASE_SECONDS = 3.0


class Jobs:
    def __init__(self, service):
        self.service=service
        self.tokens={}
        with service.corpus.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS wf_attempts(
                    attempt_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,number INTEGER NOT NULL,
                    token TEXT NOT NULL,pid INTEGER NOT NULL,launch_id TEXT,status TEXT NOT NULL,
                    started REAL NOT NULL,heartbeat REAL NOT NULL,lease_until REAL NOT NULL,
                    finished REAL,error TEXT,UNIQUE(run_id,number));
                CREATE UNIQUE INDEX IF NOT EXISTS wf_one_live_attempt ON wf_attempts(run_id)
                    WHERE status='running';
                CREATE TABLE IF NOT EXISTS wf_attempt_artifacts(
                    attempt_id TEXT NOT NULL,name TEXT NOT NULL,body TEXT NOT NULL,
                    PRIMARY KEY(attempt_id,name));
                CREATE TABLE IF NOT EXISTS wf_retries(
                    run_id TEXT NOT NULL,retry_key TEXT NOT NULL,PRIMARY KEY(run_id,retry_key));
            ''')
            columns={row['name'] for row in db.execute('PRAGMA table_info(wf_attempts)')}
            if 'launch_id' not in columns:
                db.execute('ALTER TABLE wf_attempts ADD COLUMN launch_id TEXT')

    def admit(self, db):
        """Caller holds BEGIN IMMEDIATE: reserve the sole local execution slot."""
        if db.execute("SELECT 1 FROM wf_runs WHERE status IN ('queued','running','cancel_requested')").fetchone():
            raise Fault('RESOURCE_BUSY','The local execution slot is reserved',next_action='get_run')

    def claim(self, run_id, launch_id=None):
        now=self.service.clock()
        with self.service.corpus.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT status FROM wf_runs WHERE id=?',(run_id,)).fetchone()
            if not row or row['status']!='queued':return None
            if db.execute("SELECT 1 FROM wf_attempts WHERE status='running'").fetchone():
                return None  # Only one executing worker in this local workspace.
            number=db.execute('SELECT COUNT(*) FROM wf_attempts WHERE run_id=?',(run_id,)).fetchone()[0]+1
            attempt=uuid.uuid4().hex; token=uuid.uuid4().hex
            db.execute('''INSERT INTO wf_attempts(
                attempt_id,run_id,number,token,pid,launch_id,status,started,heartbeat,lease_until,finished,error)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (attempt,run_id,number,token,os.getpid(),launch_id,'running',now,now,now+LEASE_SECONDS,None,None))
            limits=self.service.budgets.begin(db,run_id,attempt)
            db.execute("UPDATE wf_runs SET status='running',updated=? WHERE id=?",(now,run_id))
            self.service._event(db,run_id,'running')
        self.tokens[run_id]=token
        return {'attempt_id':attempt,'number':number,'token':token,'limits':limits}

    def fence(self, db, run_id, *, allow_cancel=False):
        token=self.tokens.get(run_id)
        row=db.execute('''SELECT a.*,r.status AS run_status FROM wf_attempts a JOIN wf_runs r ON r.id=a.run_id
                          WHERE a.run_id=? AND a.token=? AND a.status='running' ''',(run_id,token)).fetchone()
        if row is None or row['lease_until']<=self.service.clock():
            raise Fault('LEASE_LOST','Worker no longer owns a valid lease')
        if row['run_status']=='cancel_requested' and not allow_cancel:
            raise Fault('CANCELLED','Cancellation requested')
        if row['run_status'] not in ('running','cancel_requested'):
            raise Fault('LEASE_LOST','Logical job is no longer running')
        return row

    def heartbeat(self, run_id):
        with self.service.corpus.connect() as db:
            plan_id=db.execute('SELECT plan_id FROM wf_runs WHERE id=?',(run_id,)).fetchone()['plan_id']
        self.service.authorization.authorize_operation(self.service._plan(plan_id),'execute')
        with self.service.corpus.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            attempt=self.fence(db,run_id)
            now=self.service.clock()
            db.execute('UPDATE wf_attempts SET heartbeat=?,lease_until=? WHERE attempt_id=?',
                       (now,now+LEASE_SECONDS,attempt['attempt_id']))

    def heartbeat_thread(self, run_id):
        stop=threading.Event()
        def beat():
            while not stop.wait(.5):
                try:self.heartbeat(run_id)
                except Exception:return  # Never renew a lost/revoked lease.
        thread=threading.Thread(target=beat,daemon=True);thread.start()
        return stop,thread

    def collect(self, run_id, name, data, limit):
        if not name or Path(name).name!=name or name in ('.','..'):
            raise Fault('SCHEMA_INVALID','Artifact name must be a filename')
        with self.service.corpus.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            attempt=self.fence(db,run_id)
            prior=db.execute('SELECT body FROM wf_attempt_artifacts WHERE attempt_id=? AND name=?',
                             (attempt['attempt_id'],name)).fetchone()
            if prior:raise Fault('ARTIFACT_CONFLICT','Artifact already collected in this attempt')
            all_rows=db.execute('''SELECT body FROM wf_attempt_artifacts WHERE attempt_id IN
                                   (SELECT attempt_id FROM wf_attempts WHERE run_id=?)''',(run_id,))
            used=sum(json.loads(row['body'])['bytes'] for row in all_rows)
            if used+len(data)>limit:raise Fault('BUDGET_EXCEEDED','Artifact reservation exhausted across attempts')
            stored=name if attempt['number']==1 else attempt['attempt_id']+'--'+name
            directory=self.service.root/run_id;directory.mkdir(exist_ok=True)
            path=directory/stored
            if path.exists():raise Fault('ARTIFACT_CONFLICT','Unreconciled artifact already exists')
            temporary=directory/(stored+'.tmp')
            with temporary.open('xb') as handle:
                handle.write(data);handle.flush();os.fsync(handle.fileno())
            temporary.replace(path)
            item={'name':name,'storage_name':stored,'attempt_id':attempt['attempt_id'],
                  'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                  'href':f'/v1/results/{run_id}/artifacts/{name}'}
            db.execute('INSERT INTO wf_attempt_artifacts VALUES (?,?,?)',(attempt['attempt_id'],name,encoded(item)))
            return item

    def attempts(self, run_id):
        with self.service.corpus.connect() as db:
            rows=db.execute('''SELECT attempt_id,number,pid,status,started,heartbeat,lease_until,finished,error
                               FROM wf_attempts WHERE run_id=? ORDER BY number''',(run_id,)).fetchall()
            result=[]
            for row in rows:
                value=dict(row)
                value['artifacts']=[json.loads(r['body']) for r in db.execute(
                    'SELECT body FROM wf_attempt_artifacts WHERE attempt_id=? ORDER BY name',(row['attempt_id'],))]
                result.append(value)
            return result

    def reconcile(self, run_id, *, exit_code=None, expected_pid=None, expected_launch_id=None):
        """No retry or fabricated zero usage. Preserve last committed partial data."""
        with self.service.corpus.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM wf_runs WHERE id=?',(run_id,)).fetchone()
            if not row:raise Fault('NOT_FOUND','Run not found')
            attempt=db.execute("SELECT * FROM wf_attempts WHERE run_id=? AND status='running'",(run_id,)).fetchone()
            if row['status'] not in ('queued','running','cancel_requested'):return False
            if expected_pid is not None:
                if attempt is None or attempt['pid'] != expected_pid:
                    return False
            if expected_launch_id is not None:
                if attempt is None or attempt['launch_id'] != expected_launch_id:
                    return False
            if exit_code is None and (attempt is None or attempt['lease_until']>self.service.clock()):return False
            status='cancelled' if row['status']=='cancel_requested' else 'budget_exhausted' if exit_code in (124,1816,-9,-24) else 'incomplete'
            # A SIGKILL alone does not prove budget exhaustion.
            if exit_code == -9:status='incomplete'
            error='WORKER_EXIT' if exit_code is not None else 'LEASE_EXPIRED'
            result=json.loads(row['result']) if row['result'] else {'records':[],'artifacts':[]}
            if attempt:
                artifacts=[json.loads(r['body']) for r in db.execute(
                    'SELECT body FROM wf_attempt_artifacts WHERE attempt_id=? ORDER BY name',(attempt['attempt_id'],))]
                result['artifacts']=artifacts
                db.execute('UPDATE wf_attempts SET status=?,finished=?,error=? WHERE attempt_id=?',
                           ('lost',self.service.clock(),error,attempt['attempt_id']))
                self.service.budgets.close(db,run_id,attempt['attempt_id'],unknown=True)
            result.update(schema_version='1.0.0',execution_status=status,provider_calls=0)
            db.execute('UPDATE wf_runs SET status=?,updated=?,result=?,result_hash=?,error=? WHERE id=?',
                       (status,self.service.clock(),encoded(result),digest(result),error,run_id))
            self.service._event(db,run_id,status)
            self.service.budgets.state(db,run_id,'released' if status!='incomplete' else 'reconciling')
            return True

    def resume(self, run_id, retry_key):
        self.reconcile(run_id)
        plan=self.service._plan(self.service._run_record(run_id,authorize=False)['plan_id'])
        self.service.authorization.authorize_operation(plan,'execute')
        from .runner import check
        check(self.service,plan)
        if plan['code']!=self.service._code() or plan['snapshot']!=self.service._snapshot(plan['request']):
            raise Fault('PLAN_CHANGED','Pinned code or input changed before retry')
        with self.service.corpus.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM wf_retries WHERE run_id=? AND retry_key=?',(run_id,retry_key)).fetchone():return False
            row=db.execute('SELECT * FROM wf_runs WHERE id=?',(run_id,)).fetchone()
            if row['status']!='incomplete':raise Fault('RETRY_UNSUPPORTED','Only reconciled incomplete jobs can resume')
            self.admit(db)
            db.execute('INSERT INTO wf_retries VALUES (?,?)',(run_id,retry_key))
            from .quotas import admit
            admit(db,plan['contract_plan']['resource_limits'],existing_run=run_id)
            self.service.budgets.remaining(db,run_id)
            self.service.budgets.state(db,run_id,'reserved')
            db.execute("UPDATE wf_runs SET status='queued',updated=?,error=NULL WHERE id=?",(self.service.clock(),run_id))
            self.service._event(db,run_id,'queued')
            return True
