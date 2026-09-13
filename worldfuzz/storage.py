"""Versioned catalog storage. No data downloads or provider access."""
from contextlib import contextmanager
import hashlib
from importlib import resources
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Protocol


class CatalogRepository(Protocol):
    path: Path | None
    def connect(self): ...
    def initialize(self) -> dict: ...
    def snapshot(self, destination) -> dict: ...
    def ingest_records(self, dataset, metadata, records) -> int: ...
    def catalog_listing(self) -> dict: ...
    def export_records(self, *, domain, status=None, label=None, split=None, episode_id=None) -> dict: ...


BASE = [
    'CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, metadata TEXT NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS episodes (
       id TEXT PRIMARY KEY, dataset TEXT NOT NULL REFERENCES datasets(id),
       source_key TEXT NOT NULL, domain TEXT NOT NULL, task TEXT NOT NULL,
       status TEXT NOT NULL, label TEXT NOT NULL, group_id TEXT NOT NULL,
       split TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
       UNIQUE(dataset, source_key))''',
    'CREATE INDEX IF NOT EXISTS episode_filters ON episodes(domain,status,label,split)',
]
GROUPS = [
    "CREATE TABLE corpus_group_splits (group_id TEXT PRIMARY KEY, split TEXT NOT NULL CHECK(split IN ('train','validation','test')), rule TEXT NOT NULL)",
    "INSERT INTO corpus_group_splits SELECT DISTINCT group_id,split,'legacy-sha256-v1' FROM episodes",
    """CREATE TRIGGER corpus_group_insert BEFORE INSERT ON episodes
       WHEN EXISTS (SELECT 1 FROM corpus_group_splits WHERE group_id=NEW.group_id AND split!=NEW.split)
       BEGIN SELECT RAISE(ABORT,'group split conflict'); END""",
    """CREATE TRIGGER corpus_group_capture AFTER INSERT ON episodes
       BEGIN INSERT OR IGNORE INTO corpus_group_splits VALUES (NEW.group_id,NEW.split,'sha256-v1'); END""",
    """CREATE TRIGGER corpus_episode_group_update BEFORE UPDATE OF group_id,split ON episodes
       WHEN NEW.group_id!=OLD.group_id OR NEW.split!=OLD.split
       BEGIN SELECT RAISE(ABORT,'episode group and split are immutable'); END""",
    """CREATE TRIGGER corpus_group_update BEFORE UPDATE ON corpus_group_splits
       BEGIN SELECT RAISE(ABORT,'group manifest is immutable'); END""",
    """CREATE TRIGGER corpus_group_delete BEFORE DELETE ON corpus_group_splits
       BEGIN SELECT RAISE(ABORT,'group manifest is immutable'); END""",
]
MIGRATIONS = {1:BASE,2:GROUPS}


def fingerprint(db):
    """Streaming checksum over all original columns, including exact payload text."""
    result={}
    for table in ('datasets','episodes'):
        h=hashlib.sha256();count=0
        for row in db.execute('SELECT * FROM '+table+' ORDER BY id'):
            h.update(json.dumps(list(row),separators=(',',':'),ensure_ascii=True).encode());h.update(b'\n');count+=1
        result[table]={'rows':count,'sha256':h.hexdigest()}
    return result


DATASET_COLUMNS = ('id', 'metadata')
EPISODE_COLUMNS = (
    'id', 'dataset', 'source_key', 'domain', 'task', 'status', 'label',
    'group_id', 'split', 'payload', 'sha256',
)


def repository_fingerprint(repository):
    """Backend-neutral checksum over the exact legacy catalog columns."""
    listing = repository.catalog_listing()
    datasets = listing['datasets']
    episodes = []
    for domain in ('navigation', 'manipulation'):
        episodes.extend(repository.export_records(domain=domain)['episodes'])
    if len({row['id'] for row in episodes}) != len(episodes):
        raise ValueError('Repository returned duplicate episode identities')
    if {row['id'] for row in episodes} != {row['id'] for row in listing['episodes']}:
        raise ValueError('Repository listing and export disagree')

    result = {}
    for table, rows, columns in (
        ('datasets', datasets, DATASET_COLUMNS),
        ('episodes', episodes, EPISODE_COLUMNS),
    ):
        checksum = hashlib.sha256()
        for row in sorted(rows, key=lambda item: item['id']):
            checksum.update(json.dumps([row[name] for name in columns], separators=(',', ':'), ensure_ascii=True).encode())
            checksum.update(b'\n')
        result[table] = {'rows': len(rows), 'sha256': checksum.hexdigest()}
    return result


def validate_catalog(db):
    from .corpus import digest, split_for
    if db.execute('PRAGMA quick_check').fetchone()[0]!='ok' or db.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('Database integrity check failed')
    sources={}
    for row in db.execute('SELECT * FROM datasets'):
        if digest(json.loads(row['metadata']))!=row['id']:raise ValueError('Dataset identity changed')
        sources[row['id']]=True
    has_groups=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='corpus_group_splits'").fetchone()
    groups={r['group_id']:r['split'] for r in db.execute('SELECT * FROM corpus_group_splits')} if has_groups else {}
    for row in db.execute('SELECT * FROM episodes'):
        if row['dataset'] not in sources or digest([row['dataset'],row['source_key']])!=row['id']:
            raise ValueError('Episode identity changed')
        if hashlib.sha256(row['payload'].encode()).hexdigest()!=row['sha256']:raise ValueError('Payload hash changed')
        data=json.loads(row['payload'])
        if any(data[k]!=row[k] for k in ('source_key','domain','task','status','label','group_id')):
            raise ValueError('Episode metadata changed')
        expected=groups.get(row['group_id']) if has_groups else split_for(row['group_id'])
        if expected!=row['split']:raise ValueError('Group split mismatch')
    return fingerprint(db)


class SQLiteRepository:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)

    def ingest_records(self,dataset,metadata,records,*,transaction_guard=None):
        added=0
        with self.connect() as db:
            # Reserve the writer before reading identities/group assignments.
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT metadata FROM datasets WHERE id=?',(dataset,)).fetchone()
            if prior and prior['metadata']!=metadata:raise ValueError('Dataset identity conflict')
            if not prior:db.execute('INSERT INTO datasets VALUES (?,?)',(dataset,metadata))
            for record in records:
                if transaction_guard:transaction_guard(db)
                if record['dataset']!=dataset:raise ValueError('Record dataset mismatch')
                prior=db.execute('SELECT sha256 FROM episodes WHERE dataset=? AND source_key=?',
                                 (dataset,record['source_key'])).fetchone()
                if prior:
                    if prior['sha256']!=record['sha256']:raise ValueError('Conflicting episode content for existing source identity')
                    continue
                group=db.execute('SELECT split FROM corpus_group_splits WHERE group_id=?',(record['group_id'],)).fetchone()
                assigned=group['split'] if group else record['proposed_split']
                db.execute('INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    tuple(record[k] for k in ('id','dataset','source_key','domain','task','status','label','group_id'))+
                    (assigned,record['payload'],record['sha256']))
                added+=1
            if transaction_guard:transaction_guard(db)
        return added

    def catalog_listing(self):
        with self.connect() as db:
            db.execute('BEGIN')
            return {'episodes':[dict(r) for r in db.execute('SELECT id,dataset,source_key,domain,task,status,label,group_id,split,sha256 FROM episodes ORDER BY dataset,source_key')],
                    'datasets':[dict(r) for r in db.execute('SELECT * FROM datasets ORDER BY id')]}

    def export_records(self,*,domain,status=None,label=None,split=None,episode_id=None):
        query='SELECT * FROM episodes WHERE domain=?';params=[domain]
        for name,value in (('status',status),('label',label),('split',split),('id',episode_id)):
            if value:query+=' AND '+name+'=?';params.append(value)
        with self.connect() as db:
            db.execute('BEGIN')
            return {'episodes':[dict(r) for r in db.execute(query+' ORDER BY id',params)],
                    'datasets':[dict(r) for r in db.execute('SELECT * FROM datasets ORDER BY id')],
                    'groups':[dict(r) for r in db.execute('SELECT * FROM corpus_group_splits ORDER BY group_id')]}

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
        try:
            with db:yield db
        finally:db.close()

    def initialize(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS corpus_schema_migrations (version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at REAL NOT NULL)')
            history=list(db.execute('SELECT * FROM corpus_schema_migrations ORDER BY version'))
            if [r['version'] for r in history]!=list(range(1,len(history)+1)) or len(history)>max(MIGRATIONS):
                raise ValueError('Unsupported catalog migration history')
            for row in history:
                expected=hashlib.sha256('\n'.join(MIGRATIONS[row['version']]).encode()).hexdigest()
                if row['checksum']!=expected:raise ValueError('Migration definition differs from recorded version')
            current=len(history)
            if current==max(MIGRATIONS):return {'version':current,'migrated':False}
            before=None
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='episodes'").fetchone():
                before=validate_catalog(db)
            for version in range(current+1,max(MIGRATIONS)+1):
                for statement in MIGRATIONS[version]:db.execute(statement)
                db.execute('INSERT INTO corpus_schema_migrations VALUES (?,?,?)',
                           (version,hashlib.sha256('\n'.join(MIGRATIONS[version]).encode()).hexdigest(),time.time()))
            after=validate_catalog(db)
            if before is not None and before!=after:raise ValueError('Migration changed original records')
            return {'version':max(MIGRATIONS),'migrated':True,'original_records':after}

    def snapshot(self,destination):
        """SQLite online backup to a new path, including uncheckpointed WAL state."""
        destination=Path(destination)
        if destination.exists():raise FileExistsError('Snapshot destination already exists')
        destination.parent.mkdir(parents=True,exist_ok=True)
        handle,temp=tempfile.mkstemp(prefix='catalog-snapshot-',suffix='.sqlite3',dir=destination.parent)
        os.close(handle)
        try:
            source=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)
            try:
                target=sqlite3.connect(temp)
                try:
                    source.backup(target)
                    target.execute('PRAGMA journal_mode=DELETE')
                finally:target.close()
            finally:source.close()
            db=sqlite3.connect(temp);db.row_factory=sqlite3.Row
            try:summary=validate_catalog(db)
            finally:db.close()
            # Atomic no-overwrite publication on the same filesystem.
            os.link(temp,destination)
            return {'database':str(destination),'original_records':summary,'scope':'database only; external artifacts not included'}
        finally:
            for suffix in ('','-wal','-shm'):Path(temp+suffix).unlink(missing_ok=True)


class PostgreSQLRepository:
    """PostgreSQL implementation of the corpus repository operations.

    The DSN is intentionally retained only on the repository instance and is not
    exposed as ``path`` because it may contain credentials. Platform job storage
    remains SQLite until its later service-wiring gate.
    """

    path = None
    backend = 'postgresql'
    migration_version = 2
    migrations = ((1, '001_catalog'), (2, '002_m1_catalog'))

    def __init__(self, dsn, *, connect_timeout=10):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError('A PostgreSQL DSN is required')
        self._dsn = dsn
        self.connect_timeout = connect_timeout

    @staticmethod
    def _driver():
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError('PostgreSQL support requires worldfuzz[postgresql]') from exc
        return psycopg, dict_row

    @staticmethod
    def schema_sql(name='001_catalog'):
        return resources.files('worldfuzz').joinpath(f'sql/postgresql/{name}.sql').read_text(encoding='utf-8')

    @contextmanager
    def connect(self):
        psycopg, dict_row = self._driver()
        with psycopg.connect(
            self._dsn,
            row_factory=dict_row,
            connect_timeout=self.connect_timeout,
            application_name='worldfuzz-catalog',
        ) as db:
            yield db

    def initialize(self):
        psycopg, dict_row = self._driver()
        with psycopg.connect(
            self._dsn,
            row_factory=dict_row,
            connect_timeout=self.connect_timeout,
            application_name='worldfuzz-catalog-init',
            autocommit=True,
        ) as db:
            db.execute("SELECT pg_advisory_lock(hashtextextended('worldfuzz.catalog.schema', 0))")
            try:
                exists = db.execute("SELECT to_regclass('worldfuzz.corpus_schema_migrations') AS relation").fetchone()['relation']
                migrated = False
                if exists is None:
                    db.execute(self.schema_sql('001_catalog'))
                    migrated = True
                history = list(db.execute(
                    'SELECT version,name FROM worldfuzz.corpus_schema_migrations ORDER BY version'
                ))
                expected = [{'version': version, 'name': name} for version, name in self.migrations]
                if history != expected[:len(history)]:
                    raise ValueError('Unsupported PostgreSQL catalog migration history')
                for version, name in self.migrations[len(history):]:
                    db.execute(self.schema_sql(name))
                    migrated = True
                    history.append({'version': version, 'name': name})
                if history != expected:
                    raise ValueError('Incomplete PostgreSQL catalog migration history')
                return {'version': self.migration_version, 'migrated': migrated, 'backend': self.backend}
            except Exception:
                # Packaged migrations use an explicit transaction. Restore the
                # session before releasing its advisory lock when any statement fails.
                db.execute('ROLLBACK')
                raise
            finally:
                db.execute("SELECT pg_advisory_unlock(hashtextextended('worldfuzz.catalog.schema', 0))")

    @staticmethod
    def _insert_records(db, dataset, metadata, records):
        db.execute(
            'INSERT INTO worldfuzz.datasets(id,metadata) VALUES (%s,%s) ON CONFLICT (id) DO NOTHING',
            (dataset, metadata),
        )
        prior = db.execute(
            'SELECT metadata FROM worldfuzz.datasets WHERE id=%s FOR SHARE', (dataset,)
        ).fetchone()
        if prior['metadata'] != metadata:
            raise ValueError('Dataset identity conflict')

        added = 0
        for record in records:
            if record['dataset'] != dataset:
                raise ValueError('Record dataset mismatch')
            inserted = db.execute(
                '''INSERT INTO worldfuzz.episodes
                   (id,dataset,source_key,domain,task,status,label,group_id,split,payload,sha256)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (dataset,source_key) DO NOTHING RETURNING id''',
                tuple(record[name] for name in (
                    'id', 'dataset', 'source_key', 'domain', 'task', 'status',
                    'label', 'group_id', 'proposed_split', 'payload', 'sha256',
                )),
            ).fetchone()
            if inserted:
                added += 1
                continue
            prior = db.execute(
                'SELECT id,sha256 FROM worldfuzz.episodes WHERE dataset=%s AND source_key=%s FOR SHARE',
                (dataset, record['source_key']),
            ).fetchone()
            if prior['id'] != record['id'] or prior['sha256'] != record['sha256']:
                raise ValueError('Conflicting episode content for existing source identity')
        return added

    def ingest_records(self, dataset, metadata, records):
        with self.connect() as db:
            return self._insert_records(db, dataset, metadata, records)

    def ingest_catalog(self, datasets, episodes):
        """Atomically copy a complete catalog into an empty PostgreSQL schema."""
        by_dataset = {row['id']: [] for row in datasets}
        for row in episodes:
            if row['dataset'] not in by_dataset:
                raise ValueError('Episode references an unknown dataset')
            by_dataset[row['dataset']].append({**row, 'proposed_split': row['split']})
        with self.connect() as db:
            count = db.execute('SELECT COUNT(*) AS count FROM worldfuzz.datasets').fetchone()['count']
            if count:
                raise ValueError('PostgreSQL catalog copy requires an empty target')
            added = 0
            for dataset in sorted(datasets, key=lambda item: item['id']):
                added += self._insert_records(
                    db, dataset['id'], dataset['metadata'],
                    sorted(by_dataset[dataset['id']], key=lambda item: item['source_key']),
                )
            return added

    def catalog_listing(self):
        with self.connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            return {
                'episodes': list(db.execute(
                    '''SELECT id,dataset,source_key,domain,task,status,label,group_id,split,sha256
                       FROM worldfuzz.episodes ORDER BY dataset,source_key'''
                )),
                'datasets': list(db.execute('SELECT id,metadata FROM worldfuzz.datasets ORDER BY id')),
            }

    def export_records(self, *, domain, status=None, label=None, split=None, episode_id=None):
        query = 'SELECT id,dataset,source_key,domain,task,status,label,group_id,split,payload,sha256 FROM worldfuzz.episodes WHERE domain=%s'
        params = [domain]
        for name, value in (('status', status), ('label', label), ('split', split), ('id', episode_id)):
            if value:
                query += f' AND {name}=%s'
                params.append(value)
        with self.connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            return {
                'episodes': list(db.execute(query + ' ORDER BY id', params)),
                'datasets': list(db.execute('SELECT id,metadata FROM worldfuzz.datasets ORDER BY id')),
                'groups': list(db.execute(
                    'SELECT group_id,split,rule FROM worldfuzz.corpus_group_splits ORDER BY group_id'
                )),
            }

    def snapshot(self, destination):
        raise NotImplementedError('PostgreSQL logical backup belongs to the later deployment/recovery gate')


def copy_catalog(source, destination):
    """Copy exact legacy rows through repository operations into fresh PostgreSQL."""
    destination.initialize()
    listing = source.catalog_listing()
    episodes = []
    for domain in ('navigation', 'manipulation'):
        episodes.extend(source.export_records(domain=domain)['episodes'])
    added = destination.ingest_catalog(listing['datasets'], episodes)
    before = repository_fingerprint(source)
    after = repository_fingerprint(destination)
    if before != after:
        raise ValueError('Cross-backend catalog copy changed original records')
    return {'added': added, 'before': before, 'after': after, 'backend': destination.backend}


def upgrade_copy(source,destination):
    repository=SQLiteRepository(source)
    receipt=repository.snapshot(destination)
    try:
        target=SQLiteRepository(destination);migration=target.initialize()
        with target.connect() as db:after=validate_catalog(db)
        if after!=receipt['original_records']:raise ValueError('Copy upgrade changed original records')
        return {**receipt,'migration':migration,'source_unchanged':True}
    except Exception:
        # Only the newly created failed preview is removed; source is untouched.
        for suffix in ('','-wal','-shm'):Path(str(destination)+suffix).unlink(missing_ok=True)
        raise
