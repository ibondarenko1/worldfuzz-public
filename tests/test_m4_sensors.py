import base64
from io import BytesIO
import struct
import unittest

from mcap.writer import CompressionType, Writer
from worldfuzz.platform.contracts import Fault
from worldfuzz.platform.mcap_records import parse_mcap


def ros1_recording():
    output=BytesIO();writer=Writer(output,compression=CompressionType.NONE);writer.start(profile='ros1')
    schema=writer.register_schema('std_msgs/String','ros1msg',b'string data\n')
    channel=writer.register_channel('/chatter','ros1',schema)
    payload=b'published sample';writer.add_message(channel,123456789,struct.pack('<I',len(payload))+payload,123456700)
    writer.finish();return base64.b64encode(output.getvalue()).decode('ascii')


class McapContractTests(unittest.TestCase):
    def test_ros1_timestamps_schema_and_payload_hash_are_preserved(self):
        result=parse_mcap(ros1_recording(),'ros1')
        self.assertEqual(result['schemas'][0]['encoding'],'ros1msg')
        self.assertEqual(result['channels'][0]['message_encoding'],'ros1')
        self.assertEqual(result['messages'][0]['log_time_ns'],123456789)
        self.assertEqual(result['messages'][0]['publish_time_ns'],123456700)

    def test_declared_ros_version_mismatch_is_rejected(self):
        with self.assertRaises(Fault) as caught:parse_mcap(ros1_recording(),'ros2')
        self.assertEqual(caught.exception.code,'PROFILE_INCOMPATIBLE')


if __name__=='__main__':unittest.main()
