"""Live session adapter using the proven Reader + activation + 0x42 RF path."""
from types import SimpleNamespace
import math
import time
import wakeup_probe as probe
from iso_dep import IsoDep, FrameError, LinkTimeout, SessionError


# Exact phase-one RF configuration, not a new driver or alternate FIFO routine.
SETTINGS = {0x6302:0, 0x6303:0, 0x6305:0x40, 0x633d:7,
            0x633c:0x10, 0x6308:0x85, 0x6309:0x4d, 0x6313:0x6f,
            0x6315:0x87, 0x6316:0x59, 0x6317:0xf4, 0x6318:0x3f, 0x6319:0x11}


def timeout_setting(milliseconds):
    if not math.isfinite(milliseconds) or milliseconds <= 0:
        raise ValueError('Timeout must be finite and positive')
    for index in range(1,17):
        actual = 0.1 * 2**(index-1)
        if actual >= milliseconds:
            return index,actual
    # PN532 max finite RFConfiguration timeout is 3276.8 ms. For legal long WTX,
    # use documented no-timeout mode plus a finite HSU deadline; abort on expiry.
    return 0,milliseconds


class PN532IsoDep:
    def __init__(self, pn532, *, cid=None, prepower_ms=100, frame_delay_ms=0, **limits):
        if cid not in (None,0):
            raise ValueError('Proven RATS selects CID=0; only omitted CID or explicit CID=0 supported')
        if not (0 <= prepower_ms <= 5000 and 0 <= frame_delay_ms <= 1000):
            raise ValueError('Timing out of experimental range')
        self.pn532, self.cid = pn532,cid
        self.prepower_ms,self.frame_delay_ms = prepower_ms,frame_delay_ms
        self.limits=limits
        self.engine=None
        self.info=None
        self.saved={}
        self.closed=False
        self._started=False
        self._timeout_index=None

    @property
    def rf_on(self): return self.pn532.rf_on

    def activate(self):
        if self.closed or self._started:
            raise SessionError('Explicit close and a new session required for reactivation')
        self._started=True
        firmware=self.pn532.command(2)
        if len(firmware)!=4 or firmware[0]!=0x32:
            raise SessionError('Not an identified PN532')
        probe.log('session_firmware', raw=firmware.hex(' '),
                  transport=getattr(self.pn532,'transport_name','PN532 HSU'))
        self.pn532.command(0x14,[1,0x14,1])
        self.pn532.command(0x12,[0x14])
        self.pn532.command(0x32,[2,0,0x0b,0x0b])
        self.pn532.command(0x32,[4,0])
        self.saved=self.pn532.read(list(SETTINGS))
        self.pn532.field(False)
        time.sleep(0.3)
        self.pn532.write(SETTINGS)
        self.pn532.field(True)
        time.sleep(self.prepower_ms/1000)
        args=SimpleNamespace(stage='ats',frame_delay_ms=self.frame_delay_ms,timeout_ms_actual=102.4)
        try:
            self.info=probe.activation(self.pn532,args)
        except probe.RFStatusError as exc:
            if exc.status==1:
                raise SessionError(
                    f'{exc}；未检测到墨水屏响应，请检查屏幕是否位于天线工作区以及供电/耦合是否足够'
                    '（ACR122U 天线较小，可能无法为屏幕提供足够功率）') from exc
            raise
        ats=self.info['ats']
        if self.cid is not None and not ats['cid_supported']:
            raise SessionError('ATS does not permit CID')
        if ats['sfgt_ms'] is not None:
            time.sleep(ats['sfgt_ms']/1000)
        engine=IsoDep(self, fsc=ats['fsc'],fsd=self.info['fsd'],fwt_ms=ats['fwt_ms'],
                      cid=self.cid,logger=probe.log,**self.limits)
        self._configure_timeout(ats['fwt_ms']+49152/13560)
        probe.log('session_active', parameters=self.info,max_tx_inf=engine.max_tx_inf,
                  cid=self.cid,rf_on=self.rf_on,max_wtx=engine.max_wtx,
                  exchange_timeout_s=engine.exchange_timeout_s,application_exchange_verified=False)
        self.engine=engine  # publish only after every activation step succeeds
        return self.info

    def _configure_timeout(self,requested):
        index,actual=timeout_setting(requested)
        if index!=self._timeout_index:
            self.pn532.command(0x32,[2,0,0x0b,index])
            self._timeout_index=index
        probe.log('session_timeout',requested_ms=requested,rf_timeout_index=index,
                  rf_timeout_ms=actual if index else None,
                  host_deadline_ms=max(2000,actual+1000),rf_on=self.rf_on)
        return actual

    def transceive(self,frame,timeout_ms):
        if self.closed or not self.rf_on:
            raise SessionError('RF field is not enabled')
        try:
            actual=self._configure_timeout(timeout_ms)
            args=SimpleNamespace(frame_delay_ms=self.frame_delay_ms,timeout_ms_actual=actual)
            return probe.rf_transceive(self.pn532,frame,stage='ISO_DEP',tx_bits=len(frame)*8,
                                       args=args,software_crc=True)
        except probe.RFStatusError as exc:
            if exc.status==1: raise LinkTimeout(str(exc)) from exc
            if exc.status in (2,3,4,5,-1): raise FrameError(str(exc)) from exc
            raise SessionError(f'Fatal PN532 status: {exc}') from exc
        except (TimeoutError,RuntimeError) as exc:
            # Serial framing/deadline uncertainty is not a safe reason to resend INF.
            # Abort the controller command without cycling RF, then invalidate.
            try:
                self.pn532.abort()
                probe.log('session_abort_diagnostics',registers=probe.diagnostics(self.pn532))
            except Exception as abort_error:
                probe.log('session_abort_failed',error=str(abort_error))
            raise SessionError(f'HSU/transport uncertain; close and reactivate: {exc}') from exc

    def exchange(self,inf, *, deadline=None):
        if self.closed or self.engine is None:
            raise SessionError('activate() must succeed before exchange()')
        if deadline is None:
            return self.engine.exchange(inf)
        previous=getattr(self.pn532,'deadline',None)
        self.pn532.deadline=deadline
        try:
            return self.engine.exchange(inf,deadline=deadline)
        finally:
            self.pn532.deadline=previous
            self.pn532.reset_timeout()

    def check_field(self):
        """Host register read only: proves reader field configuration, not tag liveness."""
        if self.closed or self.engine is None or not self.engine.active:
            raise SessionError('Field check requires an active session')
        try:
            values=self.pn532.read([0x6302,0x6303,0x6304,0x633d])
            on=values[0x6304]&3==3
            probe.log('field_hold_check',rf_on=on,registers={f'{k:04X}':f'{v:02X}' for k,v in values.items()},
                      target_liveness_verified=False)
            if not on or values[0x6302]!=0 or values[0x6303]!=0:
                raise SessionError('RF configuration changed while holding session')
            return values
        except Exception as exc:
            self.engine.active=False
            probe.log('session_invalidated',error=str(exc),rf_on=self.rf_on)
            raise

    def close(self):
        if self.closed: return
        self.closed=True
        if self.engine is not None: self.engine.active=False
        try:
            if self._started or self.rf_on: self.pn532.field(False)
            if self.saved: self.pn532.write(self.saved)
        finally:
            self.pn532.close()
            probe.log('session_closed',rf_on=self.rf_on)
