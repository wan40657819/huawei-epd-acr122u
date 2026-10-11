import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
from image_codec import prepare_image,ImageOptions
from epd_protocol import build_transfer_plan
from epd_export import export_job

class ExportTests(unittest.TestCase):
    def test_export_offline(self):
        with TemporaryDirectory() as d:
            root=Path(d); p=root/'source.png'; Image.new('RGB',(30,20),'white').save(p)
            prepared=prepare_image(p,ImageOptions()); plan=build_transfer_plan(prepared.packed)
            target=export_job(prepared,plan,root/'out')
            self.assertEqual((target/'packed.bin').read_bytes(),prepared.packed)
            summary=json.loads((target/'job-summary.json').read_text())
            self.assertEqual(summary['mode'],'offline')
            self.assertEqual(summary['packed_sha256'],hashlib.sha256(prepared.packed).hexdigest())
            self.assertEqual(len(json.loads((target/'apdus.json').read_text())),len(plan.packets))
            self.assertEqual(len(list((target/'blocks').glob('*.lzo'))),53)
            no_logs=export_job(prepared,plan,root/'no-logs',save_logs=False)
            self.assertTrue((no_logs/'packed.bin').exists())
            self.assertFalse((no_logs/'events.txt').exists())
            self.assertFalse((no_logs/'events.json').exists())
