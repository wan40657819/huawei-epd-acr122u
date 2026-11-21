"""Offline tests for the ACR122U PC/SC PN532 transport; no reader is opened."""
import unittest
from unittest.mock import Mock, patch

import acr122_reader
from acr122_reader import PcscPn532, PcscError, PcscEscapeDisabled, _PyscardLink, escape_hint
from epd_devices import device_backend, enumerate_devices, split_device
import epd_writer


def response(command, payload=b'', sw=b'\x90\x00'):
    return bytes([0xD5, command+1])+payload+sw


class FakeLink:
    name='ACS ACR122U PICC Interface 0'
    mode='direct'

    def __init__(self, responses=()):
        self.responses=list(responses)
        self.sent=[]
        self.closed=False

    def send(self, apdu):
        self.sent.append(bytes(apdu))
        result=self.responses.pop(0)
        if isinstance(result,Exception):
            raise result
        return result

    def close(self):
        self.closed=True


class TransportTests(unittest.TestCase):
    def reader(self,*responses):
        link=FakeLink(responses)
        return PcscPn532(link=link),link

    @patch.object(acr122_reader.probe,'log')
    def test_firmware_and_incommunicate_thru_use_pseudo_apdu(self,log):
        reader,link=self.reader(response(0x02,bytes.fromhex('32010607')),
                                response(0x42,bytes.fromhex('000400')))
        self.assertEqual(reader.command(2),bytes.fromhex('32010607'))
        self.assertEqual(reader.command(0x42,b'\x7A'),bytes.fromhex('000400'))
        self.assertEqual(link.sent[0],bytes.fromhex('ff00000002d402'))
        self.assertEqual(link.sent[1],bytes.fromhex('ff00000003d4427a'))

    @patch.object(acr122_reader.probe,'log')
    def test_register_read_write_and_field(self,log):
        reader,link=self.reader(response(0x06,b'\x00'),response(0x08,b''),
                                response(0x32,b''))
        self.assertEqual(reader.read([0x6302]),{0x6302:0})
        reader.write({0x6302:0})
        reader.field(True)
        self.assertEqual(link.sent[0],bytes.fromhex('ff00000004d4066302'))
        self.assertEqual(link.sent[1],bytes.fromhex('ff00000005d408630200'))
        self.assertEqual(link.sent[2],bytes.fromhex('ff00000004d4320103'))
        self.assertTrue(reader.rf_on)

    @patch.object(acr122_reader.probe,'log')
    def test_status_word_command_and_length_are_validated(self,log):
        for bad in (bytes.fromhex('d543000400e400'),
                    bytes.fromhex('d501009000'),
                    bytes.fromhex('d542 009000')):
            reader,_=self.reader(bad)
            with self.assertRaises(RuntimeError): reader.command(0x42)
        reader,_=self.reader()
        with self.assertRaises(ValueError): reader.command(0x42,b'x'*254)

    @patch.object(acr122_reader.probe,'log')
    def test_expired_deadline_never_sends(self,log):
        reader,link=self.reader()
        reader.deadline=0
        with self.assertRaises(TimeoutError): reader.command(2)
        self.assertFalse(link.sent)

    @patch.object(acr122_reader.probe,'log')
    def test_late_response_is_not_accepted(self,log):
        reader,link=self.reader(response(0x02,bytes.fromhex('32010607')))
        reader.deadline=1
        with patch.object(acr122_reader.time,'monotonic',side_effect=[0,0,0,5,5]):
            with self.assertRaises(TimeoutError): reader.command(2)
        self.assertEqual(len(link.sent),1)

    @patch.object(acr122_reader.probe,'log')
    def test_watchdog_enforces_serial_host_budget(self,log):
        import time as real_time
        class SlowLink(FakeLink):
            def send(self, apdu):
                self.sent.append(bytes(apdu))
                real_time.sleep(0.5)
                return response(0x02,bytes.fromhex('32010607'))
        link=SlowLink()
        reader=PcscPn532(link=link)
        started=real_time.monotonic()
        with self.assertRaises(TimeoutError): reader.command(2,timeout=0.1)
        self.assertLess(real_time.monotonic()-started,0.4)
        self.assertTrue(reader.stuck)
        self.assertEqual(len(link.sent),1)
        with self.assertRaises(PcscError): reader.command(2)
        reader.close()
        with self.assertRaises(PcscError): reader.command(2)

    @patch.object(acr122_reader.probe,'log')
    def test_abort_does_not_write_and_close_is_idempotent(self,log):
        reader,link=self.reader()
        reader.abort(); reader.close(); reader.close()
        self.assertFalse(link.sent)
        self.assertTrue(link.closed)
        with self.assertRaises(acr122_reader.PcscError): reader.command(2)

    @patch.object(acr122_reader.probe,'log')
    def test_escape_disabled_is_reported_unchanged(self,log):
        reader,_=self.reader(PcscEscapeDisabled('escape disabled'))
        with self.assertRaises(PcscEscapeDisabled): reader.command(2)

    def test_escape_hint_mentions_registry_and_helper(self):
        text=escape_hint()
        self.assertIn('EscapeCommandEnable',text)
        self.assertIn('enable-acr122-escape.ps1',text)

    def test_sharing_violation_reports_draining_previous_task(self):
        import sys
        from types import SimpleNamespace
        sharing=0x8010000B
        parent=SimpleNamespace(__name__='smartcard',__path__=[])
        fake=SimpleNamespace(
            SCARD_SCOPE_USER=0,SCARD_S_SUCCESS=0,
            SCARD_SHARE_DIRECT=3,SCARD_SHARE_EXCLUSIVE=2,
            SCARD_PROTOCOL_T0=1,SCARD_PROTOCOL_T1=2,
            SCARD_E_SHARING_VIOLATION=sharing,
            SCardEstablishContext=lambda scope:(0,object()),
            SCardConnect=lambda *args:(sharing,None,0),
            SCardGetErrorMessage=lambda hresult:'sharing violation',
            SCardDisconnect=lambda *args:0,
            SCardReleaseContext=lambda *args:0)
        with patch.dict(sys.modules,{'smartcard':parent,'smartcard.scard':fake}):
            with self.assertRaises(PcscError) as raised:
                _PyscardLink('ACS ACR122U PICC Interface 0')
        message=str(raised.exception)
        self.assertIn('一分钟',message)
        self.assertIn('上一次任务占用',message)
        self.assertIn('不需要重新运行 enable-acr122-escape.ps1',message)
        self.assertNotIn('若尚未启用',message)


