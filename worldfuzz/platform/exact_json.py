"""Versioned transport projection; never modifies stored bytes or their hashes."""
from copy import deepcopy

CONTRACT = {'version': 'nanoseconds-decimal.v1', 'encoding': 'base10_integer_string',
            'unit': 'ns', 'hash_scope': 'stored hashes identify original bytes, not this transport projection'}
DECIMAL_NS = {'type': 'string', 'pattern': '-?(0|[1-9][0-9]*)', 'maxLength': 40}


def project(value, field=''):
    if isinstance(value, dict):
        return {k: project(v, k if field != 'time_range_ns' else 'timestamp_ns') for k, v in value.items()}
    if isinstance(value, list):
        return [project(v, field) for v in value]
    if type(value) is int and field.endswith('_ns'):
        return str(value)
    return deepcopy(value)
