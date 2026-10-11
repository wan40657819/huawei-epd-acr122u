import unittest
from unittest.mock import patch
import wakeup_probe as probe
from test_transport_and_stages import ByteStream

class FullInitTests(unittest.TestCase):
    def test_full_init_bytes_and_cleanup(self):
        stream=ByteStream(bytes.fromhex('0000ff00ff00 0000ff02fed5151600'))
        stream.close=lambda: setattr(stream,'closed',True)
        with patch.object(probe.serial,'Serial',return_value=stream),patch.object(probe,'log'):
            r=probe.Reader('fake',full_init=True)
        self.assertEqual(stream.written[0],bytes.fromhex('55550000000000000000000000000000 0000ff05fbd4140114000300'))
        self.assertFalse(r.rf_on)
        stream=ByteStream(bytes.fromhex('0000ff00ff00 0000ff02fed5171400'))
        stream.close=lambda: setattr(stream,'closed',True)
        with patch.object(probe.serial,'Serial',return_value=stream),patch.object(probe,'log'):
            with self.assertRaises(RuntimeError): probe.Reader('fake',full_init=True)
        self.assertTrue(stream.closed)

    def test_command_deadline_does_not_send_after_expiry(self):
        r=probe.Reader.__new__(probe.Reader); r.s=ByteStream(b'')
        r.deadline=0
        with self.assertRaises(TimeoutError): r.command(2)
        self.assertFalse(r.s.written)

    def test_external_deadline_propagates(self):
        from iso_dep import IsoDep,SessionError
        from unittest.mock import Mock
        engine=IsoDep(Mock(),fsc=256,fsd=256,fwt_ms=309)
        with self.assertRaises(SessionError): engine.exchange(b'test',deadline=0)

    def test_full_init_requires_ack(self):
        stream=ByteStream(bytes.fromhex('0000ff02fed5151600'))
        stream.close=lambda: setattr(stream,'closed',True)
        with patch.object(probe.serial,'Serial',return_value=stream),patch.object(probe,'log'):
            with self.assertRaises(RuntimeError): probe.Reader('fake',full_init=True)
        self.assertTrue(stream.closed)

    def test_write_and_ack_use_absolute_deadline(self):
        clock=[10.0]
        class Stream(ByteStream):
            timeout=.05
            write_timeout=1
            def write(self,data):
                self.last_write_budget=self.write_timeout
                clock[0]+=.02
                super().write(data)
        stream=Stream(bytes.fromhex('0000ff00ff00 0000ff06fad50332010607e800'))
        r=probe.Reader.__new__(probe.Reader); r.s=stream; r.deadline=10.1
        with patch('wakeup_probe.time.monotonic',side_effect=lambda:clock[0]),patch.object(probe,'log'):
            self.assertEqual(r.command(2),bytes.fromhex('32010607'))
        self.assertLessEqual(stream.last_write_budget,.08+1e-8)
        self.assertEqual(stream.write_timeout,1)