class DeviceTests(unittest.TestCase):
    def test_split_and_backend(self):
        self.assertEqual(split_device('COM17'),('serial','COM17'))
        self.assertEqual(split_device('acr122:ACS ACR122U PICC Interface 0'),
                         ('acr122','ACS ACR122U PICC Interface 0'))
        self.assertEqual(device_backend('COM17'),'PN532')
        self.assertEqual(device_backend('acr122:reader'),'ACR122U')

    def test_enumeration_combines_serial_and_acr122(self):
        port=Mock(device='COM9',description='USB-Serial')
        with patch('epd_devices.ensure_dependencies'), \
                patch('serial.tools.list_ports.comports',return_value=[port]), \
                patch('acr122_reader.list_acr122_readers',
                      return_value=['ACS ACR122U PICC Interface 0']):
            entries,warnings=enumerate_devices()
        self.assertEqual(warnings,[])
        self.assertEqual([value for _,value in entries],
                         ['COM9','acr122:ACS ACR122U PICC Interface 0'])
        self.assertTrue(entries[1][0].startswith('ACR122U · '))

    def test_enumeration_reports_missing_backends_without_raising(self):
        with patch('epd_devices.ensure_dependencies',side_effect=RuntimeError('serial missing')), \
                patch('acr122_reader.list_acr122_readers',
                      side_effect=acr122_reader.PcscError('pcsc missing')):
            entries,warnings=enumerate_devices()
        self.assertEqual(entries,[])
        self.assertEqual(len(warnings),2)
        self.assertIn('serial missing',warnings[0])
        self.assertIn('pcsc missing',warnings[1])

    def test_real_session_selects_transport_by_device_value(self):
        fake_iso=Mock()
        with patch('epd_dependencies.ensure_dependencies'), \
                patch('acr122_reader.PcscPn532') as pcsc, \
                patch('pn532_iso_dep.PN532IsoDep',return_value=fake_iso) as iso:
            self.assertIs(epd_writer.real_session('acr122:ACS ACR122U PICC Interface 0'),fake_iso)
        pcsc.assert_called_once_with('ACS ACR122U PICC Interface 0')
        self.assertEqual(iso.call_args.kwargs['tx_inf_limit'],240)
        fake_reader=Mock()
        with patch('epd_dependencies.ensure_dependencies'), \
                patch('wakeup_probe.Reader',return_value=fake_reader) as serial, \
                patch('pn532_iso_dep.PN532IsoDep',return_value=fake_iso) as iso:
            self.assertIs(epd_writer.real_session('COM9'),fake_iso)
        serial.assert_called_once_with('COM9',full_init=True)
        self.assertEqual(iso.call_args.kwargs['tx_inf_limit'],240)


if __name__=='__main__':
    unittest.main()
