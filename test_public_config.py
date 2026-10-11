"""A maintainer-authorized default PIN works without a local configuration."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock
from epd_protocol import load_pin
import epd_protocol
from epd_writer import EpdWriter,WriteJob
from epd_logging import JobLog
from test_epd_writer import Session


class PublicConfigTests(unittest.TestCase):
    def test_missing_config_uses_published_default_pin(self):
        with TemporaryDirectory() as d:
            pin=load_pin(Path(d)/'missing.json')
            self.assertEqual(pin,epd_protocol.DEFAULT_PIN)
            self.assertEqual(len(pin),5)

    def test_write_job_loads_default_without_configuration(self):
        from unittest.mock import patch
        with TemporaryDirectory() as d,patch('epd_protocol.app_dir',return_value=Path(d)):
            self.assertEqual(WriteJob('FAKE',None,None).pin,epd_protocol.DEFAULT_PIN)

    def test_detect_still_works_without_pin(self):
        session=Session()
        with TemporaryDirectory() as d:
            writer=EpdWriter(lambda port:session,
                lambda callback,pin:JobLog(Path(d)/'job',callback,pin,enabled=lambda:False))
            result=writer.detect('FAKE',Event(),lambda row:None)
        self.assertEqual(result.outcome,'detected')
        self.assertEqual(len(session.sent),1)

    def test_explicit_empty_pin_never_opens_hardware(self):
        factory=Mock(side_effect=AssertionError('Hardware must remain closed'))
        with TemporaryDirectory() as d:
            writer=EpdWriter(factory,
                lambda callback,pin:JobLog(Path(d)/'job',callback,pin,enabled=lambda:False))
            result=writer.write(WriteJob('FAKE',None,None,pin=b''),Event(),lambda row:None)
        self.assertEqual(result.outcome,'failed')
        factory.assert_not_called()
