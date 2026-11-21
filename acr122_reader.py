"""ACR122U PC/SC transport exposing the PN532 command channel used by wakeup_probe.

The ACR122U is a PN532 behind an ACS CCID controller.  PC/SC APDUs alone cannot
drive the display because the target needs the project's non-standard 7-bit wake
frame and raw ISO-DEP blocks.  This module therefore forwards complete PN532
commands through the ACR122U pseudo-APDU interface, keeping the verified RF and
ISO-DEP code unchanged.

On Windows the Microsoft Usbccid driver only permits these direct commands after
the one-time EscapeCommandEnable registry value is set (see
enable-acr122-escape.ps1).  A friendly error is raised otherwise.

wakeup_probe.Reader bounds every PN532 command with a host deadline and can
cancel a stuck command with the HSU ACK-abort frame.  PC/SC has no abort frame
and SCardControl blocks until the reader firmware gives up (~48 s), so this
transport runs each command in a watchdog thread and raises TimeoutError within
the same budget the serial path uses.  A timed-out command leaves the reader
firmware busy; the session is invalidated and never replayed, matching the
serial contract.
"""
from __future__ import annotations

import threading
import time

import wakeup_probe as probe


class PcscError(RuntimeError):
    """The ACR122U PC/SC connection could not be opened or used."""


class PcscEscapeDisabled(PcscError):
    """The Windows CCID driver rejected the direct PN532 escape command."""


def _escape_registry_entries():
    """Read-only inventory of ACR122U device parameters; never changes the registry."""
    try:
        import winreg
    except ImportError:
        return []
    base=r'SYSTEM\CurrentControlSet\Enum\USB\VID_072F&PID_2200'
    entries=[]
    try:
        root=winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,base,0,winreg.KEY_READ)
    except OSError:
        return entries
    with root:
        index=0
        while True:
            try:
                instance=winreg.EnumKey(root,index)
            except OSError:
                break
            index+=1
            params=base+'\\'+instance+'\\Device Parameters'
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,params,0,winreg.KEY_READ) as key:
                    try:
                        value,_=winreg.QueryValueEx(key,'EscapeCommandEnable')
                    except OSError:
                        value=None
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                        params+'\\WudfUsbccidDriver',0,winreg.KEY_READ) as key:
                        try:
                            driver_value,_=winreg.QueryValueEx(key,'EscapeCommandEnable')
                        except OSError:
                            driver_value=None
                except OSError:
                    driver_value=None
                entries.append((params,value,driver_value))
            except OSError:
                pass
    return entries


def escape_hint():
    entries=_escape_registry_entries()
    if entries:
        paths='\n'.join(
            f'  {path}（根项{"已启用" if value==1 else "未设置"}，'
            f'WudfUsbccidDriver {"已启用" if driver_value==1 else "未设置"}）'
            for path,value,driver_value in entries)
    else:
        paths='  HKLM\\SYSTEM\\CurrentControlSet\\Enum\\USB\\VID_072F&PID_2200\\<实例>\\Device Parameters'
    return (
        'ACR122U 需要一次性启用 PC/SC Escape 命令，才能转发 PN532 原始命令。\n'
        '请以管理员身份运行项目中的 enable-acr122-escape.ps1，然后拔插 ACR122U 再重试。\n'
        '手动设置：在以下注册表项的 Device Parameters（及其 WudfUsbccidDriver 子项）下'
        '新建 DWORD EscapeCommandEnable=1：\n'+paths)


def list_acr122_readers():
    """Return names of connected ACR122U PC/SC readers (listing only, no RF)."""
    from epd_dependencies import ensure_dependencies
    ensure_dependencies(pcsc_required=True)
    try:
        from smartcard.System import readers
        return [reader.name for reader in readers() if 'ACR122' in reader.name.upper()]
    except Exception as exc:
        raise PcscError(f'无法枚举 PC/SC 读卡器：{exc}') from exc


def default_acr122_reader():
    names=list_acr122_readers()
    if not names:
        raise PcscError('未找到 ACR122U；请确认 USB 已连接并已安装 PC/SC 驱动')
    return names[0]


