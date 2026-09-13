"""Offline workspace backup/restore. Local database and registered result artifacts."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time

from ..corpus import digest, encoded
from ..storage import SQLiteRepository, validate_catalog
from .contracts import Fault, strict_object

TERMINAL={'completed','failed','cancelled','budget_exhausted','incomplete'}


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def connect(path, readonly=False):
    if readonly:
        db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    else:db=sqlite3.connect(path,timeout=30)
    db.row_factory=sqlite3.Row
    return db


def workspace_fingerprint(db):
    """Logical rows remain identical except the declared artifact-root binding."""
    output={}
    names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for name in names:
        quoted='"'+name.replace('"','""')+'"'
        h=hashlib.sha256();count=0
        for row in db.execute('SELECT * FROM '+quoted+' ORDER BY rowid'):
            if name=='wf_metadata' and row['key']=='artifact_root':continue
            h.update(encoded(list(row)).encode());h.update(b'\n');count+=1
        output[name]={'rows':count,'sha256':h.hexdigest()}
    return output


def inventory(db):
    binding=db.execute("SELECT value FROM wf_metadata WHERE key='artifact_root'").fetchone()
    if not binding:raise Fault('WORKSPACE_INVALID','Catalog lacks artifact-root binding')
    references={}
    has_retention=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wf_retention'").fetchone()
    for row in db.execute('SELECT * FROM wf_runs ORDER BY id'):
        if row['status'] not in TERMINAL:
            raise Fault('WORKSPACE_BUSY','Finish or reconcile active jobs before taking a backup')
        if not re.fullmatch('[0-9a-f]{32}',row['id']):raise Fault('WORKSPACE_INVALID','Invalid run identity')
        if row['result'] is None:continue
        result=json.loads(row['result'],object_pairs_hook=strict_object)
        if digest(result)!=row['result_hash']:raise Fault('INTEGRITY_FAILED','Result hash mismatch')
        retained=db.execute('SELECT state FROM wf_retention WHERE run_id=?',(row['id'],)).fetchone() if has_retention else None
        if retained:
            if retained['state']!='complete':raise Fault('WORKSPACE_BUSY','Reconcile pending retention before backup')
            continue
        artifacts=list(result.get('artifacts',[]))
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wf_attempt_artifacts'").fetchone():
            artifacts.extend(json.loads(r['body']) for r in db.execute(
                'SELECT body FROM wf_attempt_artifacts WHERE attempt_id IN (SELECT attempt_id FROM wf_attempts WHERE run_id=?)',(row['id'],)))
        for item in artifacts:
            name=item.get('name','')
            if not name or '/' in name or '\\' in name or name in ('.','..') or '\x00' in name:
                raise Fault('WORKSPACE_INVALID','Invalid artifact filename')
            checksum=item.get('sha256');size=item.get('bytes')
            if not isinstance(checksum,str) or not re.fullmatch('[0-9a-f]{64}',checksum) or type(size) is not int or size<0:
                raise Fault('WORKSPACE_INVALID','Invalid artifact descriptor')
            stored=item.get('storage_name',name)
            if not stored or '/' in stored or '\\' in stored or stored in ('.','..') or '\x00' in stored:
                raise Fault('WORKSPACE_INVALID','Invalid artifact storage filename')
            key=f"results/{row['id']}/{stored}"
            if key in references and references[key]!={'sha256':checksum,'bytes':size}:
                raise Fault('WORKSPACE_INVALID','Conflicting artifact reference')
            references[key]={'sha256':checksum,'bytes':size}
    for table in ('wf_plans','wf_assets'):
        for row in db.execute('SELECT id,body FROM '+table):
            if digest(json.loads(row['body'],object_pairs_hook=strict_object))!=row['id']:
                raise Fault('INTEGRITY_FAILED','Plan or asset hash mismatch')
    return binding['value'],references


def copy_checked(source,destination,expected,budget):
    source=Path(source);destination=Path(destination)
    if source.is_symlink() or not source.is_file():raise Fault('INTEGRITY_FAILED','Artifact is missing or linked')
    size=source.stat().st_size
    if size!=expected['bytes'] or size>budget[0]:raise Fault('BACKUP_LIMIT','File size mismatch or backup limit exceeded')
    destination.parent.mkdir(parents=True,exist_ok=True)
    h=hashlib.sha256();actual=0
    with source.open('rb') as src,destination.open('xb') as dst:
        while chunk:=src.read(1024*1024):
            actual+=len(chunk)
            if actual>size:raise Fault('INTEGRITY_FAILED','File changed while copying')
            dst.write(chunk);h.update(chunk)
    if actual!=size or h.hexdigest()!=expected['sha256']:raise Fault('INTEGRITY_FAILED','File hash mismatch')
    budget[0]-=actual


def backup_workspace(database,root,destination,max_bytes=1024*1024*1024):
    database=Path(database).resolve();root=Path(root).resolve();destination=Path(destination).absolute()
    if not database.is_file():raise Fault('NOT_FOUND','Workspace database not found')
    if destination.exists():raise FileExistsError('Backup destination already exists')
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=Path(tempfile.mkdtemp(prefix='workspace-backup-',dir=destination.parent))
    try:
        with closing(connect(database)) as locked:
            locked.execute('BEGIN IMMEDIATE')  # Stops new jobs and catalog writers until bytes are frozen.
            try:
                binding,references=inventory(locked)
                if Path(binding).resolve()!=root:raise Fault('WORKSPACE_MISMATCH','Supplied artifact root differs from catalog')
                if len(references)>10000:raise Fault('BACKUP_LIMIT','At most 10000 referenced files per backup')
                if database.stat().st_size+sum(x['bytes'] for x in references.values())>max_bytes:
                    raise Fault('BACKUP_LIMIT','Workspace exceeds configured byte limit')
                SQLiteRepository(database).snapshot(temporary/'catalog.sqlite3')
                db_entry={'sha256':file_hash(temporary/'catalog.sqlite3'),'bytes':(temporary/'catalog.sqlite3').stat().st_size}
                budget=[max_bytes-db_entry['bytes']]
                if budget[0]<0:raise Fault('BACKUP_LIMIT','Snapshot exceeds configured byte limit')
                for relative,expected in references.items():
                    tail=Path(relative).parts[1:];source=root.joinpath(*tail)
                    if source.parent.is_symlink() or source.resolve().parent!=root/tail[0]:
                        raise Fault('INTEGRITY_FAILED','Artifact path escapes its run directory')
                    copy_checked(source,temporary/relative,expected,budget)
                manifest={'format':'worldfuzz.workspace.v1','created_at':time.time(),'original_root':str(root),
                          'files':{'catalog.sqlite3':db_entry,**references},
                          'logical_rows':workspace_fingerprint(locked),'catalog':validate_catalog(locked),
                          'scope':'catalog plus all registered result artifacts; unregistered files excluded'}
                (temporary/'manifest.json').write_text(encoded(manifest)+'\n')
            finally:locked.rollback()
        # Reserve destination exclusively; only a completed manifest is published last.
        destination.mkdir()
        try:
            for name in ('catalog.sqlite3','results'):
                child=temporary/name
                if child.exists():shutil.move(str(child),destination/name)
            shutil.move(str(temporary/'manifest.json'),destination/'manifest.json')
        except Exception:
            shutil.rmtree(destination);raise
        return {'backup':str(destination),'manifest_sha256':digest(manifest),'files':len(manifest['files']),
                'bytes':sum(e['bytes'] for e in manifest['files'].values()),'catalog':manifest['catalog'],'scope':manifest['scope']}
    finally:shutil.rmtree(temporary,ignore_errors=True)


def restore_workspace(backup,destination,max_bytes=1024*1024*1024):
    backup=Path(backup).resolve();destination=Path(destination).absolute()
    if destination.exists():raise FileExistsError('Restore destination already exists')
    manifest_path=backup/'manifest.json'
    if manifest_path.is_symlink() or manifest_path.stat().st_size>8*1024*1024:raise Fault('WORKSPACE_INVALID','Invalid backup manifest')
    manifest=json.loads(manifest_path.read_text(),object_pairs_hook=strict_object)
    if manifest.get('format')!='worldfuzz.workspace.v1':raise Fault('WORKSPACE_INVALID','Unsupported backup format')
    files=manifest.get('files',{})
    if not isinstance(files,dict) or not 1<=len(files)<=10001 or 'catalog.sqlite3' not in files:
        raise Fault('WORKSPACE_INVALID','Invalid backup inventory')
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=Path(tempfile.mkdtemp(prefix='workspace-restore-',dir=destination.parent))
    try:
        budget=[max_bytes]
        for relative,expected in files.items():
            if relative!='catalog.sqlite3' and not re.fullmatch(r'results/[0-9a-f]{32}/[^/\\\x00]+',relative):
                raise Fault('WORKSPACE_INVALID','Invalid backup path')
            if Path(relative).name in ('.','..') or (backup/relative).resolve()!=backup/relative:
                raise Fault('WORKSPACE_INVALID','Linked or escaping backup path')
            copy_checked(backup/relative,temporary/relative,expected,budget)
        with closing(connect(temporary/'catalog.sqlite3',readonly=True)) as db:
            old_root,references=inventory(db)
            if old_root!=manifest['original_root'] or {'catalog.sqlite3':files['catalog.sqlite3'],**references}!=files:
                raise Fault('INTEGRITY_FAILED','Catalog and file inventory disagree')
            if validate_catalog(db)!=manifest['catalog'] or workspace_fingerprint(db)!=manifest['logical_rows']:
                raise Fault('INTEGRITY_FAILED','Logical database content changed')
        (temporary/'results').mkdir(exist_ok=True)
        with closing(connect(temporary/'catalog.sqlite3')) as db:
            db.execute("UPDATE wf_metadata SET value=? WHERE key='artifact_root'",(str((destination/'results').resolve()),));db.commit()
            if workspace_fingerprint(db)!=manifest['logical_rows']:raise Fault('INTEGRITY_FAILED','Restore changed logical rows')
            db.execute('PRAGMA journal_mode=DELETE')
        receipt={'format':'worldfuzz.workspace.restore.v1','restored_at':time.time(),'backup_manifest_sha256':digest(manifest),
                 'database':str(destination/'catalog.sqlite3'),'artifact_root':str(destination/'results'),
                 'catalog':manifest['catalog'],'files_verified':len(files),
                 'changed_fields':['wf_metadata.artifact_root'],'jobs_restarted':0}
        (temporary/'restore.json').write_text(encoded(receipt)+'\n')
        destination.mkdir()
        try:
            for child in temporary.iterdir():
                if child.name!='restore.json':shutil.move(str(child),destination/child.name)
            shutil.move(str(temporary/'restore.json'),destination/'restore.json')
        except Exception:shutil.rmtree(destination);raise
        return receipt
    finally:shutil.rmtree(temporary,ignore_errors=True)
