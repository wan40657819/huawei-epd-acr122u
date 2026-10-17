"""Synthetic ISO-DEP peers: no hardware and no claim about target application data."""
import unittest
from unittest.mock import patch
from protocol import crc_a
from iso_dep import Block, decode, encode, IsoDep, ProtocolError, FrameError, LinkTimeout, SessionError


def frame(raw):
    data = bytes.fromhex(raw)
    return data + crc_a(data)


class Peer:
    rf_on = True
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []
    def transceive(self, data, timeout_ms):
        self.sent.append((data, timeout_ms))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class CodecTests(unittest.TestCase):
    def test_i_r_s_roundtrips(self):
        for block in [Block('I', bn=0, inf=b'abc'), Block('I', bn=1, chaining=True),
                      Block('I', bn=1, cid=0, nad=0x12, inf=b'x'),
                      Block('R', bn=0), Block('R', bn=1, nak=True, cid=3),
                      Block('WTX', inf=b'\x3b'), Block('DESELECT', cid=0)]:
            self.assertEqual(decode(encode(block)), block)
    def test_known_pcbs(self):
        self.assertEqual(encode(Block('I', bn=0, inf=b'\x55')), frame('0255'))
        self.assertEqual(encode(Block('R', bn=1)), frame('a3'))
        self.assertEqual(encode(Block('R', bn=0, nak=True)), frame('b2'))
        self.assertEqual(encode(Block('WTX', inf=b'\x02')), frame('f202'))
    def test_bad_crc(self):
        with self.assertRaises(FrameError): decode(frame('0255')[:-1]+b'\x00')
    def test_malformed_frames(self):
        for raw in ['00', '22', '82', 'a255', 'c200', 'f2', 'f200', 'f23c', 'f23f', '0a', '06', '0a0f', 'f301']:
            with self.subTest(raw=raw), self.assertRaises(ProtocolError): decode(frame(raw))
    def test_wtx_power_bits_preserved(self):
        self.assertEqual(decode(frame('f282')).inf, b'\x82')

    def test_cid_power_bits_are_not_cid_value(self):
        block=decode(frame('0a8041'))
        self.assertEqual((block.cid,block.cid_power,block.inf),(0,2,b'A'))
        with self.assertRaises(ProtocolError): decode(frame('0a2041'))
    def test_encoder_rejects_invalid_bn_cid_and_control_inf(self):
        for block in [Block('I', bn=2), Block('I', cid=15), Block('R', inf=b'x'), Block('WTX', inf=b'\x00')]:
            with self.assertRaises((ValueError,ProtocolError)): encode(block)