class _PyscardLink:
    """One PC/SC connection that can send ACR122U pseudo-APDUs."""

    def __init__(self, name):
        self.name=name
        self.hcontext=None
        self.hcard=None
        self.protocol=0
        self.mode=None
        self.closed=False
        try:
            import smartcard.scard as sc
        except ImportError as exc:
            raise PcscError('缺少 pyscard，请运行：python -m pip install pyscard') from exc
        self.sc=sc
        hresult,hcontext=sc.SCardEstablishContext(sc.SCARD_SCOPE_USER)
        if hresult!=sc.SCARD_S_SUCCESS:
            raise PcscError('无法建立 PC/SC 上下文：'+self._error(hresult))
        self.hcontext=hcontext
        try:
            hresult,hcard,active=sc.SCardConnect(
                hcontext,name,sc.SCARD_SHARE_DIRECT,0)
            if hresult==sc.SCARD_S_SUCCESS:
                # No card is needed in direct mode; this is the path used for the
                # special 7-bit wake and raw ISO-DEP frames.
                self.hcard=hcard
                self.mode='direct'
                self.protocol=0
                self._verify_direct()
            else:
                hresult,hcard,active=sc.SCardConnect(
                    hcontext,name,sc.SCARD_SHARE_EXCLUSIVE,
                    sc.SCARD_PROTOCOL_T0 | sc.SCARD_PROTOCOL_T1)
                if hresult!=sc.SCARD_S_SUCCESS:
                    raise PcscError(
                        f'无法连接 {name}：{self._error(hresult)}；'
                        '若尚未启用 Escape 命令，请先运行 enable-acr122-escape.ps1')
                self.hcard=hcard
                self.mode='card'
                self.protocol=active
        except Exception:
            self.close()
            raise

    @staticmethod
    def _error(hresult):
        try:
            from smartcard.scard import SCardGetErrorMessage
            return SCardGetErrorMessage(hresult)
        except Exception:
            return f'0x{hresult & 0xffffffff:08X}'

    def _verify_direct(self):
        # Read-only PN532 GetFirmwareVersion through the same direct path the
        # application will use.  This also proves the escape command is enabled.
        try:
            response=self._control([0xFF,0x00,0x00,0x00,0x02,0xD4,0x02])
        except PcscEscapeDisabled:
            raise
        if len(response)<6 or response[:2]!=b'\xD5\x03':
            raise PcscError('ACR122U 直通查询返回异常：'+response.hex(' '))

    def _control(self, apdu):
        sc=self.sc
        hresult,response=sc.SCardControl(
            self.hcard,sc.SCARD_CTL_CODE(3500),list(apdu))
        if hresult!=sc.SCARD_S_SUCCESS:
            # The Microsoft Usbccid driver returns 1 for the rejected vendor
            # escape; other drivers use SCARD_E_INVALID_VALUE / NOT_TRANSACTED.
            if hresult in (1,sc.SCARD_E_INVALID_VALUE,sc.SCARD_E_NOT_TRANSACTED,
                           sc.SCARD_E_UNSUPPORTED_FEATURE):
                raise PcscEscapeDisabled(escape_hint())
            raise PcscError('ACR122U 直通命令失败：'+self._error(hresult))
        return bytes(response)

    def _transmit(self, apdu):
        sc=self.sc
        pci=sc.SCARD_PCI_T0 if self.protocol & sc.SCARD_PROTOCOL_T0 else sc.SCARD_PCI_T1
        hresult,response=sc.SCardTransmit(self.hcard,pci,list(apdu))
        if hresult!=sc.SCARD_S_SUCCESS:
            raise PcscError('ACR122U APDU 发送失败：'+self._error(hresult))
        if self.protocol & sc.SCARD_PROTOCOL_T0 and len(response)==2 and response[0]==0x61:
            hresult,response=sc.SCardTransmit(
                self.hcard,sc.SCARD_PCI_T0,[0xFF,0xC0,0x00,0x00,response[1]])
            if hresult!=sc.SCARD_S_SUCCESS:
                raise PcscError('ACR122U GET RESPONSE 失败：'+self._error(hresult))
        return bytes(response)

    def send(self, apdu):
        if self.closed:
            raise PcscError('ACR122U 连接已关闭')
        if self.mode=='direct':
            return self._control(apdu)
        return self._transmit(apdu)

    def close(self):
        if self.closed:
            return
        self.closed=True
        sc=getattr(self,'sc',None)
        try:
            if self.hcard is not None and sc is not None:
                sc.SCardDisconnect(self.hcard,sc.SCARD_LEAVE_CARD)
        finally:
            self.hcard=None
            if self.hcontext is not None and sc is not None:
                sc.SCardReleaseContext(self.hcontext)
                self.hcontext=None


