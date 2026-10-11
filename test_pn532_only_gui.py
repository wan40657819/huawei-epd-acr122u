"""PN532-only UI accepts previous settings without importing a PM3 backend."""
import json
import subprocess
import sys
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import epd_gui
from epd_writer import real_session


class PN532OnlyTests(unittest.TestCase):
    def test_gui_title_and_migrated_settings(self):
        with TemporaryDirectory() as d:
            base=Path(d)
            (base/'epd-gui-settings.json').write_text(json.dumps(dict(
                backend='PM3',port='COM10',backend_ports={'PN532':'COM17'},pm3_client='old.exe')))
            root=tk.Tk(); root.withdraw()
            try:
                with patch('epd_gui.BASE',base),patch.object(epd_gui.EpdApp,'refresh_ports'):
                    app=epd_gui.EpdApp(root)
                    self.assertEqual(root.title(),'华为3.98寸墨水屏图片工具')
                    self.assertEqual(app.title_label.cget('text'),'华为3.98寸墨水屏图片工具')
                    self.assertFalse(hasattr(app,'backend_control'))
                    self.assertFalse(hasattr(app,'client_control'))
                    self.assertIs(app.task_writer().session_factory,real_session)
                    app.save_settings()
                    saved=json.loads(app.config_path.read_text('utf-8'))
                    self.assertNotIn('pm3_client',saved)
                    self.assertNotIn('backend',saved)
                    self.assertFalse(app.file_logging.get())
                    self.assertIsNone(app.pin_error)
                    self.assertEqual(len(app.pin),5)
            finally: root.destroy()

    def test_gui_import_does_not_load_pm3(self):
        code="import epd_gui,sys; assert not any(n.startswith('pm3_') for n in sys.modules)"
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
