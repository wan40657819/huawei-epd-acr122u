from test_fixtures import TEST_PIN
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from epd_logging import JobLog
import wakeup_probe as probe

class LoggingTests(unittest.TestCase):
    def test_optional_logging_can_start_and_stop_without_losing_ui_events(self):
        with TemporaryDirectory() as d:
            target=Path(d)/'job'; rows=[]; enabled=[False]
            logger=JobLog(target,rows.append,TEST_PIN,enabled=lambda:enabled[0])
            logger.emit('stage',message='before')
            self.assertFalse(target.exists())
            enabled[0]=True
            logger.emit('stage',message='saved')
            enabled[0]=False
            logger.emit('stage',message='after')
            self.assertEqual(len(rows),3)
            self.assertEqual(len((target/'events.ndjson').read_text().splitlines()),1)
            enabled[0]=True
            logger.finish({'result':'test'})
            self.assertEqual(len(json.loads((target/'events.json').read_text())),1)

    def test_disabled_finish_creates_no_files(self):
        with TemporaryDirectory() as d:
            target=Path(d)/'job'
            logger=JobLog(target,lambda row:None,TEST_PIN,enabled=lambda:False)
            logger.emit('result',outcome='detected'); logger.finish({})
            self.assertFalse(target.exists())
            self.assertIsNone(logger.directory)

    def test_redaction_and_restore(self):
        with TemporaryDirectory() as d:
            target=Path(d)/'job'
            rows=[]; logger=JobLog(target,rows.append,TEST_PIN)
            original=probe.log
            with self.assertRaises(RuntimeError):
                with logger.capture_probe():
                    probe.log('rf_tx',raw='00 ff 02 01 02 03 04 05 aa',nested={'error':'0102030405'})
                    raise RuntimeError('test')
            self.assertIs(probe.log,original)
            logger.finish({'result':'test'})
            text=(target/'events.txt').read_text('utf-8')+(target/'events.json').read_text('utf-8')
            self.assertNotIn('01 02 03 04 05',text)
            self.assertNotIn('0102030405',text)
            self.assertIn('XX',text)
            self.assertTrue(json.loads((target/'events.json').read_text())[0]['hex_redacted'])
