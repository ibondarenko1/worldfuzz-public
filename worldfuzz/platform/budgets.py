"""Atomic per-job reservations and idempotent cumulative phase usage.

Lost-worker tails are unknown actual usage. A separate conservative charge is
used when deciding whether the original reservation can fund another attempt.
"""
import json
import math
import time

from ..corpus import encoded
from ..entities import validate_entity
from .contracts import Fault

PHASES = ('search', 'reduction', 'verification', 'replay')
RESOURCES = ('cpu_seconds', 'wall_seconds', 'artifact_bytes', 'model_calls', 'device_calls')


class Budgets:
    def __init__(self,service):
        self.service=service
        with service.corpus.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS wf_budget_reservations(
                    run_id TEXT PRIMARY KEY,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS wf_usage(
                    run_id TEXT NOT NULL,attempt_id TEXT NOT NULL,phase TEXT NOT NULL,
                    resource TEXT NOT NULL,value REAL NOT NULL,
                    PRIMARY KEY(run_id,attempt_id,phase,resource));
                CREATE TABLE IF NOT EXISTS wf_budget_attempts(
                    attempt_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,phase TEXT NOT NULL,
                    cpu_ceiling REAL NOT NULL,wall_ceiling REAL NOT NULL,
                    charge_cpu REAL NOT NULL DEFAULT 0,charge_wall REAL NOT NULL DEFAULT 0,
                    closed INTEGER NOT NULL DEFAULT 0,unknown_tail INTEGER NOT NULL DEFAULT 0);
            ''')
            # Repair earlier calculated holds without changing any observation,
            # event or terminal result. Old wall-derived CPU holds were unsafe.
            db.execute('''UPDATE wf_budget_attempts
                SET charge_cpu=MAX(charge_cpu,cpu_ceiling),
                    charge_wall=MAX(charge_wall,wall_ceiling)
                WHERE closed=1 AND unknown_tail=1''')
            from .quotas import initialize
            initialize(db)

    def reserve(self,db,run_id,plan):
        body={'entity_type':'BudgetReservation','schema_version':'1.0.0','reservation_id':run_id,
              'workspace_id':self.service.authorization.workspace_id,
              'plan_digest':plan['contract_plan']['digest'],
              'limits':plan['contract_plan']['resource_limits'],'estimated_usage':[],
              'actual_usage':[],'unknown_usage':[],
              'expires_at':plan['expires_at'],'state':'reserved'}
        validate_entity(body,'BudgetReservation')
        prior=db.execute('SELECT body FROM wf_budget_reservations WHERE run_id=?',(run_id,)).fetchone()
        if prior:
            if json.loads(prior['body'])['plan_digest']!=body['plan_digest']:
                raise Fault('BUDGET_CONFLICT','Reservation belongs to a different plan')
            return
        from .quotas import admit
        admit(db,body['limits'])
        active=[json.loads(r['body']) for r in db.execute('SELECT body FROM wf_budget_reservations')]
        committed=sum(r['limits']['memory_bytes'] for r in active if r['state'] in ('reserved','consuming'))
        if committed+body['limits']['memory_bytes']>4*1024**3:
            raise Fault('RESOURCE_BUSY','Local aggregate reserved-memory ceiling exceeded')
        db.execute('INSERT INTO wf_budget_reservations VALUES (?,?)',(run_id,encoded(body)))

    def state(self,db,run_id,value):
        row=db.execute('SELECT body FROM wf_budget_reservations WHERE run_id=?',(run_id,)).fetchone()
        if row:
            body=json.loads(row['body']);body['state']=value
            db.execute('UPDATE wf_budget_reservations SET body=? WHERE run_id=?',(encoded(body),run_id))

    def remaining(self,db,run_id):
        row=db.execute('SELECT body FROM wf_budget_reservations WHERE run_id=?',(run_id,)).fetchone()
        if row is None:raise Fault('BUDGET_MISSING','Job has no atomic reservation')
        limits=json.loads(row['body'])['limits'].copy()
        spent=db.execute('SELECT COALESCE(SUM(charge_cpu),0),COALESCE(SUM(charge_wall),0) FROM wf_budget_attempts WHERE run_id=? AND closed=1',(run_id,)).fetchone()
        limits['cpu_seconds']-=spent[0];limits['wall_seconds']-=spent[1]
        used = dict(db.execute(
            '''SELECT resource,COALESCE(SUM(value),0) FROM wf_usage
               WHERE run_id=? AND resource IN ('artifact_bytes','model_calls','device_calls')
               GROUP BY resource''', (run_id,)).fetchall())
        for resource in ('artifact_bytes', 'model_calls', 'device_calls'):
            limits[resource] -= used.get(resource, 0)
        if limits['cpu_seconds']<=0 or limits['wall_seconds']<=0:
            raise Fault('BUDGET_EXCEEDED','Original reservation cannot fund another attempt')
        if any(limits[name] < 0 for name in ('artifact_bytes','model_calls','device_calls')):
            raise Fault('BUDGET_EXCEEDED','Original non-compute reservation is exhausted')
        return limits

    def begin(self,db,run_id,attempt_id):
        limits=self.remaining(db,run_id)
        db.execute('INSERT INTO wf_budget_attempts(attempt_id,run_id,phase,cpu_ceiling,wall_ceiling) VALUES (?,?,?,?,?)',
                   (attempt_id,run_id,'search',limits['cpu_seconds'],limits['wall_seconds']))
        for phase in ('search', 'reduction', 'verification'):
            for resource in RESOURCES:
                db.execute('INSERT INTO wf_usage VALUES (?,?,?,?,0)',(run_id,attempt_id,phase,resource))
        self.state(db,run_id,'consuming')
        return limits

    def sample(self,run_id,phase,values):
        if phase not in PHASES:raise Fault('BUDGET_INVALID','Unknown accounting phase')
        with self.service.corpus.connect() as db:
            db.execute('BEGIN IMMEDIATE');attempt=self.service.jobs.fence(db,run_id,allow_cancel=True)
            if phase == 'replay':
                for resource in RESOURCES:
                    db.execute('INSERT OR IGNORE INTO wf_usage VALUES (?,?,?,?,0)',
                               (run_id,attempt['attempt_id'],phase,resource))
            for resource,value in values.items():
                if (resource not in RESOURCES or isinstance(value, bool) or
                        not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                    raise Fault('BUDGET_INVALID','Invalid usage sample')
                prior=db.execute('SELECT value FROM wf_usage WHERE run_id=? AND attempt_id=? AND phase=? AND resource=?',
                    (run_id,attempt['attempt_id'],phase,resource)).fetchone()
                if prior is None or value<prior['value']:raise Fault('BUDGET_INVALID','Cumulative usage must not decrease')
                db.execute('UPDATE wf_usage SET value=? WHERE run_id=? AND attempt_id=? AND phase=? AND resource=?',
                           (value,run_id,attempt['attempt_id'],phase,resource))
            db.execute('UPDATE wf_budget_attempts SET phase=? WHERE attempt_id=?',(phase,attempt['attempt_id']))

    def close(self,db,run_id,attempt_id,*,unknown=False):
        row=db.execute('SELECT * FROM wf_budget_attempts WHERE attempt_id=?',(attempt_id,)).fetchone()
        if row is None or row['closed']:return
        totals={r['resource']:r['value'] for r in db.execute('SELECT resource,SUM(value) AS value FROM wf_usage WHERE attempt_id=? GROUP BY resource',(attempt_id,))}
        cpu=totals.get('cpu_seconds',0);wall=totals.get('wall_seconds',0)
        if unknown:
            # Wall time cannot upper-bound process CPU across multiple threads.
            # No authenticated final CPU observation exists for a lost attempt:
            # hold its entire allocation, separately from measured samples.
            cpu=max(cpu,row['cpu_ceiling'])
            wall=max(wall,row['wall_ceiling'])
        db.execute('UPDATE wf_budget_attempts SET closed=1,unknown_tail=?,charge_cpu=?,charge_wall=? WHERE attempt_id=?',
                   (int(unknown),cpu,wall,attempt_id))

    def report(self,run_id):
        with self.service.corpus.connect() as db:
            row=db.execute('SELECT body FROM wf_budget_reservations WHERE run_id=?',(run_id,)).fetchone()
            if row is None:raise Fault('BUDGET_MISSING','Historical job has no reservation')
            body=json.loads(row['body'])
            attempts=db.execute('SELECT * FROM wf_budget_attempts WHERE run_id=?',(run_id,)).fetchall()
            usage=db.execute('SELECT * FROM wf_usage WHERE run_id=? ORDER BY attempt_id,phase,resource',(run_id,)).fetchall()
            body['actual_usage']=[{'phase':r['phase'],'resource':r['resource'],'value':r['value'],
                                  'unit':'bytes' if r['resource']=='artifact_bytes' else 'calls' if r['resource'].endswith('calls') else 's',
                                  'basis':'Cumulative observed process sample for attempt '+r['attempt_id']}
                                  for r in usage]
            body['unknown_usage']=[{'phase':a['phase'],'resource':'memory_bytes',
                                   'reason':'Peak usage not sampled; memory ceiling enforced for attempt '+a['attempt_id']}
                                  for a in attempts]
            for attempt in attempts:
                if attempt['unknown_tail'] or not attempt['closed']:
                    for resource in ('cpu_seconds','wall_seconds'):
                        body['unknown_usage'].append({'phase':attempt['phase'],'resource':resource,
                            'reason':'Unobserved tail after last sample for attempt '+attempt['attempt_id']})
            validate_entity(body,'BudgetReservation')
            accounting=[]
            for attempt in attempts:
                item=dict(attempt)
                measured={r['resource']:r['value'] for r in db.execute(
                    'SELECT resource,SUM(value) AS value FROM wf_usage WHERE attempt_id=? GROUP BY resource',
                    (attempt['attempt_id'],))}
                item['observed_cpu_seconds']=measured.get('cpu_seconds',0)
                item['observed_wall_seconds']=measured.get('wall_seconds',0)
                item['unmeasured_cpu_hold_seconds']=max(0,attempt['charge_cpu']-item['observed_cpu_seconds']) if attempt['closed'] else None
                item['unmeasured_wall_hold_seconds']=max(0,attempt['charge_wall']-item['observed_wall_seconds']) if attempt['closed'] else None
                item['charge_basis']='full_attempt_allocation_after_loss' if attempt['unknown_tail'] else 'observed_samples'
                accounting.append(item)
            return {'reservation':body,'attempt_accounting':accounting,
                    'scope':'Observed samples and unknown tails; conservative retry charges are not measured consumption'}


class Meter:
    def __init__(self,service,run_id):
        self.service=service;self.run_id=run_id;self.phase='search'
        self.values={p:{'cpu_seconds':0.,'wall_seconds':0.,'artifact_bytes':0} for p in PHASES}
        self.cpu=time.process_time();self.wall=time.monotonic()

    def sample(self):
        cpu=time.process_time();wall=time.monotonic()
        self.values[self.phase]['cpu_seconds']+=max(0,cpu-self.cpu)
        self.values[self.phase]['wall_seconds']+=max(0,wall-self.wall)
        self.cpu=cpu;self.wall=wall
        self.service.budgets.sample(self.run_id,self.phase,self.values[self.phase])

    def switch(self,phase):
        if phase not in PHASES:raise Fault('BUDGET_INVALID','Unknown accounting phase')
        self.sample();self.phase=phase

    def artifact(self,size):
        self.values[self.phase]['artifact_bytes']+=size
        self.sample()
