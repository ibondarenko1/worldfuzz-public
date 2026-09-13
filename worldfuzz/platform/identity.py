"""Local organization authority. Tokens are hashed; each workspace owns its store.

Provisioning is a trusted operator API, never a task request. Filesystem owners
remain trusted. This is local organizational login, not an external SSO claim.
"""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path

from .contracts import Fault, OPERATIONS


def denied():
    return Fault('ACCESS_DENIED', 'Organization, workspace or token scope denied')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}', value):
        raise ValueError('Invalid identity')
    return value


@contextmanager
def connection(path):
    db=sqlite3.connect(path,timeout=15)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:yield db
    finally:db.close()


class IdentityStore:
    def __init__(self, path, *, clock=time.time):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS organizations(id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS principals(org TEXT, id TEXT, salt TEXT, password_hash TEXT,
                    PRIMARY KEY(org,id), FOREIGN KEY(org) REFERENCES organizations(id));
                CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, org TEXT NOT NULL,
                    root TEXT UNIQUE NOT NULL, store TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS memberships(workspace TEXT, principal TEXT, operations TEXT,
                    active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(workspace,principal));
                CREATE TABLE IF NOT EXISTS tokens(id TEXT PRIMARY KEY, workspace TEXT, principal TEXT,
                    operations TEXT, expires REAL, revoked INTEGER NOT NULL DEFAULT 0);
            ''')
            if 'restored_from' not in {r['name'] for r in db.execute('PRAGMA table_info(workspaces)')}:
                db.execute('ALTER TABLE workspaces ADD COLUMN restored_from TEXT')

    def connect(self):
        return connection(self.path)

    def provision(self, organization, principal, password, workspace, root, store, operations):
        """Trusted, local operator provisioning. Does not import or modify episodes."""
        for value in (organization, principal, workspace): identifier(value)
        if not isinstance(password, str) or len(password) < 16: raise ValueError('Password requires 16 characters')
        if not operations or not set(operations) <= set(OPERATIONS): raise ValueError('Explicit operations required')
        root, store = str(Path(root).resolve()), str(Path(store).resolve())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO organizations VALUES(?)', (organization,))
            old = db.execute('SELECT * FROM workspaces WHERE id=?', (workspace,)).fetchone()
            if old and (old['org'],old['root'],old['store']) != (organization,root,store):
                raise ValueError('Workspace binding is immutable')
            for other in db.execute('SELECT * FROM workspaces WHERE id<>?',(workspace,)):
                a,b=Path(other['root']),Path(root)
                if (a==b or a in b.parents or b in a.parents or other['store']==store or
                        Path(store).is_relative_to(a) or Path(other['store']).is_relative_to(b)):
                    raise ValueError('Workspace storage must be disjoint')
            existing=db.execute('SELECT * FROM principals WHERE org=? AND id=?',(organization,principal)).fetchone()
            if existing:
                if not self._password(existing,password): raise denied()
            else:
                salt=secrets.token_hex(16)
                hashed=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
                db.execute('INSERT INTO principals VALUES(?,?,?,?)',(organization,principal,salt,hashed))
            db.execute('INSERT OR IGNORE INTO workspaces(id,org,root,store) VALUES(?,?,?,?)',(workspace,organization,root,store))
            db.execute('INSERT OR REPLACE INTO memberships VALUES(?,?,?,1)',(workspace,principal,json.dumps(sorted(set(operations)))))
        # Bind before accepting authenticated traffic; old rows are retained.
        from .service import Service
        Path(store).parent.mkdir(parents=True,exist_ok=True)
        with connection(store) as db:
            exists=db.execute("SELECT name FROM sqlite_master WHERE name='wf_metadata'").fetchone()
            bound=db.execute("SELECT value FROM wf_metadata WHERE key='organization_binding'").fetchone() if exists else None
        if not bound: Service(root,store,launch=False)
        with connection(store) as db:
            old=db.execute("SELECT value FROM wf_metadata WHERE key='organization_binding'").fetchone()
            binding=json.dumps({'organization':organization,'workspace':workspace,'authority':str(self.path)},sort_keys=True)
            if old and old['value'] != binding: raise denied()
            db.execute("INSERT OR IGNORE INTO wf_metadata VALUES('organization_binding',?)",(binding,))

    @staticmethod
    def _password(row, password):
        if not isinstance(password,str) or len(password)>1024: return False
        hashed=hashlib.scrypt(password.encode(),salt=bytes.fromhex(row['salt']),n=16384,r=8,p=1).hex()
        return hmac.compare_digest(hashed,row['password_hash'])

    def login(self, organization, principal, password, workspace, operations, ttl_seconds=3600):
        if not 1<=ttl_seconds<=86400 or not operations or not set(operations)<=set(OPERATIONS): raise denied()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            account=db.execute('SELECT * FROM principals WHERE org=? AND id=?',(organization,principal)).fetchone()
            ws=db.execute('SELECT * FROM workspaces WHERE id=? AND org=?',(workspace,organization)).fetchone()
            member=db.execute('SELECT * FROM memberships WHERE workspace=? AND principal=?',(workspace,principal)).fetchone()
            if not account or not ws or not member or not member['active'] or not self._password(account,password): raise denied()
            if not set(operations)<=set(json.loads(member['operations'])): raise denied()
            token=secrets.token_urlsafe(32); key=hashlib.sha256(token.encode()).hexdigest()
            db.execute('INSERT INTO tokens VALUES(?,?,?,?,?,0)',(key,workspace,principal,json.dumps(sorted(set(operations))),self.clock()+ttl_seconds))
        return {'token':token,'workspace_id':workspace,'principal_id':principal,'expires_at':self.clock()+ttl_seconds}

    def revoke(self, token):
        with self.connect() as db:
            db.execute('UPDATE tokens SET revoked=1 WHERE id=?',(hashlib.sha256(token.encode()).hexdigest(),))

    def remove_member(self, workspace, principal):
        with self.connect() as db:
            db.execute('UPDATE memberships SET active=0 WHERE workspace=? AND principal=?',(workspace,principal))

    def adopt_restore(self, session, database, root):
        """Register a verified separate copy; retain original job/data identities."""
        import uuid
        ctx=session.check('restore_backup');key='restore-'+uuid.uuid4().hex
        ancestors=sorted(set(ctx['restored_from']+[ctx['id']]))
        source=Path(ctx['root'])/'restores'
        if not Path(root).resolve().is_relative_to(source) or not Path(database).resolve().is_relative_to(source): raise denied()
        with connection(database) as db:
            original=json.loads(db.execute("SELECT value FROM wf_metadata WHERE key='organization_binding'").fetchone()[0])
            if original['workspace']!=ctx['id'] or original['organization']!=ctx['org']:raise denied()
            binding=json.dumps({'organization':ctx['org'],'workspace':key,'authority':str(self.path)},sort_keys=True)
            db.execute("UPDATE wf_metadata SET value=? WHERE key='organization_binding'",(binding,))
            db.execute("INSERT OR REPLACE INTO wf_metadata VALUES('restored_from_workspaces',?)",(json.dumps(ancestors),))
            # Preserve the owner authority instead of issuing fresh full grants.
            # Old records remain for historical provenance; mapped grants govern
            # the new operational workspace with the same expiry/actions/revocation.
            from .authorization import principal_grant_id
            from ..m1_catalog import canonical
            rows=db.execute('SELECT body,revoked FROM wf_policy_grants').fetchall()
            for row in rows:
                grant=json.loads(row['body'])
                if grant['workspace_id']!=ctx['id']:continue
                grant['workspace_id']=key
                grant['grant_id']=principal_grant_id(key,grant['principal']['principal_id'])
                db.execute('INSERT INTO wf_policy_grants VALUES(?,?,?)',(grant['grant_id'],canonical(grant),row['revoked']))
        with self.connect() as db:
            db.execute('INSERT INTO workspaces VALUES(?,?,?,?,?)',(key,ctx['org'],str(Path(root).resolve()),str(Path(database).resolve()),json.dumps(ancestors)))
            db.execute('INSERT INTO memberships VALUES(?,?,?,1)',(key,ctx['principal'],json.dumps(ctx['operations'])))
        return key

    def context(self, key):
        with self.connect() as db:
            token=db.execute('SELECT * FROM tokens WHERE id=?',(key,)).fetchone()
            if token is None or token['revoked'] or token['expires']<=self.clock(): raise denied()
            member=db.execute('SELECT * FROM memberships WHERE workspace=? AND principal=?',(token['workspace'],token['principal'])).fetchone()
            ws=db.execute('SELECT * FROM workspaces WHERE id=?',(token['workspace'],)).fetchone()
            if not member or not member['active'] or not ws: raise denied()
            ancestors=ws['restored_from']
            ancestors=json.loads(ancestors) if ancestors and ancestors.startswith('[') else [ancestors] if ancestors else []
            return {**dict(ws),'restored_from':ancestors,'principal':token['principal'],'operations':sorted(set(json.loads(token['operations'])) & set(json.loads(member['operations']))),'expires':token['expires']}

    def session(self, token, *, launch=True):
        return self.worker_session(hashlib.sha256(token.encode()).hexdigest(), launch=launch)

    def worker_session(self, key, *, launch=False):
        """Trusted worker bootstrap with a stored token fingerprint, never a network API."""
        ctx=self.context(key)
        from .service import Service
        return Service(ctx['root'],ctx['store'],launch=launch,workspace_id=ctx['id'],principal_id=ctx['principal'],
                       identity=Session(self,key,ctx))


class Session:
    def __init__(self, authority, key, context):
        self.authority,self.key,self.bound=authority,key,deepcopy(context)

    def check(self, operation=None):
        current=self.authority.context(self.key)
        for field in ('root','store','id','org','principal','restored_from'):
            if current[field]!=self.bound[field]: raise denied()
        if operation and operation not in current['operations']: raise denied()
        return current

    def check_plan(self, plan, action):
        self.check()
        actor=plan.get('actor')
        if not actor:
            if action not in ('inspect','cancel','export'): raise denied()
            return  # Pre-migration results remain readable within this bound store.
        allowed_workspaces={self.bound['id']}
        if action in ('inspect','verify','export'):allowed_workspaces.update(self.bound.get('restored_from',[]))
        if actor['workspace'] not in allowed_workspaces or actor['organization']!=self.bound['org']: raise denied()
        # Execution and recovery remain tied to the submitting token. Inspection
        # by another authorized workspace member still uses their current grant.
        if action in ('execute','prepare'):
            if actor['token_id']!=self.key: raise denied()
            self.check('execute_plan' if action=='execute' else 'prepare_plan')


class OrganizationGateway:
    """Transport-independent token routing. No mutable shared active workspace."""
    def __init__(self, authority, *, launch=True): self.authority,self.launch=authority,launch
    def authenticate(self, token): return self.authority.session(token,launch=self.launch)
    def call(self, operation, arguments=None):
        from .contracts import envelope, validate, validate_output
        try:
            if operation!='organization_login':raise denied()
            validate(arguments or {},OPERATIONS[operation]['inputSchema'])
            result=self.authority.login(**arguments)
            validate_output(operation,result)
            return envelope(result)
        except Fault as exc:return envelope(error=exc)