class StateTests(unittest.TestCase):
    def test_wtx_counter_resets_for_each_exchange(self):
        peer,dep=self.engine([frame('f201')]*50+[frame('0241')]+[frame('f201')]*50+[frame('0342')])
        self.assertEqual(dep.exchange(b'x'),b'A')
        self.assertEqual(dep.exchange(b'y'),b'B')
        self.assertEqual(dep._wtx,50)

    def test_zero_wtx_budget_does_not_echo_first_wtx(self):
        peer,dep=self.engine([frame('f201')],max_wtx=0)
        with self.assertRaisesRegex(SessionError,'WTX budget exceeded'): dep.exchange(b'x')
        self.assertEqual(len(peer.sent),1)

    def test_late_response_is_not_accepted_after_deadline(self):
        peer,dep=self.engine([frame('0241')])
        now=[100.0]
        original=peer.transceive
        def late(data,timeout_ms):
            now[0]=102.0
            return original(data,timeout_ms)
        peer.transceive=late
        with patch('iso_dep.time.monotonic',side_effect=lambda:now[0]):
            with self.assertRaisesRegex(SessionError,'time budget'): dep.exchange(b'x',deadline=101.0)
        self.assertFalse(dep.active)

    def test_default_wtx_budget_accepts_50_and_rejects_51(self):
        peer,dep=self.engine([frame('f201')]*50+[frame('0241')])
        self.assertEqual(dep.max_wtx,50)
        self.assertEqual(dep.exchange(b'x'),b'A')
        peer,dep=self.engine([frame('f201')]*51)
        with self.assertRaisesRegex(SessionError,'WTX budget exceeded'): dep.exchange(b'x')
        self.assertFalse(dep.active)

    def test_short_rf_segments_preserve_full_257_byte_application(self):
        peer=Peer([frame('a2'),frame('039000')])
        dep=IsoDep(peer,fsc=256,fsd=256,fwt_ms=309.314,tx_inf_limit=240)
        data=bytes(range(256))+b'Z'
        self.assertEqual(dep.exchange(data),bytes.fromhex('9000'))
        self.assertEqual([len(v[0]) for v in peer.sent],[243,20])
        self.assertEqual(b''.join(decode(v[0]).inf for v in peer.sent),data)
        self.assertEqual([v[0][0] for v in peer.sent],[0x12,3])

    def engine(self, responses, **kw):
        peer = Peer(responses)
        return peer, IsoDep(peer, fsc=256, fsd=256, fwt_ms=309.314, **kw)
    def test_consecutive_exchanges_toggle_number(self):
        peer, dep = self.engine([frame('0241'), frame('0342'), frame('0243')])
        self.assertEqual([dep.exchange(b'x') for _ in range(3)], [b'A',b'B',b'C'])
        self.assertEqual([x[0][0] for x in peer.sent], [2,3,2])
    def test_outgoing_chaining_at_fsc_boundary(self):
        peer, dep = self.engine([frame('a2'),frame('035a')])
        self.assertEqual(dep.exchange(b'x'*254), b'Z')
        self.assertEqual([len(x[0]) for x in peer.sent], [256,4])
        self.assertEqual([decode(x[0]).bn for x in peer.sent], [0,1])
    def test_incoming_chaining_and_duplicate(self):
        peer, dep = self.engine([frame('1241'),frame('1241'),frame('0342')])
        self.assertEqual(dep.exchange(b'x'), b'AB')
        self.assertEqual([x[0][0] for x in peer.sent], [2,0xa3,0xa3])
        self.assertEqual(dep.block_number, 0)
    def test_outgoing_and_incoming_chaining(self):
        peer, dep = self.engine([frame('a2'), frame('1341'), frame('0242')])
        self.assertEqual(dep.exchange(b'x'*254), b'AB')
        self.assertEqual([x[0][0] for x in peer.sent], [0x12,3,0xa2])
    def test_wtx_then_response_and_timeout_restored(self):
        peer, dep = self.engine([frame('f23b'), frame('0241'), frame('0342')])
        self.assertEqual(dep.exchange(b'x'), b'A')
        self.assertEqual(dep.exchange(b'y'), b'B')
        self.assertEqual(peer.sent[1][0], frame('f23b'))
        self.assertGreater(peer.sent[1][1], 4900)
        self.assertLess(peer.sent[1][1], 5000)  # FWT_TEMP is capped at FWT_MAX
        self.assertLess(peer.sent[2][1], 400)
    def test_wtx_power_bits_not_echoed(self):
        peer, dep = self.engine([frame('f282'),frame('0241')])
        dep.exchange(b'x')
        self.assertEqual(peer.sent[1][0],frame('f202'))
    def test_timeout_queries_with_r_nak(self):
        peer, dep = self.engine([LinkTimeout('mute'),frame('0241')])
        self.assertEqual(dep.exchange(b'x'), b'A')
        self.assertEqual(peer.sent[1][0],frame('b2'))
    def test_bad_crc_queries_r_nak(self):
        peer, dep = self.engine([b'\x02\x00\x00', frame('0241')])
        self.assertEqual(dep.exchange(b'x'),b'A')
        self.assertEqual(peer.sent[1][0],frame('b2'))
    def test_chain_timeout_repeats_r_ack(self):
        peer, dep = self.engine([frame('1241'),LinkTimeout('mute'),frame('0342')])
        self.assertEqual(dep.exchange(b'x'),b'AB')
        self.assertEqual(peer.sent[1][0],peer.sent[2][0])
    def test_ack_opposite_bn_requests_exact_retransmission(self):
        peer, dep = self.engine([frame('a3'),frame('0241')])
        dep.exchange(b'x')
        self.assertEqual(peer.sent[0][0],peer.sent[1][0])
    def test_nak_current_bn_retransmits(self):
        peer, dep = self.engine([frame('b2'),frame('0241')])
        dep.exchange(b'x')
        self.assertEqual(peer.sent[0][0],peer.sent[1][0])
    def test_no_progress_and_wtx_budgets(self):
        for responses,kw in [([frame('a3')]*4, {'max_retries':2}),
                             ([frame('f201')]*4, {'max_wtx':2}),
                             ([LinkTimeout('mute')]*4, {'max_retries':2})]:
            peer, dep = self.engine(responses, **kw)
            with self.assertRaises(SessionError): dep.exchange(b'x')
            self.assertFalse(dep.active)
            with self.assertRaises(SessionError): dep.exchange(b'x')
            self.assertTrue(peer.rf_on)
    def test_unexpected_bn_cid_nad_or_s_block_invalidates(self):
        for raw in ['0341','0a0041','060041','c2','a2']:
            peer, dep = self.engine([frame(raw)])
            with self.subTest(raw=raw),self.assertRaises(SessionError): dep.exchange(b'x')
            self.assertFalse(dep.active)
    def test_wrong_duplicate_content_rejected(self):
        peer, dep = self.engine([frame('1241'),frame('1242')])
        with self.assertRaises(SessionError): dep.exchange(b'x')
    def test_cid_uses_one_more_overhead_byte(self):
        peer, dep = self.engine([frame('aa00'),frame('0b0041')], cid=0)
        self.assertEqual(dep.exchange(b'x'*253),b'A')
        self.assertEqual([len(x[0]) for x in peer.sent],[256,5])

    def test_cid_power_change_does_not_duplicate_payload(self):
        peer, dep=self.engine([frame('1a4041'),frame('1a8041'),frame('0b0042')],cid=0)
        self.assertEqual(dep.exchange(b'x'),b'AB')
    def test_oversized_received_frame(self):
        peer, dep = self.engine([encode(Block('I',inf=b'x'*254))])
        with self.assertRaises(SessionError): dep.exchange(b'x')
    def test_empty_inf_is_transport_data_not_presence_probe(self):
        peer, dep = self.engine([frame('02')])
        self.assertEqual(dep.exchange(b''),b'')
        self.assertEqual(peer.sent[0][0],frame('02'))

    def test_transport_budgets_must_be_finite_and_integral(self):
        for kw in [dict(exchange_timeout_s=float('inf')),dict(max_wtx=2.5),dict(max_retries=float('inf'))]:
            with self.assertRaises(ValueError): self.engine([],**kw)

    def test_exchange_deadline_and_frame_budget(self):
        for kw in [dict(exchange_timeout_s=0.01),dict(max_frames=1)]:
            peer,dep=self.engine([frame('f201'),frame('0241')],**kw)
            with self.assertRaises(SessionError): dep.exchange(b'x')
            self.assertFalse(dep.active)


if __name__ == '__main__': unittest.main()
