"""Offline tests only: serial is a byte-stream stub; no COM port is opened."""
import argparse
import unittest
from unittest.mock import patch
import wakeup_probe as probe
from protocol import crc_a


def response_frame(body):
    return bytes.fromhex('0000ff') + bytes([len(body), -len(body) & 255]) + body + bytes([-sum(body) & 255, 0])


class ByteStream:
    def __init__(self, incoming):
        self.incoming = bytearray(incoming)
        self.written = []

    @property
    def in_waiting(self):
        return min(2, len(self.incoming))  # exercise fragmented ACK and response

    def read(self, n):
        result = self.incoming[:n]
        del self.incoming[:n]
        return result

    def write(self, data):
        self.written.append(bytes(data))


class TransportTests(unittest.TestCase):
    def reader(self, incoming):
        reader = probe.Reader.__new__(probe.Reader)
        reader.s = ByteStream(incoming)
        return reader

    @patch.object(probe, 'log')
    def test_real_hsu_frame_fragmented(self, log):
        reader = self.reader(bytes.fromhex('0000ff00ff00 0000ff05fbd543000400e400'))
        self.assertEqual(reader.command(0x42, b'\x7a'), bytes.fromhex('000400'))
        self.assertEqual(reader.s.written[0], bytes.fromhex('0000ff03fdd4427a7000'))
        self.assertEqual(reader.s.written[-1], bytes.fromhex('0000ff00ff00'))

    @patch.object(probe, 'log')
    def test_ack_only_is_not_tag_response(self, log):
        reader = self.reader(bytes.fromhex('0000ff00ff00'))
        with self.assertRaises(TimeoutError):
            reader.command(0x42, b'\x7a', timeout=0.005)

    @patch.object(probe, 'log')
    def test_bad_lcs_dcs_postamble_and_response_code(self, log):
        good = bytes.fromhex('0000ff05fbd543000400e400')
        for index in (4, 10, 11):
            bad = bytearray(good)
            bad[index] ^= 1
            with self.subTest(index=index), self.assertRaises(RuntimeError):
                self.reader(bad).command(0x42, b'\x7a')
        with self.assertRaises(RuntimeError):
            self.reader(response_frame(bytes.fromhex('d54500'))).command(0x42, b'\x7a')

    @patch.object(probe, 'log')
    def test_extended_hsu_send_and_receive(self, log):
        payload = bytes(range(256))
        body = b'\xd5\x43\x00' + payload
        hi, lo = divmod(len(body), 256)
        incoming = bytes.fromhex('0000ffffff') + bytes([hi, lo, -(hi+lo)&255]) + body + bytes([-sum(body)&255, 0])
        reader = self.reader(bytes.fromhex('0000ff00ff00') + incoming)
        self.assertEqual(reader.command(0x42, payload), b'\x00' + payload)
        self.assertEqual(reader.s.written[0][:8], bytes.fromhex('0000ffffff0102fd'))
        self.assertEqual(sum(reader.s.written[0][8:-1]) & 255, 0)

    @patch.object(probe, 'log')
    def test_extended_bad_length_checksum(self, log):
        with self.assertRaises(RuntimeError):
            self.reader(bytes.fromhex('0000ffffff0103fd')).command(2, timeout=0.01)


class StageTests(unittest.TestCase):
    def run_exchange(self, responses, stage='ats'):
        args = argparse.Namespace(stage=stage)
        context = patch.object(probe, 'rf_transceive', side_effect=responses)
        return context, args

    @patch.object(probe, 'log')
    def test_real_complete_session_transcript(self, log):
        ctx, args = self.run_exchange([bytes.fromhex(s) for s in
            ['0400', '1254715265', '28b4fc', '0b7833a002901254715203cc82']])
        with ctx as exchange:
            probe.activation(object(), args)
        self.assertEqual([call.args[1].hex() for call in exchange.call_args_list],
                         ['7a', '9320', '9370125471526546d2', 'e0803173'])
        self.assertTrue(log.call_args.kwargs['iso_dep_activated'])

    @patch.object(probe, 'log')
    def test_bad_bcc_stops_before_select(self, log):
        ctx, args = self.run_exchange([bytes.fromhex('0400'), bytes.fromhex('1254715264')])
        with ctx as exchange, self.assertRaises(ValueError):
            probe.activation(object(), args)
        self.assertEqual(exchange.call_count, 2)

    @patch.object(probe, 'log')
    def test_bad_sak_crc_stops_before_rats(self, log):
        ctx, args = self.run_exchange([bytes.fromhex(s) for s in ['0400', '1254715265', '28b4fd']])
        with ctx as exchange, self.assertRaises(ValueError):
            probe.activation(object(), args)
        self.assertEqual(exchange.call_count, 3)

    @patch.object(probe, 'log')
    def test_no_iso_dep_bit_stops_before_rats(self, log):
        sak = b'\x08'
        ctx, args = self.run_exchange([bytes.fromhex('0400'), bytes.fromhex('1254715265'), sak + crc_a(sak)])
        with ctx as exchange, self.assertRaises(ValueError):
            probe.activation(object(), args)
        self.assertEqual(exchange.call_count, 3)

    @patch.object(probe, 'log')
    def test_two_cascade_levels_select_dynamic_uid(self, log):
        responses = [bytes.fromhex('0400'), bytes.fromhex('8801020388'),
                     b'\x04' + crc_a(b'\x04'), bytes.fromhex('0405060700'),
                     b'\x20' + crc_a(b'\x20')]
        ctx, args = self.run_exchange(responses, 'select')
        with ctx as exchange:
            probe.activation(object(), args)
        self.assertEqual(exchange.call_args_list[3].args[1], bytes.fromhex('9520'))
        selected = [c.kwargs for c in log.call_args_list if c.args[0] == 'selected'][0]
        self.assertEqual(selected['uid'], '01 02 03 04 05 06 07')


if __name__ == '__main__':
    unittest.main()
