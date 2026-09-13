"""WP06-WP08 source, compatibility and generation acceptance."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from worldfuzz.entities.compatibility import check_compatibility
from worldfuzz.entities.examples import examples
from worldfuzz.m1_catalog import LocalBlobStore, VersionedCatalog
from worldfuzz.storage import PostgreSQLRepository


ADMIN_DSN = os.environ.get('WORLDFUZZ_TEST_POSTGRES_DSN')


def verified_asset(blob):
    value = examples('navigation')['AssetVersion']
    value.update(access_status='file_verified', observed_hash_or_null=blob['sha256'],
                 size_or_null=blob['size_bytes'], source_locator='urn:test:pinned-source')
    return value


class CompatibilityTests(unittest.TestCase):
    def test_profiles_resources_and_explicit_rights_share_one_decision(self):
        profile = examples('navigation')['EmbodimentProfile']
        profile['unknown_fields'] = []
        asset = examples('navigation')['AssetVersion']
        asset.update(access_status='file_verified', observed_hash_or_null='a' * 64, size_or_null=1)
        asset['rights'][0].update(status='declared', license='fixture declaration',
                                  allowed_uses=['evaluate'], evidence_urls=['urn:test:rights'])
        limits = examples('navigation')['RunPlan']['resource_limits']
        limits.update(cpu_seconds=10, memory_bytes=1024)
        rights = {'data': 'evaluate', 'code': None, 'model': None, 'scene': None}
        decision = check_compatibility(profile, copy.deepcopy(profile), [asset],
            required_rights=rights, required_resources=limits,
            offered_resources=copy.deepcopy(limits), checked_at=0)
        self.assertEqual(decision['status'], 'compatible')

        changed = copy.deepcopy(profile)
        changed['action_schema'][0]['semantic'] = 'different controller command'
        self.assertEqual(check_compatibility(profile, changed, [asset],
            required_rights=rights, required_resources=limits,
            offered_resources=limits, checked_at=0)['status'], 'incompatible')
        asset['rights'][0].update(status='unknown', license=None, allowed_uses=[], evidence_urls=[])
        self.assertEqual(check_compatibility(profile, profile, [asset],
            required_rights=rights, required_resources=limits,
            offered_resources=limits, checked_at=0)['status'], 'needs_information')

    def test_resource_shortfall_is_incompatible_and_unknown_is_named(self):
        profile = examples('sensors')['EmbodimentProfile']
        profile['unknown_fields'] = []
        asset = examples('sensors')['AssetVersion']
        asset.update(access_status='file_verified', observed_hash_or_null='b' * 64, size_or_null=1)
        asset['rights'][0].update(status='declared', license='fixture declaration',
                                  allowed_uses=['inspect'], evidence_urls=['urn:test:rights'])
        required = examples('sensors')['RunPlan']['resource_limits']
        offered = copy.deepcopy(required)
        offered['wall_seconds'] = required['wall_seconds'] - 1
        decision = check_compatibility(profile, profile, [asset],
            required_rights={'data': 'inspect', 'code': None, 'model': None, 'scene': None},
            required_resources=required, offered_resources=offered, checked_at=0)
        self.assertEqual(decision['status'], 'incompatible')
        self.assertIn('resource.wall_seconds', {item['field'] for item in decision['conflicts']})


class LocalBlobStoreTests(unittest.TestCase):
    def test_content_addressing_is_idempotent_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.bin'
            source.write_bytes(b'worldfuzz pinned bytes')
            store = LocalBlobStore(root / 'blobs')
            first = store.put_file(source)
            second = store.put_file(source, expected_sha256=first['sha256'],
                                    expected_size=first['size_bytes'])
            self.assertEqual(first, second)
            with store.open(first['sha256']) as stream:
                self.assertEqual(stream.read(), source.read_bytes())
            (store.root / first['storage_key']).write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                store.describe(first['sha256'], verify=True)


@unittest.skipUnless(ADMIN_DSN, 'set WORLDFUZZ_TEST_POSTGRES_DSN to an isolated test cluster')
class VersionedCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import conninfo, sql

        cls.psycopg = psycopg
        cls.sql = sql
        cls.dbname = 'worldfuzz_m1_' + uuid.uuid4().hex
        parameters = conninfo.conninfo_to_dict(ADMIN_DSN)
        parameters['dbname'] = cls.dbname
        cls.dsn = conninfo.make_conninfo(**parameters)
        with psycopg.connect(ADMIN_DSN, autocommit=True) as db:
            db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.dbname)))

    @classmethod
    def tearDownClass(cls):
        with cls.psycopg.connect(ADMIN_DSN, autocommit=True) as db:
            db.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s',
                       (cls.dbname,))
            db.execute(cls.sql.SQL('DROP DATABASE {}').format(cls.sql.Identifier(cls.dbname)))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.blobs = LocalBlobStore(Path(self.temp.name) / 'blobs')
        self.catalog = VersionedCatalog(PostgreSQLRepository(self.dsn), self.blobs, clock=lambda: 1)

    def tearDown(self):
        self.temp.cleanup()
        with self.catalog.repository.connect() as db:
            db.execute('TRUNCATE worldfuzz.dataset_generation_members,worldfuzz.split_generation_groups,'
                       'worldfuzz.versioned_entities,worldfuzz.blob_objects CASCADE')

    def fixtures(self):
        payload = Path(self.temp.name) / 'payload.bin'
        payload.write_bytes(b'complete episode payload')
        blob = self.blobs.put_file(payload)
        family = examples('navigation')['SourceFamily']
        asset = verified_asset(blob)
        profile = examples('navigation')['EmbodimentProfile']
        episode = examples('navigation')['Episode']
        episode.update(asset_ref={'id': asset['asset_id'], 'version': asset['version']},
                       profile_ref={'id': profile['profile_id'], 'version': profile['version']},
                       payload_ref={'locator': 'blob:sha256:' + blob['sha256'],
                                    'sha256': blob['sha256'], 'bytes': blob['size_bytes']})
        split = examples('navigation')['SplitManifest']
        split.update(source_lineage=[{'id': asset['asset_id'], 'version': asset['version']}])
        dataset = examples('navigation')['DatasetManifest']
        dataset.update(episode_refs=[{'id': episode['episode_id'], 'version': episode['schema_version']}],
                       split_ref={'id': split['split_id'], 'version': split['version']})
        return family, asset, profile, episode, split, dataset

    def test_canonical_source_asset_and_version_conflicts(self):
        family, asset, *_ = self.fixtures()
        self.assertTrue(self.catalog.register(family)['created'])
        self.assertTrue(self.catalog.register(asset)['created'])
        self.assertFalse(self.catalog.register(asset)['created'])
        changed = copy.deepcopy(asset)
        changed['source_locator'] = 'urn:test:changed'
        with self.assertRaises(ValueError):
            self.catalog.register(changed)
        self.assertEqual(self.catalog.get('AssetVersion', asset['asset_id'], asset['version']), asset)

    def test_generation_is_atomic_idempotent_and_immutable(self):
        family, asset, profile, episode, split, dataset = self.fixtures()
        for entity in (family, asset, profile, episode):
            self.catalog.register(entity)
        first = self.catalog.create_generation(split, dataset)
        second = self.catalog.create_generation(split, dataset)
        self.assertEqual((first['episodes'], first['groups']), (1, 1))
        self.assertFalse(second['dataset']['created'])
        changed = copy.deepcopy(dataset)
        changed['target_schema'] = 'urn:test:changed'
        with self.assertRaises(ValueError):
            self.catalog.create_generation(split, changed)
        with self.catalog.repository.connect() as db:
            count = db.execute('SELECT COUNT(*) AS count FROM worldfuzz.dataset_generation_members').fetchone()['count']
        self.assertEqual(count, 1)

    def test_generation_rejects_group_and_rights_escalation(self):
        family, asset, profile, episode, split, dataset = self.fixtures()
        for entity in (family, asset, profile, episode):
            self.catalog.register(entity)
        split['group_assignments'][0]['group_id'] = 'not-the-episode-group'
        with self.assertRaises(ValueError):
            self.catalog.create_generation(split, dataset)
        split['group_assignments'][0]['group_id'] = episode['group_refs'][0]
        dataset['rights'][0].update(status='verified', license='invented',
                                    allowed_uses=['train'], evidence_urls=['urn:test:invented'])
        with self.assertRaises(ValueError):
            self.catalog.create_generation(split, dataset)


if __name__ == '__main__':
    unittest.main()
