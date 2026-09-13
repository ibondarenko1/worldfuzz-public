"""Corpus data operations must not depend on a SQL connection API."""
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from worldfuzz.corpus import Corpus,import_navigation
from worldfuzz.storage import SQLiteRepository

ROOT=Path(__file__).resolve().parents[1]

class NoSQLConnection:
    def __init__(self,path):self.inner=SQLiteRepository(path);self.path=path
    def initialize(self):return self.inner.initialize()
    def connect(self):raise AssertionError('Corpus leaked SQL outside repository')
    def ingest_records(self,*args):return self.inner.ingest_records(*args)
    def catalog_listing(self):return self.inner.catalog_listing()
    def export_records(self,**args):return self.inner.export_records(**args)

class RepositoryBoundaryTests(unittest.TestCase):
    def test_published_import_listing_export_use_repository_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Corpus(repository=NoSQLConnection(Path(tmp)/'catalog.sqlite3'))
            first=import_navigation(c,ROOT/'examples/published-street-cases-20260910')
            self.assertEqual(first['added'],9)
            before=c.export('navigation')
            self.assertEqual(import_navigation(c,ROOT/'examples/published-street-cases-20260910')['added'],0)
            self.assertEqual(before,c.export('navigation'))

    def test_concurrent_duplicate_imports_are_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'catalog.sqlite3';Corpus(path)
            def load(_):return import_navigation(Corpus(path),ROOT/'examples/published-street-cases-20260910')['added']
            with ThreadPoolExecutor(max_workers=2) as pool:added=list(pool.map(load,range(2)))
            self.assertEqual(sorted(added),[0,9]);self.assertEqual(len(Corpus(path).listing()['episodes']),9)
