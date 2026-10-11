"""Single-session application workflow. No automatic application replay."""
from dataclasses import dataclass,field
from datetime import datetime
from pathlib import Path
import hashlib
import time
import math
from epd_protocol import (READ_INFO,READ_STATUS,parse_device_info,load_pin,
                          validate_target,require_success,parse_refresh_status,PN532_RF_INF_LIMIT,build_refresh_command)
from epd_logging import JobLog,sanitize_text
from epd_export import job_metadata
from epd_paths import data_dir


@dataclass(frozen=True)
class WriteJob:
    port: str
    prepared: object
    plan: object
    pin: bytes = field(default_factory=load_pin,repr=False)
    refresh_timeout_s: float = 60.0
    packet_interval_s: float = 0.0
    pre_refresh_wait_s: float = 0.0
    power_on_delay_ms: int = 2000
    poll_interval_s: float = 0.5


@dataclass(frozen=True)
class JobEvent:
    kind: str
    payload: dict


@dataclass(frozen=True)
class JobResult:
    outcome: str
    device_completed: bool
    confirmed_packets: int
    error: str | None
    cleanup_error: str | None
    log_directory: Path | None


class Cancelled(Exception): pass


def real_session(port):
    from epd_dependencies import ensure_dependencies
    ensure_dependencies(serial_required=True)
    import wakeup_probe as probe
    from pn532_iso_dep import PN532IsoDep
    return PN532IsoDep(probe.Reader(port,full_init=True),tx_inf_limit=PN532_RF_INF_LIMIT)


def default_log_factory(callback,pin):
    path=data_dir()/'logs/epd_gui'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    return JobLog(path,callback,pin)