class PcscPn532:
    """Subset of wakeup_probe.Reader implemented over ACR122U PC/SC."""

    transport_name='ACR122U PC/SC'

    def __init__(self, device=None, *, link=None):
        if link is None:
            link=_PyscardLink(device or default_acr122_reader())
        self.link=link
        self.device=getattr(link,'name','ACR122U')
        self.mode=getattr(link,'mode','direct')
        self.deadline=None
        self.rf_on=False
        self.closed=False
        self.stuck=False

    def command(self, cmd, data=b'', timeout=2):
        # timeout is accepted for wakeup_probe.Reader compatibility; the PN532
        # RF wait is configured through RFConfiguration and PC/SC calls are
        # synchronous.  The same host budget the serial path uses is enforced
        # with a watchdog because SCardControl blocks in the reader firmware.
        if self.closed:
            raise PcscError('ACR122U 连接已关闭')
        if self.stuck:
            raise PcscError('ACR122U 读卡器固件仍在等待上一条 PN532 命令；连接已失效，需重新检测')
        budget=timeout
        if self.deadline is not None and time.monotonic()>=self.deadline:
            raise TimeoutError('ACR122U 截止时间已用尽，未发送命令')
        if self.deadline is not None:
            budget=min(budget,self.deadline-time.monotonic())
        if budget<=0:
            raise TimeoutError('ACR122U 截止时间已用尽，未发送命令')
        data=bytes(data)
        if not 0<=cmd<=0xFF:
            raise ValueError('PN532 命令字无效')
        if len(data)>253:
            raise ValueError('ACR122U 单条 PN532 命令数据不得超过253字节')
        apdu=[0xFF,0x00,0x00,0x00,0x02+len(data),0xD4,cmd,*data]
        started=time.monotonic()
        box={}
        def deliver():
            try:
                box['response']=self.link.send(apdu)
            except BaseException as exc:   # surfaced on the calling thread
                box['error']=exc
        worker=threading.Thread(target=deliver,name='acr122-command',daemon=True)
        worker.start()
        worker.join(budget)
        if worker.is_alive():
            # The MCU keeps waiting for the PN532; only it can clear the state.
            self.stuck=True
            probe.log('pcsc_timeout',cmd=f'{cmd:02X}',tx=bytes(apdu).hex(' '),
                      budget_s=budget,mode=self.mode,
                      note='reader firmware still waiting for PN532; session invalidated')
            raise TimeoutError(
                f'ACR122U 命令在 {budget:g} 秒内未返回；读卡器固件仍在等待 PN532，本次连接已失效')
        if 'error' in box:
            exc=box['error']
            probe.log('pcsc_error',cmd=f'{cmd:02X}',tx=bytes(apdu).hex(' '),
                      error=str(exc),mode=self.mode)
            raise exc
        raw=box['response']
        probe.log('pcsc',cmd=f'{cmd:02X}',tx=bytes(apdu).hex(' '),rx=raw.hex(' '),
                  mode=self.mode,elapsed_ms=(time.monotonic()-started)*1000)
        if len(raw)<4:
            raise RuntimeError('ACR122U 返回数据过短：'+raw.hex(' '))
        if raw[-2:]!=b'\x90\x00':
            raise RuntimeError('ACR122U 返回状态字异常：'+raw.hex(' '))
        packet=raw[:-2]
        if packet[:2]!=bytes([0xD5,cmd+1]):
            raise RuntimeError('ACR122U 返回了非预期命令：'+packet.hex(' '))
        if self.deadline is not None and time.monotonic()>self.deadline:
            raise TimeoutError('ACR122U 命令在截止时间之后才返回')
        return packet[2:]

    def read(self, addresses):
        result=self.command(0x06,b''.join(a.to_bytes(2,'big') for a in addresses))
        if len(result)!=len(addresses):
            raise RuntimeError('ACR122U 寄存器读取长度异常')
        return dict(zip(addresses,result))

    def write(self, values):
        self.command(0x08,b''.join(
            a.to_bytes(2,'big')+bytes([v]) for a,v in values.items()))

    def field(self, enabled):
        self.command(0x32,[1,3 if enabled else 2])
        self.rf_on=enabled
        probe.log('rf_field',enabled=enabled,transport=self.transport_name)

    def abort(self):
        # SCardControl is synchronous and has no PN532 HSU ACK-abort equivalent.
        # Never retry application INF after an uncertain transport result.
        probe.log('pcsc_abort',transport=self.transport_name,
                  note='synchronous PC/SC transport; no abort frame sent')

    def reset_timeout(self):
        pass

    def close(self):
        if self.closed:
            return
        self.closed=True
        if self.stuck:
            # The MCU is still busy with the abandoned command; a synchronous
            # SCardDisconnect would block until its own ~48 s timeout.  Release
            # the handle in the background once the firmware frees it.
            probe.log('pcsc_close_deferred',device=self.device,mode=self.mode,
                      note='reader firmware still busy; handle released in background')
            threading.Thread(target=self.link.close,name='acr122-close',daemon=True).start()
            return
        try:
            self.link.close()
        finally:
            probe.log('pcsc_closed',device=self.device,mode=self.mode,rf_on=self.rf_on)
