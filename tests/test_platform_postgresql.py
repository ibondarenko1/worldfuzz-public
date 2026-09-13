"""WP09 service wiring against an isolated live PostgreSQL M1 catalog."""
import os
from pathlib import Path
import tempfile
import time
import unittest
import uuid

from worldfuzz.m1_catalog import LocalBlobStore, VersionedCatalog
from worldfuzz.platform.service import Service
from worldfuzz.storage import PostgreSQLRepository


ADMIN_DSN=os.environ.get('WORLDFUZZ_TEST_POSTGRES_DSN')


@unittest.skipUnless(ADMIN_DSN,'set WORLDFUZZ_TEST_POSTGRES_DSN to an isolated test cluster')
class PostgreSQLServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import conninfo,sql
        cls.psycopg=psycopg;cls.sql=sql
        cls.dbname='worldfuzz_m2_'+uuid.uuid4().hex
        parameters=conninfo.conninfo_to_dict(ADMIN_DSN);parameters['dbname']=cls.dbname
        cls.dsn=conninfo.make_conninfo(**parameters)
        with psycopg.connect(ADMIN_DSN,autocommit=True) as db:
            db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.dbname)))

    @classmethod
    def tearDownClass(cls):
        with cls.psycopg.connect(ADMIN_DSN,autocommit=True) as db:
            db.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s',(cls.dbname,))
            db.execute(cls.sql.SQL('DROP DATABASE {}').format(cls.sql.Identifier(cls.dbname)))

    def test_service_registers_resolves_authorizes_and_executes_exact_m1_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            catalog=VersionedCatalog(PostgreSQLRepository(self.dsn),LocalBlobStore(root/'blobs'))
            service=Service(root/'results',root/'service.sqlite3',launch=True,entity_catalog=catalog)
            prepared=service.prepare_plan(operation='solve_navigation',profile='movingai.octile.v1',
                                          case_indices=[0],max_seconds=30)
            reference=prepared['run_plan']['input_versions'][0]
            self.assertEqual(reference, {
                'id':'902dcd0856dede0e7a7b003e3170d718dabc99ff4fae3eba51842e9837a69400',
                'version':'pinned-original-selection-v1'})
            self.assertEqual(prepared['run_plan']['runner_requirements']['profile_refs'], [{
                'id':'worldfuzz.legacy.navigation','version':'1.0.0'}])
            self.assertEqual(catalog.get('AssetVersion',reference['id'],reference['version'])['asset_id'],reference['id'])
            right=next(item for item in catalog.get('AssetVersion',reference['id'],reference['version'])['rights']
                       if item['subject']=='data')
            self.assertEqual((right['status'],right['allowed_uses']),
                             ('verified',['inspect','evaluate','export']))
            with catalog.repository.connect() as db:
                counts={row['entity_type']:row['count'] for row in db.execute(
                    'SELECT entity_type,COUNT(*) AS count FROM worldfuzz.versioned_entities GROUP BY entity_type')}
            self.assertEqual(counts,{'AssetVersion':1,'CompatibilityDecision':1,
                                     'EmbodimentProfile':1,'SourceFamily':1})
            queued=service.execute_plan(prepared['plan_id'],prepared['plan_digest'],'postgresql-m2')
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                state=service.get_run(queued['run_id'])['status']
                if state in ('completed','failed','incomplete','budget_exhausted'):break
                time.sleep(.05)
            self.assertEqual(state,'completed')
            self.assertEqual(service.get_result(queued['run_id'])['result']['execution_status'],'completed')


if __name__=='__main__':unittest.main()
