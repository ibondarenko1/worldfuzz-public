"""M1 canonical sources, immutable versions, blobs and dataset generations."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from .entities import EntityError, validate_entity


IDENTITIES = {
    'SourceFamily': ('family_id', 'schema_version'),
    'AssetVersion': ('asset_id', 'version'),
    'EmbodimentProfile': ('profile_id', 'version'),
    'Episode': ('episode_id', 'schema_version'),
    'CompatibilityDecision': ('decision_id', 'schema_version'),
    'SplitManifest': ('split_id', 'version'),
    'DatasetManifest': ('dataset_id', 'version'),
}


def canonical(entity):
    return json.dumps(entity, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def identity(entity):
    kind = entity.get('entity_type')
    if kind not in IDENTITIES:
        raise EntityError('Entity is outside the M1 catalog scope')
    id_field, version_field = IDENTITIES[kind]
    return kind, entity[id_field], entity[version_field]


class LocalBlobStore:
    """Immutable local content-addressed storage with bounded streaming writes."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest):
        if len(digest) != 64 or any(ch not in '0123456789abcdef' for ch in digest):
            raise ValueError('Invalid SHA-256 digest')
        path = (self.root / 'sha256' / digest[:2] / digest[2:4] / digest).resolve()
        if self.root not in path.parents:
            raise ValueError('Blob path escapes storage root')
        return path

    def put_file(self, source, *, expected_sha256=None, expected_size=None, max_bytes=512 * 1024 * 1024):
        source = Path(source)
        digest = hashlib.sha256()
        size = 0
        temporary = None
        try:
            with source.open('rb') as incoming:
                with tempfile.NamedTemporaryFile(dir=self.root, prefix='.blob-', delete=False) as outgoing:
                    temporary = Path(outgoing.name)
                    while chunk := incoming.read(1024 * 1024):
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError('Blob exceeds configured size limit')
                        digest.update(chunk)
                        outgoing.write(chunk)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
            value = digest.hexdigest()
            if expected_sha256 is not None and value != expected_sha256:
                raise ValueError('Blob checksum differs from expected value')
            if expected_size is not None and size != expected_size:
                raise ValueError('Blob size differs from expected value')
            destination = self._path(value)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                existing = self.describe(value, verify=True)
                if existing['size_bytes'] != size:
                    raise ValueError('Existing blob size conflict')
            else:
                os.replace(temporary, destination)
                temporary = None
            return {'sha256': value, 'size_bytes': size,
                    'storage_key': destination.relative_to(self.root).as_posix()}
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def describe(self, digest, *, verify=False):
        path = self._path(digest)
        if not path.is_file():
            raise FileNotFoundError('Blob is absent')
        size = path.stat().st_size
        if verify:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != digest:
                raise ValueError('Stored blob integrity check failed')
        return {'sha256': digest, 'size_bytes': size,
                'storage_key': path.relative_to(self.root).as_posix()}

    @contextmanager
    def open(self, digest):
        with self._path(digest).open('rb') as stream:
            yield stream


