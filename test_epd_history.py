import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
from image_codec import ImageOptions,prepare_image
from epd_history import RefreshHistory


class HistoryTests(unittest.TestCase):
    def test_persist_recent_five_and_restore_exact_pixels(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'input.png'; Image.new('RGB',(552,768),'red').save(p)
            prepared=prepare_image(p,ImageOptions(scale_percent=50))
            history=RefreshHistory(Path(d)/'history.json')
            for i in range(6): history.add(prepared,str(i))
            restored=RefreshHistory(history.path)
            self.assertEqual([r['label'] for r in restored.entries],['5','4','3','2','1'])
            loaded=restored.load(0)
            self.assertEqual(loaded.codes,prepared.codes)
            self.assertEqual(loaded.packed,prepared.packed)
            self.assertEqual(loaded.preview_rgb.tobytes(),prepared.preview_rgb.tobytes())
            self.assertEqual(loaded.options,prepared.options)

    def test_invalid_history_is_reported(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'history.json'; p.write_text('{bad')
            with self.assertRaises(ValueError): RefreshHistory(p)
