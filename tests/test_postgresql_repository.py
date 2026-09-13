"""WP05 acceptance tests against an explicitly configured disposable PostgreSQL cluster."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from worldfuzz.corpus import Corpus, import_navigation, split_for
from worldfuzz.storage import PostgreSQLRepository, SQLiteRepository, copy_catalog


ROOT = Path(__file__).resolve().parents[1]
ADMIN_DSN = os.environ.get('WORLDFUZZ_TEST_POSTGRES_DSN')


class PostgreSQLSchemaResourceTests(unittest.TestCase):
    def test_deployment_and_packaged_schema_are_identical(self):
        for name in ('001_catalog', '002_m1_catalog'):
            deployed = (ROOT / f'deployment/postgresql/{name}.sql').read_text()
            self.assertEqual(deployed, PostgreSQLRepository.schema_sql(name))


@unittest.skipUnless(ADMIN_DSN, 'set WORLDFUZZ_TEST_POSTGRES_DSN to an isolated test cluster')
class PostgreSQLRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import conninfo, sql

        cls.psycopg = psycopg
        cls.sql = sql
        cls.dbname = 'worldfuzz_test_' + uuid.uuid4().hex
        parameters = conninfo.conninfo_to_dict(ADMIN_DSN)
        parameters['dbname'] = cls.dbname
        cls.dsn = conninfo.make_conninfo(**parameters)
        with psycopg.connect(ADMIN_DSN, autocommit=True) as db:
            db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.dbname)))

    @classmethod
    def tearDownClass(cls):
        with cls.psycopg.connect(ADMIN_DSN, autocommit=True) as db:
            db.execute(
                'SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s',
                (cls.dbname,),
            )
            db.execute(cls.sql.SQL('DROP DATABASE {}').format(cls.sql.Identifier(cls.dbname)))

    def setUp(self):
        self.repo = PostgreSQLRepository(self.dsn)
        initialized = self.repo.initialize()
        self.assertEqual(initialized['version'], 2)
        self.assertEqual(self.repo.initialize(), {'version': 2, 'migrated': False, 'backend': 'postgresql'})

    def tearDown(self):
        with self.repo.connect() as db:
            db.execute('TRUNCATE worldfuzz.episodes, worldfuzz.datasets, worldfuzz.corpus_group_splits')

    def test_schema_migration_and_server_constraints_exist(self):
        with self.repo.connect() as db:
            history = db.execute(
                'SELECT version,name FROM worldfuzz.corpus_schema_migrations'
            ).fetchall()
            triggers = db.execute(
                "SELECT COUNT(DISTINCT trigger_name) AS count FROM information_schema.triggers WHERE trigger_schema='worldfuzz'"
            ).fetchone()['count']
            indexes = db.execute(
                "SELECT COUNT(*) AS count FROM pg_indexes WHERE schemaname='worldfuzz' AND indexname='episode_filters'"
            ).fetchone()['count']
        self.assertEqual(history, [
            {'version': 1, 'name': '001_catalog'},
            {'version': 2, 'name': '002_m1_catalog'},
        ])
        self.assertEqual(triggers, 7)
        self.assertEqual(indexes, 1)

    def test_repeat_and_concurrent_import_preserve_source_uniqueness(self):
        def load(_):
            corpus = Corpus(repository=PostgreSQLRepository(self.dsn))
            return import_navigation(corpus, ROOT / 'examples/published-street-cases-20260910')['added']

        with ThreadPoolExecutor(max_workers=2) as pool:
            added = list(pool.map(load, range(2)))
        self.assertEqual(sorted(added), [0, 9])
        corpus = Corpus(repository=self.repo)
        self.assertEqual(import_navigation(corpus, ROOT / 'examples/published-street-cases-20260910')['added'], 0)
        self.assertEqual(len(corpus.listing()['episodes']), 9)

    def test_conflict_rolls_back_whole_import(self):
        common = {'domain': 'navigation', 'task': 'fixture', 'status': 'STRUCTURE_VALIDATED',
                  'label': 'fixture', 'group_id': 'rollback-group', 'states': [[0, 0]]}
        corpus = Corpus(repository=self.repo)
        with self.assertRaises(ValueError):
            corpus.ingest({'name': 'rollback fixture'}, [
                {'source_key': 'duplicate', **common},
                {'source_key': 'duplicate', **common, 'states': [[1, 1]]},
            ])
        self.assertEqual(corpus.listing()['datasets'], [])
        self.assertEqual(corpus.listing()['episodes'], [])

    def test_groups_and_episode_assignments_are_immutable(self):
        corpus = Corpus(repository=self.repo)
        item = {'source_key': 'a', 'domain': 'navigation', 'task': 'fixture',
                'status': 'STRUCTURE_VALIDATED', 'label': 'fixture',
                'group_id': 'shared', 'states': [[0, 0]]}
        corpus.ingest({'name': 'group fixture'}, [item])
        original = split_for('shared')
        changed = 'train' if original != 'train' else 'test'
        with self.assertRaises(self.psycopg.errors.RaiseException):
            with self.repo.connect() as db:
                db.execute('UPDATE worldfuzz.corpus_group_splits SET split=%s', (changed,))
        with self.assertRaises(self.psycopg.errors.RaiseException):
            with self.repo.connect() as db:
                db.execute('DELETE FROM worldfuzz.corpus_group_splits')
        with self.assertRaises(self.psycopg.errors.RaiseException):
            with self.repo.connect() as db:
                db.execute('UPDATE worldfuzz.episodes SET split=%s', (changed,))

    def test_sqlite_to_postgresql_copy_preserves_exact_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = SQLiteRepository(Path(tmp) / 'source.sqlite3')
            corpus = Corpus(repository=source)
            import_navigation(corpus, ROOT / 'examples/published-street-cases-20260910')
            receipt = copy_catalog(source, self.repo)
        self.assertEqual(receipt['added'], 9)
        self.assertEqual(receipt['before'], receipt['after'])
        self.assertEqual(len(Corpus(repository=self.repo).export('navigation').splitlines()), 9)


if __name__ == '__main__':
    unittest.main()
