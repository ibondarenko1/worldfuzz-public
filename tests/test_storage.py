"""Transactional migration, durable groups and SQLite snapshot behavior."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from worldfuzz.corpus import Corpus, import_navigation, split_for
from worldfuzz.storage import SQLiteRepository, upgrade_copy, validate_catalog, MIGRATIONS

ROOT=Path(__file__).resolve().parents[1]


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)

    def tearDown(self):self.temp.cleanup()

    def legacy(self):
        path=self.root/'old.sqlite3'
        db=sqlite3.connect(path)
        for statement in MIGRATIONS[1]:db.execute(statement)
        db.commit();db.close()
        # Fill with published episodes, then remove only migration-added metadata
        # to model the pre-migration schema while preserving all original records.
        corpus=Corpus(path);import_navigation(corpus,ROOT/'examples/published-street-cases-20260910')
        with corpus.connect() as db:
            for name in ('corpus_group_insert','corpus_group_capture','corpus_episode_group_update','corpus_group_update','corpus_group_delete'):
                db.execute('DROP TRIGGER '+name)
            db.execute('DROP TABLE corpus_group_splits');db.execute('DROP TABLE corpus_schema_migrations')
            db.execute('CREATE TABLE unrelated_notes (value TEXT)');db.execute("INSERT INTO unrelated_notes VALUES ('retain me')")
        return path

    def test_copy_upgrade_preserves_records_and_is_idempotent(self):
        source=self.legacy();destination=self.root/'new.sqlite3'
        receipt=upgrade_copy(source,destination)
        self.assertEqual(receipt['original_records']['episodes']['rows'],9)
        self.assertEqual(receipt['migration']['version'],2)
        self.assertFalse(SQLiteRepository(destination).initialize()['migrated'])
        with SQLiteRepository(destination).connect() as db:
            self.assertEqual(db.execute('SELECT value FROM unrelated_notes').fetchone()[0],'retain me')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM corpus_group_splits').fetchone()[0],3)
        with closing(sqlite3.connect(source)) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='corpus_schema_migrations'").fetchone())
        with self.assertRaises(FileExistsError):upgrade_copy(source,destination)

    def test_failure_rolls_back_in_place_and_removes_failed_preview(self):
        source=self.legacy()
        with patch.dict(MIGRATIONS,{2:[*MIGRATIONS[2],'THIS IS NOT SQL']}):
            with self.assertRaises(sqlite3.Error):SQLiteRepository(source).initialize()
            with self.assertRaises(sqlite3.Error):upgrade_copy(source,self.root/'failed.sqlite3')
        self.assertFalse((self.root/'failed.sqlite3').exists())
        with closing(sqlite3.connect(source)) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='corpus_group_splits'").fetchone())
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='corpus_schema_migrations'").fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM episodes').fetchone()[0],9)

    def test_groups_survive_rule_changes_and_reject_mutation(self):
        corpus=Corpus(self.root/'groups.sqlite3')
        item={'source_key':'a','domain':'navigation','task':'authored fixture',
              'status':'STRUCTURE_VALIDATED','label':'fixture','group_id':'shared','states':[[0,0]]}
        corpus.ingest({'name':'fixture'},[item])
        original=split_for('shared');changed='train' if original!='train' else 'test'
        with patch('worldfuzz.corpus.split_for',return_value=changed):
            corpus.ingest({'name':'fixture'},[{**item,'source_key':'b'}])
            self.assertEqual({r['split'] for r in corpus.listing()['episodes']},{original})
            self.assertEqual(len(corpus.export('navigation').splitlines()),2)
        with self.assertRaises(sqlite3.IntegrityError):
            with corpus.connect() as db:db.execute('UPDATE episodes SET split=?',(changed,))
        with self.assertRaises(sqlite3.IntegrityError):
            with corpus.connect() as db:db.execute('DELETE FROM corpus_group_splits')

    def test_future_version_and_damaged_payload_rejected(self):
        source=self.legacy()
        with closing(sqlite3.connect(source)) as db:
            with db:db.execute("UPDATE episodes SET payload='{}'")
        with self.assertRaises(ValueError):upgrade_copy(source,self.root/'invalid.sqlite3')
        target=Corpus(self.root/'future.sqlite3')
        with target.connect() as db:db.execute("INSERT INTO corpus_schema_migrations VALUES (3,'future',0)")
        with self.assertRaises(ValueError):Corpus(target.path)

    def test_backup_includes_committed_wal_rows(self):
        corpus=Corpus(self.root/'wal.sqlite3')
        with corpus.connect() as db:
            db.execute('CREATE TABLE wal_probe(value TEXT)');db.execute("INSERT INTO wal_probe VALUES ('committed')");db.commit()
            # Connection remains open while online backup captures its WAL state.
            destination=self.root/'snapshot.sqlite3';corpus.repository.snapshot(destination)
            with closing(sqlite3.connect(destination)) as copied:
                self.assertEqual(copied.execute('SELECT value FROM wal_probe').fetchone()[0],'committed')
            self.assertEqual(list(self.root.glob('catalog-snapshot-*')),[])
