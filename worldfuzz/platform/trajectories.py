"""Bounded TUM trajectory import. No interpolation or coordinate conversion."""
import hashlib
import math
from decimal import Decimal
from .contracts import Fault


def require_xyz_pair(asset):
    """Validate the operation's actual data semantics, also for legacy assets."""
    from .contracts import TRACK, validate
    adapter = asset.get('import_provenance', {}).get('adapter', 'direct.xyz.v1')
    if adapter not in ('direct.xyz.v1', 'tum.xyz.v1') or not all(k in asset for k in ('prediction', 'reference')):
        raise Fault('PROFILE_INCOMPATIBLE',
                    'evaluate_trajectory requires paired XYZ prediction/reference tracks; this asset is a recording, not a trajectory pair',
                    '$.asset_id', next_action='register_trajectory')
    if asset.get('units') != 'm' or not isinstance(asset.get('frame'), str) or not asset['frame'].strip():
        raise Fault('PROFILE_INCOMPATIBLE', 'XYZ evaluation requires an explicit shared frame and metre units; no conversion is inferred',
                    '$.asset_id', next_action='register_trajectory')
    for name in ('prediction', 'reference'):
        validate(asset[name], TRACK, '$.asset.' + name)
    if len(asset['prediction']) != len(asset['reference']):
        raise Fault('PROFILE_INCOMPATIBLE', 'Prediction/reference sample counts differ', next_action='register_trajectory')
    previous = -math.inf
    for prediction, reference in zip(asset['prediction'], asset['reference']):
        if prediction[0] != reference[0] or prediction[0] <= previous:
            raise Fault('PROFILE_INCOMPATIBLE', 'Prediction/reference timestamps must match and strictly increase; no alignment is inferred', next_action='register_trajectory')
        previous = prediction[0]
    return asset


def exact_timestamps(text):
    """Call after parse_tum validation. Compare source times before float rounding."""
    rows=(line.split('#',1)[0].split() for line in text.splitlines())
    return [Decimal(row[0]) for row in rows if row]


def parse_tum(text, field):
    points = []
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        path = f'$.{field}:line:{line_number}'
        try:
            values = [float(token) for token in line.split()]
        except ValueError:
            raise Fault('TRAJECTORY_INVALID', 'Expected numeric TUM columns', path)
        if len(values) != 8 or not all(math.isfinite(v) for v in values):
            raise Fault('TRAJECTORY_INVALID', 'Expected eight finite columns: timestamp tx ty tz qx qy qz qw', path)
        if abs(math.hypot(*values[4:]) - 1) > 0.001:
            raise Fault('TRAJECTORY_INVALID', 'Quaternion must be unit length within 0.001; no implicit normalization', path)
        if points and values[0] <= points[-1][0]:
            raise Fault('TRAJECTORY_INVALID', 'Timestamps must strictly increase', path)
        points.append(values[:4])
        if len(points) > 10000:
            raise Fault('TRAJECTORY_INVALID', 'At most 10000 poses per input', path)
    if len(points) < 2:
        raise Fault('TRAJECTORY_INVALID', 'At least two poses required', '$.' + field)
    return points, {'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                    'utf8_bytes': len(text.encode('utf-8')), 'poses': len(points), 'text': text}
