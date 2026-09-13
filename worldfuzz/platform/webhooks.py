"""Tenant-local durable webhook pull outbox; no external delivery or credentials."""
import uuid
from .contracts import Fault


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS wf_webhooks(id TEXT PRIMARY KEY,name TEXT NOT NULL)')


def create(service,name):
    with service.corpus.connect() as db:
        initialize(db)
        key=uuid.uuid4().hex
        db.execute('INSERT INTO wf_webhooks VALUES(?,?)',(key,name))
    return {'webhook_id':key,'name':name,'delivery':'authenticated_pull','external_delivery':False}


def read(service,key,after,limit):
    with service.corpus.connect() as db:
        initialize(db)
        if not db.execute('SELECT id FROM wf_webhooks WHERE id=?',(key,)).fetchone():
            raise Fault('NOT_FOUND','Webhook not found in this workspace')
        rows=[dict(r) for r in db.execute('SELECT * FROM wf_events WHERE seq>? ORDER BY seq LIMIT ?',(after,limit))]
    for event in rows: service.get_run(event['run_id'])
    return {'webhook_id':key,'events':rows,'next_cursor':rows[-1]['seq'] if rows else after,
            'delivery':'authenticated_pull','external_delivery':False}