class VersionedCatalog:
    """Transactional M1 entity catalog over PostgreSQLRepository operations."""

    def __init__(self, repository, blob_store, *, clock=time.time):
        if getattr(repository, 'backend', None) != 'postgresql':
            raise TypeError('M1 catalog requires the PostgreSQL repository backend')
        self.repository = repository
        self.blobs = blob_store
        self.clock = clock
        self.repository.initialize()

    @staticmethod
    def _put(db, entity, created_at):
        validate_entity(entity)
        kind, entity_id, version = identity(entity)
        body = canonical(entity)
        digest = hashlib.sha256(body.encode()).hexdigest()
        inserted = db.execute(
            '''INSERT INTO worldfuzz.versioned_entities
               (entity_type,entity_id,version,body,sha256,created_at)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING entity_id''',
            (kind, entity_id, version, body, digest, created_at),
        ).fetchone()
        if inserted is None:
            prior = db.execute(
                '''SELECT body,sha256 FROM worldfuzz.versioned_entities
                   WHERE entity_type=%s AND entity_id=%s AND version=%s FOR SHARE''',
                (kind, entity_id, version),
            ).fetchone()
            if prior['body'] != body or prior['sha256'] != digest:
                raise ValueError('Immutable entity version conflict')
        return {'entity_type': kind, 'id': entity_id, 'version': version,
                'sha256': digest, 'created': inserted is not None}

    @staticmethod
    def _get(db, kind, entity_id, version):
        row = db.execute(
            '''SELECT body,sha256 FROM worldfuzz.versioned_entities
               WHERE entity_type=%s AND entity_id=%s AND version=%s''',
            (kind, entity_id, version),
        ).fetchone()
        if row is None:
            raise KeyError(f'Unknown {kind} version')
        if hashlib.sha256(row['body'].encode()).hexdigest() != row['sha256']:
            raise ValueError('Versioned entity integrity check failed')
        value = json.loads(row['body'])
        validate_entity(value, kind)
        return value

    def get(self, kind, entity_id, version):
        with self.repository.connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            return self._get(db, kind, entity_id, version)

    def register(self, entity):
        validate_entity(entity)
        kind, _, _ = identity(entity)
        with self.repository.connect() as db:
            if kind == 'AssetVersion':
                self._get(db, 'SourceFamily', entity['family_id'], entity['schema_version'])
                if entity['access_status'] == 'file_verified':
                    blob = self.blobs.describe(entity['observed_hash_or_null'], verify=True)
                    if blob['size_bytes'] != entity['size_or_null']:
                        raise ValueError('Asset size differs from content-addressed blob')
                    db.execute(
                        '''INSERT INTO worldfuzz.blob_objects(sha256,size_bytes,storage_key,created_at)
                           VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                        (blob['sha256'], blob['size_bytes'], blob['storage_key'], self.clock()),
                    )
                    prior = db.execute(
                        'SELECT size_bytes,storage_key FROM worldfuzz.blob_objects WHERE sha256=%s FOR SHARE',
                        (blob['sha256'],),
                    ).fetchone()
                    if prior != {'size_bytes': blob['size_bytes'], 'storage_key': blob['storage_key']}:
                        raise ValueError('Immutable blob metadata conflict')
            elif kind == 'Episode':
                ref = entity['asset_ref']
                self._get(db, 'AssetVersion', ref['id'], ref['version'])
                ref = entity['profile_ref']
                self._get(db, 'EmbodimentProfile', ref['id'], ref['version'])
                locator = entity['payload_ref']['locator']
                if locator.startswith('blob:sha256:'):
                    blob = self.blobs.describe(locator.removeprefix('blob:sha256:'), verify=True)
                    if (blob['sha256'] != entity['payload_ref']['sha256'] or
                            blob['size_bytes'] != entity['payload_ref']['bytes']):
                        raise ValueError('Episode payload reference differs from blob')
            return self._put(db, entity, self.clock())

    @staticmethod
    def _rights_do_not_escalate(dataset, assets):
        by_asset = [{item['subject']: item for item in asset['rights']} for asset in assets]
        for right in dataset['rights']:
            sources = [mapping[right['subject']] for mapping in by_asset]
            allowed = set.intersection(*(set(item['allowed_uses']) for item in sources))
            if not set(right['allowed_uses']) <= allowed:
                raise ValueError('Dataset generation grants a use absent from source rights')
            if right['status'] == 'verified' and any(item['status'] != 'verified' for item in sources):
                raise ValueError('Dataset generation upgrades unverified source rights')

    def create_generation(self, split_manifest, dataset_manifest):
        validate_entity(split_manifest, 'SplitManifest')
        validate_entity(dataset_manifest, 'DatasetManifest')
        if dataset_manifest['split_ref'] != {
                'id': split_manifest['split_id'], 'version': split_manifest['version']}:
            raise ValueError('Dataset generation references a different split manifest')
        episode_refs = dataset_manifest['episode_refs']
        if len({(r['id'], r['version']) for r in episode_refs}) != len(episode_refs):
            raise ValueError('Dataset generation contains duplicate episodes')
        with self.repository.connect() as db:
            assets = [self._get(db, 'AssetVersion', ref['id'], ref['version'])
                      for ref in split_manifest['source_lineage']]
            episodes = [self._get(db, 'Episode', ref['id'], ref['version']) for ref in episode_refs]
            source_refs = {(ref['id'], ref['version']) for ref in split_manifest['source_lineage']}
            if any((episode['asset_ref']['id'], episode['asset_ref']['version']) not in source_refs
                   for episode in episodes):
                raise ValueError('Episode asset is absent from split source lineage')
            assigned = {item['group_id']: item['split'] for item in split_manifest['group_assignments']}
            used = {group for episode in episodes for group in episode['group_refs']}
            if set(assigned) != used:
                raise ValueError('Split manifest must assign every and only generation group')
            self._rights_do_not_escalate(dataset_manifest, assets)
            now = self.clock()
            split_receipt = self._put(db, split_manifest, now)
            dataset_receipt = self._put(db, dataset_manifest, now)
            for ordinal, item in enumerate(split_manifest['group_assignments']):
                db.execute(
                    '''INSERT INTO worldfuzz.split_generation_groups
                       (split_id,split_version,group_id,split,ordinal)
                       VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                    (split_manifest['split_id'], split_manifest['version'],
                     item['group_id'], item['split'], ordinal),
                )
            for ordinal, ref in enumerate(episode_refs):
                db.execute(
                    '''INSERT INTO worldfuzz.dataset_generation_members
                       (dataset_id,dataset_version,episode_id,episode_version,ordinal)
                       VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                    (dataset_manifest['dataset_id'], dataset_manifest['version'],
                     ref['id'], ref['version'], ordinal),
                )
            stored_groups = list(db.execute(
                '''SELECT group_id,split FROM worldfuzz.split_generation_groups
                   WHERE split_id=%s AND split_version=%s ORDER BY ordinal''',
                (split_manifest['split_id'], split_manifest['version']),
            ))
            stored_members = list(db.execute(
                '''SELECT episode_id AS id,episode_version AS version
                   FROM worldfuzz.dataset_generation_members
                   WHERE dataset_id=%s AND dataset_version=%s ORDER BY ordinal''',
                (dataset_manifest['dataset_id'], dataset_manifest['version']),
            ))
            expected_groups = [{'group_id': item['group_id'], 'split': item['split']}
                               for item in split_manifest['group_assignments']]
            if stored_groups != expected_groups or stored_members != episode_refs:
                raise ValueError('Immutable dataset generation membership conflict')
            return {'split': split_receipt, 'dataset': dataset_receipt,
                    'episodes': len(episode_refs), 'groups': len(assigned)}
