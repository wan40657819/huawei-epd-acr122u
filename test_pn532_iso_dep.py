import unittest
from unittest.mock import Mock, patch
from protocol import parse_ats
from pn532_iso_dep import PN532IsoDep, timeout_setting
import wakeup_probe as probe
from iso_dep import LinkTimeout, FrameError, SessionError


INFO = dict(ats=parse_ats(bytes.fromhex('0b7833a002901254715203')), fsd=256, cid=0, uid='12 54 71 52')


class AdapterTests(unittest.TestCase):
    def test_fwt_rounding_and_long_wtx(self):
        self.assertEqual(timeout_setting(313), (13,409.6))
        self.assertEqual(timeout_setting(18254), (0,18254))
        for value in [0,-1,float('nan'),float('inf')]:
            with self.assertRaises(ValueError): timeout_setting(value)
    def make(self):
        reader=Mock()
        reader.rf_on=False
        reader.command.return_value=bytes.fromhex('32010607')
        reader.read.return_value={0x6302:0,0x6303:0,0x633d:0}
        reader.field.side_effect=lambda enabled: setattr(reader,'rf_on',enabled)
        return reader,PN532IsoDep(reader)
    @patch.object(probe,'log')
    @patch('pn532_iso_dep.time.sleep')
    @patch.object(probe,'activation',return_value=INFO)
    def test_reuses_activation_keeps_field_until_close(self, activate, sleep, log):
        reader,session=self.make()
        self.assertEqual(session.activate(),INFO)
        activate.assert_called_once()
        self.assertTrue(reader.rf_on)
        self.assertEqual(session.engine.max_tx_inf,253)
        self.assertAlmostEqual(session.engine.fwt_ms,309.314454,places=5)
        with self.assertRaises(SessionError): session.activate()
        self.assertTrue(reader.rf_on)
        session.close(); session.close()
        self.assertFalse(reader.rf_on)
        reader.s.close.assert_called_once()
    @patch.object(probe,'log')
    @patch('pn532_iso_dep.time.sleep')
    @patch.object(probe,'activation',return_value=INFO)
    def test_exchange_uses_proven_rf_path_and_no_field_cycle(self, activate, sleep, log):
        reader,session=self.make(); session.activate()
        before=reader.field.call_count
        with patch.object(probe,'rf_transceive',return_value=bytes.fromhex('02 41 3b 26')) as rf:
            from protocol import crc_a
            rf.return_value=b'\x02A'+crc_a(b'\x02A')
            self.assertEqual(session.exchange(b'known synthetic'),b'A')
            self.assertTrue(rf.call_args.kwargs['software_crc'])
        self.assertEqual(reader.field.call_count,before)
        session.close()
    @patch.object(probe,'log')
    def test_rf_status_mapping_and_hsu_abort(self, log):
        reader,session=self.make(); reader.rf_on=True
        for status,error in [(1,LinkTimeout),(2,FrameError),(3,FrameError),(0x27,SessionError)]:
            with patch.object(probe,'rf_transceive',side_effect=probe.RFStatusError('test',status,{})):
                with self.assertRaises(error): session.transceive(b'\x02\x00\x00',313)
        with patch.object(probe,'rf_transceive',side_effect=TimeoutError('HSU')):
            with self.assertRaises(SessionError): session.transceive(b'\x02\x00\x00',18000)
        reader.abort.assert_called_once()
        self.assertTrue(reader.rf_on)
    def test_no_exchange_before_activate(self):
        reader,session=self.make()
        with self.assertRaises(SessionError): session.exchange(b'x')
        reader.command.assert_not_called()
    def test_unproven_cid_and_nad_not_offered(self):
        with self.assertRaises(ValueError): PN532IsoDep(Mock(),cid=3)

    @patch.object(probe,'log')
    @patch('pn532_iso_dep.time.sleep')
    @patch.object(probe,'activation',return_value=INFO)
    def test_failed_final_activation_step_cannot_send(self, activate, sleep, log):
        reader,session=self.make()
        with patch.object(session,'_configure_timeout',side_effect=RuntimeError('HSU failed')):
            with self.assertRaises(RuntimeError): session.activate()
        with patch.object(probe,'rf_transceive') as rf:
            with self.assertRaises(SessionError): session.exchange(b'x')
            rf.assert_not_called()
        session.close()

    @patch.object(probe,'log')
    @patch('pn532_iso_dep.time.sleep')
    @patch.object(probe,'activation',return_value=INFO)
    def test_field_check_failure_invalidates(self, activate, sleep, log):
        for failure in [None,RuntimeError('read failed')]:
            reader,session=self.make();session.activate()
            reader.read.return_value={0x6302:0,0x6303:0,0x6304:0x80,0x633d:0}
            reader.read.side_effect=failure
            with self.assertRaises((SessionError,RuntimeError)): session.check_field()
            with patch.object(probe,'rf_transceive') as rf:
                with self.assertRaises(SessionError): session.exchange(b'x')
                rf.assert_not_called()
            reader.read.side_effect=None
            session.close()


if __name__=='__main__': unittest.main()
