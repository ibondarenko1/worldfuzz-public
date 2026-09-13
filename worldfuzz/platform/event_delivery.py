"""Signed pull delivery and an atomic local consumer. No outbound IO."""
from contextlib import closing
import hashlib
import hmac
import os
import sqlite3
import time

from ..corpus import encoded, digest
from .contracts import Fault
from .webhooks import read


def signing_key():
    value = os.environ.get('WORLDFUZZ_WEBHOOK_SIGNING_KEY', '')
    if len(value.encode()) < 32:
        raise Fault('EVENT_SIGNING_UNAVAILABLE', 'Configure a runtime webhook key of at least 32 bytes')
    return value.encode()


def delivery_id(payload):
    return digest([payload['workspace_id'], payload['webhook_id'], payload['event']['seq']])


def sign_events(service, webhook_id, after=0, limit=25):
    batch = read(service, webhook_id, after, limit)
    key = signing_key()
    result = []
    for event in batch['events']:
        payload = {'schema_version': '1.0.0', 'workspace_id': service.authorization.workspace_id,
                   'webhook_id': webhook_id, 'event': event}
        signed = {'delivery_id': delivery_id(payload), 'payload': payload,
                  'signed_at': int(service.clock())}
        signed['signature'] = hmac.new(key, encoded(signed).encode(), hashlib.sha256).hexdigest()
        result.append(signed)
    return {'webhook_id': webhook_id, 'events': result, 'next_cursor': batch['next_cursor'],
            'delivery': 'authenticated_signed_pull', 'external_delivery': False}


class EventInbox:
    """Commit a delivery and a callback's local SQLite effect in one transaction.

    A callback receives (connection, payload). It must use this connection only
    for its durable effect. Network side effects need their own transactional
    outbox; this class does not promise exactly-once external delivery.
    """
    def __init__(self, path, key, workspace_id, webhook_id, *, clock=time.time, max_age=300):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError('Use at least 32 bytes of runtime key material')
        self.path, self.key = str(path), key
        self.workspace_id, self.webhook_id = workspace_id, webhook_id
        self.clock, self.max_age = clock, max_age
        # sqlite3.Connection's context manager commits or rolls back but does
        # not close the handle. Close it explicitly so disposable inbox files
        # can be removed immediately on Windows.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS received_events '
                       '(delivery_id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL, '
                       'payload TEXT NOT NULL, received_at REAL NOT NULL)')

    def receive(self, envelope, apply=None):
        if not isinstance(envelope, dict) or set(envelope) != {'delivery_id','payload','signed_at','signature'}:
            raise Fault('EVENT_INVALID', 'Invalid signed envelope')
        signed = {k: envelope[k] for k in ('delivery_id','payload','signed_at')}
        if type(signed['signed_at']) is not int or not isinstance(envelope['signature'], str):
            raise Fault('EVENT_INVALID', 'Invalid event time or signature')
        raw = encoded(signed).encode()
        if len(raw) > 65536:
            raise Fault('EVENT_INVALID', 'Event exceeds 64 KiB')
        expected = hmac.new(self.key, raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(envelope['signature'], expected):
            raise Fault('EVENT_SIGNATURE_INVALID', 'Event signature did not match')
        age = self.clock() - signed['signed_at']
        if not -30 <= age <= self.max_age:
            raise Fault('EVENT_EXPIRED', 'Request a newly signed delivery')
        payload = signed['payload']
        if (not isinstance(payload, dict) or set(payload) != {'schema_version','workspace_id','webhook_id','event'}
                or payload['schema_version'] != '1.0.0' or not isinstance(payload['event'], dict)):
            raise Fault('EVENT_INVALID', 'Unsupported payload shape')
        if payload['workspace_id'] != self.workspace_id or payload['webhook_id'] != self.webhook_id:
            raise Fault('ACCESS_DENIED', 'Delivery belongs to another subscription or workspace')
        if type(payload['event'].get('seq')) is not int:
            raise Fault('EVENT_INVALID', 'Missing event sequence')
        if signed['delivery_id'] != delivery_id(payload):
            raise Fault('EVENT_INVALID', 'Delivery identity differs from its signed event')
        fingerprint = digest(payload)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT payload_sha256 FROM received_events WHERE delivery_id=?',
                               (signed['delivery_id'],)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise Fault('EVENT_CONFLICT', 'Previously accepted identity has different content')
                return {'delivery_id': signed['delivery_id'], 'accepted': False, 'duplicate': True}
            if apply is not None:
                apply(db, payload)
            db.execute('INSERT INTO received_events VALUES(?,?,?,?)',
                       (signed['delivery_id'], fingerprint, encoded(payload), self.clock()))
        return {'delivery_id': signed['delivery_id'], 'accepted': True, 'duplicate': False}
