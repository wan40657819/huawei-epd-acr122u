from test_fixtures import TEST_PIN
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from PIL import Image
from image_codec import ImageOptions,PreparedImage
from epd_protocol import TransferPlan,TransferPacket,READ_INFO,READ_STATUS,build_refresh_command
from epd_writer import EpdWriter,WriteJob
from epd_logging import JobLog
from test_epd_protocol import D1
import hashlib

REFRESH=build_refresh_command(2000)

class Clock:
    def __init__(self): self.value=0
    def __call__(self): return self.value
    def sleep(self,n): self.value+=n

class Session:
    def __init__(self): self.sent=[]; self.sent_at=[]; self.closed=False; self.busy=1; self.fail=None; self.close_fail=False
    def activate(self): return {'uid':getattr(self,'uid','12 54 71 52')}
    def exchange(self,inf,**kw):
        self.sent.append((inf,kw))
        if hasattr(self,'clock'): self.sent_at.append(self.clock())
        if getattr(self,'raise_secret',False) and inf.startswith(bytes.fromhex('0020')):
            raise RuntimeError('raw 01 02 03 04 05 / 0102030405')
        if inf==self.fail: return bytes.fromhex('6985')
        if inf==READ_INFO: return getattr(self,'device_info',D1)
        if inf==READ_STATUS:
            if self.busy: self.busy-=1; return bytes.fromhex('019000')
            return bytes.fromhex('009000')
        return bytes.fromhex('9000')
    def close(self):
        self.closed=True
        if self.close_fail: raise RuntimeError('cleanup')

