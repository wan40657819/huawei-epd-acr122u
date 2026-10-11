import unittest
from protocol import crc_a, validate_bcc, validate_crc, cascade_piece, parse_ats


class ProtocolTests(unittest.TestCase):
    def test_crc_known_vectors(self):
        for message, expected in [('5000', '57cd'), ('e080', '3173'),
                                  ('d01105', 'fff1')]:
            self.assertEqual(crc_a(bytes.fromhex(message)), bytes.fromhex(expected))

    def test_bcc_real_previous_uid(self):
        self.assertEqual(validate_bcc(bytes.fromhex('d2d78152d6')), bytes.fromhex('d2d78152'))

    def test_bcc_cascade_tag(self):
        self.assertEqual(validate_bcc(bytes.fromhex('8801020388')), bytes.fromhex('88010203'))

    def test_reject_bcc_and_length_errors(self):
        for raw in ['d2d78152d7', 'd2d78152', 'd2d78152d600']:
            with self.assertRaises(ValueError):
                validate_bcc(bytes.fromhex(raw))

    def test_crc_reject_corrupt_or_short(self):
        self.assertEqual(validate_crc(bytes.fromhex('e0803173')), bytes.fromhex('e080'))
        for raw in ['e0803172', '', '00', '0000']:
            with self.assertRaises(ValueError):
                validate_crc(bytes.fromhex(raw))

    def test_cascade_uid(self):
        first = cascade_piece(bytes.fromhex('88010203'), 0x04, 1)
        second = cascade_piece(bytes.fromhex('04050607'), 0x20, 2)
        self.assertEqual(first + second, bytes.fromhex('01020304050607'))
        middle = cascade_piece(bytes.fromhex('88040506'), 0x04, 2)
        last = cascade_piece(bytes.fromhex('0708090a'), 0x20, 3)
        self.assertEqual(first + middle + last, bytes(range(1, 11)))

    def test_reject_cascade_inconsistency(self):
        for raw, sak, level in [('88010203', 0x20, 1), ('01020304', 4, 1),
                                ('88010203', 4, 3)]:
            with self.assertRaises(ValueError):
                cascade_piece(bytes.fromhex(raw), sak, level)

    def test_ats_full_interfaces(self):
        parsed = parse_ats(bytes.fromhex('0b7833a002901254715203'))
        self.assertEqual((parsed['tl'], parsed['t0'], parsed['ta'], parsed['tb'], parsed['tc']),
                         (11, 0x78, 0x33, 0xa0, 2))
        self.assertEqual(parsed['historical'], '90 12 54 71 52 03')
        self.assertEqual((parsed['fsc'], parsed['fwi'], parsed['sfgi']), (256, 10, 0))
        self.assertTrue(parsed['cid_supported'])
        self.assertFalse(parsed['nad_supported'])

    def test_ats_defaults_and_optional_fields(self):
        parsed = parse_ats(b'\x01')
        self.assertEqual((parsed['fsc'], parsed['fwi']), (32, 4))
        self.assertIsNone(parsed['t0'])
        self.assertEqual(parse_ats(bytes.fromhex('034802'))['tc'], 2)
        self.assertEqual(parse_ats(bytes.fromhex('0208'))['historical'], '')

    def test_ats_invalid_length_optional_or_reserved_values(self):
        for raw in ['', '00', '0278', '0378aa', '027f', '0209', '0328f0', '03280f', '0180']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_ats(bytes.fromhex(raw))


if __name__ == '__main__':
    unittest.main()
