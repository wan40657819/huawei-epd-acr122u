"""Small recovered-loop fixtures; no dependency on IPA or private captures."""
import unittest
from image_codec import ImageOptions,atkinson_codes,normalize_dither,PALETTE


class AtkinsonTests(unittest.TestCase):
    def test_default_and_legacy_algorithm_values(self):
        self.assertEqual(ImageOptions().dither,'Atkinson')
        self.assertEqual(normalize_dither(True),'Floyd–Steinberg')
        self.assertEqual(normalize_dither(False),'关闭')
        with self.assertRaises(ValueError): normalize_dither('unknown')

    def test_recovered_rgb_fixture(self):
        rgb=bytes.fromhex('7331f82f1be0ec25fa0681f720612e66a2fcf55aeb8baa5fdbcd36cdadc45e801ac68d447f2c172d0c1f996f2095774d')
        self.assertEqual(atkinson_codes(rgb,4,4),bytes.fromhex('01000100000101030201000203000302'))

    def test_exact_palette_and_black_white(self):
        self.assertEqual(atkinson_codes(bytes(v for p in PALETTE for v in p),4,1),bytes(range(4)))
        codes=atkinson_codes(bytes([110]*3*16),4,4,palette=PALETTE[:2])
        self.assertEqual(set(codes),{0,1})

    def test_recovered_lower_left_boundary(self):
        rgb=bytes.fromhex('73a9bef499bbf4dc7bd2a4f2c8af5bd99f267a0ed31972172bba15')
        self.assertEqual(atkinson_codes(rgb,3,3),bytes.fromhex('010102010203000002'))