class WriterTests(unittest.TestCase):
    def test_experimental_job_flags_removed(self):
        from dataclasses import fields
        names={f.name for f in fields(WriteJob)}
        self.assertFalse(names & {'force_power','force_rf_output','force_poll_interval_s','retry_on_error'})

    def test_refresh_failure_closes_without_replay(self):
        for command,response in ((REFRESH,'68ca'),(READ_STATUS,'68ca'),(READ_STATUS,'6985')):
            def configure(s):
                original=s.exchange
                def exchange(inf,**kw):
                    result=original(inf,**kw)
                    return bytes.fromhex(response) if inf==command else result
                s.exchange=exchange
            result,s,events=self.execute(configure)
            self.assertEqual(result.outcome,'failed'); self.assertFalse(result.device_completed)
            self.assertTrue(s.closed)
            self.assertEqual(sum(inf==REFRESH for inf,kw in s.sent),1)
            self.assertEqual(sum(inf==READ_STATUS for inf,kw in s.sent),int(command==READ_STATUS))

    def test_refresh_transport_failure_closes_without_holding_or_replay(self):
        def configure(s):
            original=s.exchange
            def exchange(inf,**kw):
                result=original(inf,**kw)
                if inf==REFRESH: raise RuntimeError('WTX budget exceeded')
                return result
            s.exchange=exchange
        result,s,_=self.execute(configure)
        self.assertEqual(result.outcome,'failed'); self.assertTrue(s.closed)
        self.assertEqual(sum(inf==REFRESH for inf,kw in s.sent),1)

    def test_detect_without_file_logging_does_not_create_job_directory(self):
        with TemporaryDirectory() as d:
            target=Path(d)/'job'; session=Session(); events=[]
            writer=EpdWriter(session_factory=lambda port:session,
                log_factory=lambda callback,pin:JobLog(target,callback,pin,enabled=lambda:False))
            result=writer.detect('TEST',Event(),events.append)
            self.assertEqual(result.outcome,'detected')
            self.assertIsNone(result.log_directory)
            self.assertFalse(target.exists())
            self.assertTrue(events)
    def test_other_uid_same_profile_can_use_existing_workflow(self):
        def configure(session):
            session.uid='01 02 03 04'
            session.device_info=D1.replace(bytes.fromhex('12547152'),bytes.fromhex('01020304'))
        result,session,_=self.execute(configure)
        self.assertEqual(result.outcome,'success')
        self.assertTrue(result.device_completed)
        self.assertIn(REFRESH,[inf for inf,kw in session.sent])

    def test_uid_mismatch_stops_before_authentication_and_upload(self):
        result,session,_=self.execute(lambda session:setattr(session,'uid','01 02 03 04'))
        self.assertEqual(result.outcome,'failed')
        self.assertIn('UID',result.error)
        self.assertEqual([inf for inf,kw in session.sent],[READ_INFO])

    def execute(self,configure=None,cancel=None,timeout=60,*,timing=None,packets=1,sleep_hook=None):
        session=Session(); clock=Clock(); events=[]
        session.clock=clock
        if configure: configure(session)
        image=Image.new('RGB',(552,768),'white'); packed=b'\x55'*105984
        prepared=PreparedImage(image,image,b'\x01'*(552*768),packed,'hash',ImageOptions())
        packet=TransferPacket(bytes.fromhex('F0D3000103000011'),0,0,True)
        plan=TransferPlan((packet,)*packets,hashlib.sha256(packed).hexdigest(),1,1)
        with TemporaryDirectory() as d:
            def sleep(n):
                clock.sleep(n)
                if sleep_hook: sleep_hook(session,clock)
            writer=EpdWriter(lambda port:session,lambda emit,pin:JobLog(Path(d)/'job',emit,pin),clock,sleep)
            result=writer.write(WriteJob('fake',prepared,plan,TEST_PIN,timeout,**(timing or {})),cancel or Event(),events.append)
        return result,session,events

    def test_packet_precharge_and_poll_delays_keep_single_session(self):
        result,s,events=self.execute(timing=dict(packet_interval_s=.2,pre_refresh_wait_s=2,
            power_on_delay_ms=2000,poll_interval_s=.5),packets=2)
        self.assertEqual(result.outcome,'success')
        d3=[i for i,(inf,kw) in enumerate(s.sent) if inf[1:2]==b'\xd3']
        self.assertAlmostEqual(s.sent_at[d3[1]]-s.sent_at[d3[0]],.2)
        refresh_index=next(i for i,(inf,kw) in enumerate(s.sent) if inf[1:2]==b'\xd4')
        self.assertEqual(s.sent[refresh_index][0],bytes.fromhex('f0d4948000'))
        self.assertAlmostEqual(s.sent_at[refresh_index]-s.sent_at[d3[-1]],2)
        poll_times=[s.sent_at[i] for i,(inf,kw) in enumerate(s.sent) if inf==READ_STATUS]
        self.assertAlmostEqual(poll_times[1]-poll_times[0],.5)
        self.assertEqual(s.sent[refresh_index][1]['deadline'],s.sent_at[refresh_index]+60)
        self.assertTrue(s.closed)

    def test_cancel_during_precharge_does_not_refresh(self):
        cancel=Event()
        def stop(s,clock): cancel.set()
        result,s,_=self.execute(cancel=cancel,timing=dict(pre_refresh_wait_s=2),sleep_hook=stop)
        self.assertEqual(result.outcome,'cancelled')
        self.assertFalse(any(inf[1:2]==b'\xd4' for inf,kw in s.sent))
        self.assertTrue(s.closed)

    def test_invalid_timing_never_opens_session(self):
        for timing in (dict(packet_interval_s=float('nan')),dict(pre_refresh_wait_s=-1),
                       dict(power_on_delay_ms=0),dict(poll_interval_s=0)):
            result,s,_=self.execute(timing=timing)
            self.assertEqual(result.outcome,'failed')
            self.assertFalse(s.sent); self.assertFalse(s.closed)

    def test_acr122u_envelope_reminder_warns_but_proceeds(self):
        packet=TransferPacket(bytes.fromhex('f0d3000103000011')+bytes(38),0,0,True)
        image=Image.new('RGB',(552,768),'white'); packed=b'\x55'*105984
        prepared=PreparedImage(image,image,b'\x01'*(552*768),packed,'hash',ImageOptions())
        plan=TransferPlan((packet,),hashlib.sha256(packed).hexdigest(),46,1)
        for port,reminded in (('acr122:ACS ACR122U PICC Interface 0',True),('COM9',False)):
            session=Session(); session.busy=0; clock=Clock(); events=[]
            with TemporaryDirectory() as d:
                writer=EpdWriter(lambda port:session,
                    lambda callback,pin:JobLog(Path(d)/'job',callback,pin),clock,clock.sleep)
                result=writer.write(WriteJob(port,prepared,plan,TEST_PIN,60),Event(),events.append)
            warnings=[e for e in events if e.kind=='warning']
            self.assertEqual(result.outcome,'success')
            self.assertIsNone(result.error)
            self.assertTrue(session.sent); self.assertTrue(session.closed)
            if reminded:
                self.assertEqual(len(warnings),1)
                self.assertIn('42',warnings[0].payload['message'])
                self.assertIn('46',warnings[0].payload['message'])
                self.assertIn('提醒',warnings[0].payload['message'])
            else:
                self.assertEqual(warnings,[])

    def test_order_and_deadline(self):
        result,s,events=self.execute()
        self.assertEqual(result.outcome,'success')
        self.assertEqual([v[0] for v in s.sent],[READ_INFO,bytes.fromhex('00200001050102030405'),bytes.fromhex('00200001050102030405'),bytes.fromhex('F0D3000103000011'),REFRESH,READ_STATUS,READ_STATUS])
        deadlines=[kw['deadline'] for inf,kw in s.sent if inf in (REFRESH,READ_STATUS)]
        self.assertEqual(len(set(deadlines)),1)
        self.assertEqual(result.confirmed_packets,1)
        self.assertTrue(s.closed)

    def test_d3_failure_no_refresh(self):
        result,s,_=self.execute(lambda s:setattr(s,'fail',bytes.fromhex('F0D3000103000011')))
        self.assertEqual(result.outcome,'failed')
        self.assertNotIn(REFRESH,[inf for inf,kw in s.sent]); self.assertTrue(s.closed)
        self.assertEqual(result.confirmed_packets,0)

    def test_timeout_and_cancel(self):
        result,s,_=self.execute(lambda s:setattr(s,'busy',999),timeout=.2)
        self.assertEqual(result.outcome,'failed')
        cancel=Event(); cancel.set()
        result,s,_=self.execute(cancel=cancel)
        self.assertEqual(result.outcome,'cancelled'); self.assertTrue(s.closed)
        self.assertFalse(s.sent)

    def test_completed_cleanup_failure(self):
        result,s,_=self.execute(lambda s:setattr(s,'close_fail',True))
        self.assertTrue(result.device_completed)
        self.assertEqual(result.cleanup_error,'cleanup')
        self.assertEqual(result.outcome,'cleanup_failed')

    def test_auth_rejected(self):
        result,s,_=self.execute(lambda s:setattr(s,'fail',bytes.fromhex('00200001050102030405')))
        self.assertEqual(result.outcome,'failed')
        self.assertEqual(len(s.sent),2); self.assertTrue(s.closed)

    def test_result_error_redacted(self):
        result,s,_=self.execute(lambda s:setattr(s,'raise_secret',True))
        self.assertNotIn('01 02 03 04 05',result.error)
        self.assertNotIn('0102030405',result.error)
