import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
from image_codec import ImageOptions, prepare_image, pack_codes, PALETTE


class BlackWhiteTests(unittest.TestCase):
    def test_scale_centers_small_image_and_crops_large_image(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'black.png'; Image.new('RGB',(552,768),'black').save(p)
            small=prepare_image(p,ImageOptions(scale_percent=50))
            self.assertEqual(small.preview_rgb.getpixel((0,0)),(255,255,255))
            self.assertEqual(small.preview_rgb.getpixel((276,384)),(0,0,0))
            large=prepare_image(p,ImageOptions(scale_percent=200))
            self.assertEqual(set(large.codes),{0})
            self.assertEqual(len(small.packed),105984)
            with self.assertRaises(ValueError): prepare_image(p,ImageOptions(scale_percent=0))

    def test_black_white_keeps_panel_codes_and_preview_in_sync(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'colors.png'
            image=Image.new('RGB',(552,768),'red')
            image.paste('yellow',(276,0,552,768)); image.save(p)
            for dither in (False,True):
                result=prepare_image(p,ImageOptions(black_white=True,dither=dither))
                self.assertLessEqual(set(result.codes),{0,1})
                self.assertLessEqual(set(result.preview_rgb.getdata()),{PALETTE[0],PALETTE[1]})
                self.assertEqual(len(result.packed),105984)
                self.assertEqual(result.packed,pack_codes(result.codes))
                self.assertEqual(result.source_rgb.getpixel((0,0)),(255,0,0))

    def test_gray_dithering_and_brightness(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'gray.png'; Image.new('RGB',(552,768),(110,110,110)).save(p)
            plain=prepare_image(p,ImageOptions(black_white=True))
            bright=prepare_image(p,ImageOptions(black_white=True,brightness=1.5))
            dithered=prepare_image(p,ImageOptions(black_white=True,dither=True))
            self.assertEqual(set(plain.codes),{0})
            self.assertEqual(set(bright.codes),{1})
            self.assertEqual(set(dithered.codes),{0,1})


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