class EpdWriter:
    def __init__(self,session_factory=real_session,log_factory=default_log_factory,clock=time.monotonic,sleep=time.sleep):
        self.session_factory,self.log_factory=session_factory,log_factory
        self.clock,self.sleep=clock,sleep

    def detect(self,port,cancel,emit):
        return self._run(port,None,cancel,emit)

    def write(self,job,cancel,emit):
        return self._run(job.port,job,cancel,emit)

    def _run(self,port,job,cancel,emit):
        pin=job.pin if job else b''
        logger=self.log_factory(lambda row:emit(JobEvent(row['kind'],row)),pin)
        session=None; completed=False; confirmed=0; error=None; cleanup_error=None; outcome='failed'
        info=None; started=self.clock(); phase='connecting'; poll_count=0
        backend=getattr(self.session_factory,'backend','PN532')
        metadata=dict(port=port,backend=backend,mode='hardware-write' if job else 'hardware-read-only')

        def check_cancel():
            if cancel.is_set(): raise Cancelled('已取消；设备最终状态未确认' if phase in ('refreshing','polling') else '已取消')

        def stage(name,message):
            nonlocal phase
            phase=name
            logger.emit('stage',stage=name,message=message)

        def exchange(inf,context,deadline=None):
            check_cancel()
            logger.emit('application_tx',context=context,inf=inf.hex(' '))
            response=session.exchange(inf,**({'deadline':deadline} if deadline is not None else {}))
            logger.emit('application_rx',context=context,inf=response.hex(' '),sw=response[-2:].hex(' ').upper())
            return response

        def wait_with_field(seconds):
            # Leave the activated session and RF field untouched; permit cancellation.
            until=self.clock()+max(0,seconds)
            while self.clock()<until:
                check_cancel()
                self.sleep(min(.05,until-self.clock()))
            check_cancel()

        with logger.capture_probe():
            try:
                if job and len(pin)!=5: raise ValueError('当前协议PIN必须为5字节，请配置自己的设备PIN')
                if job:
                    if not 0<job.refresh_timeout_s<=300: raise ValueError('刷新预算必须在0至300秒之间')
                    for name,value,minimum,maximum in (
                        ('上传包间隔',job.packet_interval_s,0,1),
                        ('刷新前等待',job.pre_refresh_wait_s,0,30),
                        ('状态查询间隔',job.poll_interval_s,.12,2)):
                        if not math.isfinite(value) or not minimum<=value<=maximum:
                            raise ValueError(f'{name}必须在{minimum}至{maximum}秒之间')
                    refresh_command=build_refresh_command(job.power_on_delay_ms)
                    metadata['timing']=dict(packet_interval_s=job.packet_interval_s,
                        pre_refresh_wait_s=job.pre_refresh_wait_s,power_on_delay_ms=job.power_on_delay_ms,
                        poll_interval_s=job.poll_interval_s,refresh_timeout_s=job.refresh_timeout_s)
                    logger.emit('timing',**metadata['timing'],message=(
                        f'时序：包间隔 {job.packet_interval_s*1000:g}ms，刷新前保场 {job.pre_refresh_wait_s:g}s，'
                        f'D4上电等待 {job.power_on_delay_ms}ms，状态查询 {job.poll_interval_s*1000:g}ms'))
                    if hashlib.sha256(job.prepared.packed).hexdigest()!=job.plan.packed_sha256:
                        raise ValueError('预览与传输计划哈希不一致')
                    metadata.update(job_metadata(job.prepared,job.plan))
                    logger.save_preview(job.prepared.preview_rgb)
                stage('connecting',f'正在打开{backend}串口')
                session=self.session_factory(port)
                check_cancel()
                stage('activating','正在唤醒并激活墨水屏')
                activation=session.activate()
                stage('checking','正在读取设备信息')
                info=parse_device_info(exchange(READ_INFO,'设备信息读取'))
                validate_target(info,bytes.fromhex(activation['uid']))
                metadata['device']=dict(uid=info.uid.hex(),width=info.width,height=info.height,
                                        colors=info.colors,firmware=info.firmware)
                logger.emit('device',**metadata['device'],message='目标屏幕检查通过')
                if job:
                    stage('authenticating','正在完成本地验证')
                    verify=bytes.fromhex('0020000105')+pin
                    for i in range(2): require_success(exchange(verify,f'本地验证{i+1}'),f'本地验证{i+1}')
                    stage('transferring','正在发送图片')
                    for index,packet in enumerate(job.plan.packets):
                        if index: wait_with_field(job.packet_interval_s)
                        require_success(exchange(packet.inf,f'D3块{packet.block}/包{packet.subpacket}'),'图像传输')
                        confirmed+=1
                        logger.emit('progress',confirmed=confirmed,total=len(job.plan.packets),
                                    block=packet.block,subpacket=packet.subpacket)
                    if job.pre_refresh_wait_s:
                        stage('precharging',f'刷新前保持射频场，等待 {job.pre_refresh_wait_s:g} 秒')
                        wait_with_field(job.pre_refresh_wait_s)
                    stage('refreshing','正在触发屏幕刷新')
                    deadline=self.clock()+job.refresh_timeout_s
                    require_success(exchange(refresh_command,'触发刷新',deadline),'触发刷新')
                    stage('polling','图片已传输，正在等待屏幕刷新')
                    while True:
                        check_cancel()
                        if self.clock()>=deadline: raise TimeoutError('屏幕刷新等待超过总预算')
                        sent_at=self.clock()
                        done=parse_refresh_status(exchange(READ_STATUS,'刷新状态',deadline))
                        poll_count+=1
                        logger.emit('poll',count=poll_count,elapsed_s=self.clock()-(deadline-job.refresh_timeout_s))
                        if done:
                            completed=True
                            break
                        wait_with_field(min(job.poll_interval_s-(self.clock()-sent_at),deadline-self.clock()))
                    outcome='success'
                else:
                    outcome='detected'
            except Cancelled as exc:
                outcome='cancelled'; error=str(exc)
                logger.emit('warning',message=error,stage=phase)
            except Exception as exc:
                error=str(exc)
                logger.emit('error',message=error,error_type=type(exc).__name__,stage=phase)
            finally:
                if session is not None:
                    try: session.close()
                    except Exception as exc:
                        cleanup_error=str(exc)
                        logger.emit('error',message='关闭RF或串口失败：'+cleanup_error)
                        if outcome in ('success','detected'): outcome='cleanup_failed'
        summary=dict(**metadata,outcome=outcome,device_completed=completed,confirmed_packets=confirmed,
                     error=error,cleanup_error=cleanup_error,last_stage=phase,polls=poll_count,elapsed_s=self.clock()-started)
        logger.emit('result',**summary)
        logger.finish(summary)
        return JobResult(outcome,completed,confirmed,sanitize_text(error,pin) if error else None,
                         sanitize_text(cleanup_error,pin) if cleanup_error else None,logger.directory)
