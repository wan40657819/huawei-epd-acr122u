from test_fixtures import TEST_PIN
import unittest
from PIL import Image
from image_codec import ImageOptions,PreparedImage
from epd_protocol import TransferPlan
from epd_gui import PreviewState

class PreviewTests(unittest.TestCase):
    def test_versions_and_snapshot(self):
        s=PreviewState(); first=s.request_preview(); second=s.request_preview()
        im=Image.new('RGB',(552,768),'white')
        p=PreparedImage(im,im,b'\x01'*(552*768),b'\x55'*105984,'hash',ImageOptions())
        self.assertFalse(s.accept_preview(first,p))
        with self.assertRaises(ValueError): s.snapshot('COM17',None,TEST_PIN)
        self.assertTrue(s.accept_preview(second,p))
        plan=TransferPlan((),'',0,53)
        job=s.snapshot('COM17',plan,TEST_PIN)
        with self.assertRaises(ValueError): s.request_preview()
        self.assertEqual(job.prepared.packed,p.packed)
        s.busy=False
        s.request_preview()
        self.assertEqual(job.prepared.packed,p.packed)

    def test_detect_during_preview_retains_result(self):
        s=PreviewState(); version=s.request_preview(); s.busy=True
        self.assertFalse(s.accept_preview(version,'prepared'))
        s.busy=False
        self.assertTrue(s.resume_preview())
        self.assertTrue(s.ready)
        self.assertEqual(s.prepared,'prepared')
