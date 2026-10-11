from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from tempfile import TemporaryDirectory
import epd_paths


class PathsTests(TestCase):
    def test_frozen_app_uses_executable_directory(self):
        with TemporaryDirectory() as d:
            exe=Path(d)/'tool.exe'
            with patch.object(epd_paths.sys,'frozen',True,create=True),patch.object(epd_paths.sys,'executable',str(exe)):
                self.assertEqual(epd_paths.app_dir(),Path(d).resolve())
                self.assertEqual(epd_paths.data_dir(),Path(d).resolve())

    def test_unwritable_directory_falls_back_to_local_appdata(self):
        with TemporaryDirectory() as d:
            with patch.object(epd_paths.tempfile,'TemporaryFile',side_effect=PermissionError),patch.dict(epd_paths.os.environ,{'LOCALAPPDATA':d}):
                self.assertEqual(epd_paths.data_dir(),Path(d)/'EInkTool')
