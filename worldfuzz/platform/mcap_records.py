"""Bounded MCAP ingestion. ROS payloads remain opaque recorded bytes."""
import base64
from io import BytesIO
import hashlib
from importlib import metadata

from .contracts import Fault


MAX_BYTES=2*1024*1024
MAX_MESSAGES=10000


def parse_mcap(text, ros_version):
    try:raw=base64.b64decode(text,validate=True)
    except Exception as exc:raise Fault('SCHEMA_INVALID','mcap_base64 is not canonical base64') from exc
    if base64.b64encode(raw).decode('ascii')!=text:
        raise Fault('SCHEMA_INVALID','mcap_base64 is not canonical base64')
    if not raw or len(raw)>MAX_BYTES:raise Fault('SCHEMA_INVALID','MCAP bytes exceed the 2 MiB registration limit')
    try:
        from mcap.reader import make_reader
        reader=make_reader(BytesIO(raw))
        messages=[];channels={};schemas={}
        for schema,channel,message in reader.iter_messages(log_time_order=False):
            if len(messages)>=MAX_MESSAGES:raise Fault('SCHEMA_INVALID','MCAP message count exceeds 10000')
            expected_schema='ros1msg' if ros_version=='ros1' else 'ros2msg'
            expected_message='ros1' if ros_version=='ros1' else 'cdr'
            if schema is None or schema.encoding!=expected_schema or channel.message_encoding!=expected_message:
                raise Fault('PROFILE_INCOMPATIBLE','MCAP channel does not match the declared ROS version')
            schemas[schema.id]={'id':schema.id,'name':schema.name,'encoding':schema.encoding,
                'definition_sha256':hashlib.sha256(schema.data).hexdigest(),'definition_bytes':len(schema.data)}
            channels[channel.id]={'id':channel.id,'topic':channel.topic,'message_encoding':channel.message_encoding,
                'schema_id':channel.schema_id,'metadata':dict(sorted(channel.metadata.items()))}
            messages.append({'sequence':message.sequence,'channel_id':message.channel_id,
                'log_time_ns':message.log_time,'publish_time_ns':message.publish_time,
                'data_sha256':hashlib.sha256(message.data).hexdigest(),'data_bytes':len(message.data)})
    except Fault:raise
    except Exception as exc:raise Fault('DATA_INVALID','MCAP could not be read: '+type(exc).__name__) from exc
    if not messages:raise Fault('SCHEMA_INVALID','MCAP contains no ROS messages')
    return {'format':'mcap','mcap_library':metadata.version('mcap'),'raw_sha256':hashlib.sha256(raw).hexdigest(),
        'raw_bytes':len(raw),'raw_base64':text,'ros_version':ros_version,
        'schemas':[schemas[key] for key in sorted(schemas)],'channels':[channels[key] for key in sorted(channels)],
        'messages':messages,'time_range_ns':{'first_log':min(m['log_time_ns'] for m in messages),
            'last_log':max(m['log_time_ns'] for m in messages)}}
