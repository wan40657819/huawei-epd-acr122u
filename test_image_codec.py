import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
from image_codec import ImageOptions, prepare_image, pack_codes, PALETTE


class ImageTests(unittest.TestCase):
    def test_scan_rotate_msb(self):
        # 2 columns x 4 rows, rotated 180; left column becomes [3,2,1,0].
        self.assertEqual(pack_codes(bytes([3,0,2,1,1,2,0,3]), 2, 4), bytes([0xe4,0x1b]))

    def test_full_panel_generated_four_colour_stripes(self):
        # Independent known result: each vertical band packs to one constant byte.
        codes=bytes(x//138 for y in range(768) for x in range(552))
        packed=pack_codes(codes)
        expected=b''.join(bytes((value,))*(138*192) for value in (0xff,0xaa,0x55,0x00))
        self.assertEqual(len(packed),105984)
        self.assertEqual(packed,expected)

    def test_transparency_options_and_size(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'input.png'
            Image.new('RGBA',(8,8),(0,0,0,0)).save(p)
            result=prepare_image(p,ImageOptions())
            self.assertEqual(result.preview_rgb.size,(552,768))
            self.assertEqual(set(result.codes),{1})
            self.assertEqual(len(result.packed),105984)
            self.assertEqual(len(result.source_sha256),64)
            with self.assertRaises(ValueError): prepare_image(p,ImageOptions(brightness=2))
            p.write_bytes(b'bad image')
            with self.assertRaises(Exception): prepare_image(p,ImageOptions())

    def test_fit_rotation_and_palette(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'input.png'
            Image.new('RGB',(300,40),PALETTE[3]).save(p)
            contain=prepare_image(p,ImageOptions())
            cover=prepare_image(p,ImageOptions(fit='cover',rotation=90,dither=True))
            self.assertIn(1,contain.codes)
            self.assertEqual(set(cover.codes),{3})
            with self.assertRaises(ValueError): pack_codes(b'\x04'*8,2,4)

    def test_exif(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'input.jpg'
            exif=Image.Exif(); exif[274]=6
            Image.new('RGB',(20,10),'white').save(p,exif=exif)
            self.assertEqual(prepare_image(p,ImageOptions()).source_rgb.size,(10,20))
