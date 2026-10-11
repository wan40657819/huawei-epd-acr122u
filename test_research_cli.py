import contextlib
import io
import unittest
from unittest.mock import patch
from research_cli import main
from iso_dep import decode


class ResearchCliTests(unittest.TestCase):
    @patch('research_cli.probe.Reader')
    def test_preview_never_opens_hardware(self, reader):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result=main(['preview','--inf','DE AD BE EF'])
        self.assertEqual(result,0)
        reader.assert_not_called()
        self.assertIn('offline',output.getvalue())

    @patch('research_cli.probe.Reader')
    def test_send_requires_explicit_confirmation_and_evidence(self, reader):
        for args in [['send','--port','COM17','--inf','DEAD'],
                     ['send','--port','COM17','--inf','DEAD','--confirmed-read-only']]:
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit): main(args)
        reader.assert_not_called()

    @patch('research_cli.probe.Reader')
    def test_invalid_hex_never_opens_hardware(self, reader):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            main(['preview','--inf','not hex'])
        reader.assert_not_called()


if __name__=='__main__': unittest.main()
