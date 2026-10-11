"""ISO14443-4 Type A codec and bounded PCD transport; INF is opaque, not APDU.

Frames passed to/from the link INCLUDE software CRC_A. The link must disable
hardware CRC. No RF lifecycle operations or application command knowledge here.
"""
from dataclasses import dataclass, replace
import math
import time
from protocol import crc_a, validate_crc

FWT_MAX_MS = 4096 / 13560 * 2**14


class SessionError(RuntimeError):
    """Exchange state is unusable; explicitly close and reactivate, never replay."""


class ProtocolError(SessionError): pass
class FrameError(SessionError): pass
class LinkTimeout(SessionError): pass


@dataclass(frozen=True)
class Block:
    kind: str
    bn: int = 0
    chaining: bool = False
    nak: bool = False
    cid: int | None = None
    nad: int | None = None
    inf: bytes = b''
    cid_power: int = 0  # received PICC power indication; not part of CID


def decode(raw):
    try:
        data = validate_crc(raw)
    except ValueError as exc:
        raise FrameError(str(exc)) from exc
    pcb, pos = data[0], 1
    cid = nad = None
    cid_power = 0
    if pcb & 8:
        if len(data) <= pos or data[pos] & 0x30 or data[pos] & 15 == 15:
            raise ProtocolError('Missing/invalid CID (0..14)')
        cid, cid_power, pos = data[pos] & 15, data[pos] >> 6, pos+1
    if pcb & 0xe2 == 0x02:
        kind = 'I'
        if pcb & 4:
            if len(data) <= pos:
                raise ProtocolError('Missing NAD')
            nad, pos = data[pos], pos+1
        return Block(kind, pcb & 1, bool(pcb & 16), cid=cid, nad=nad, inf=bytes(data[pos:]), cid_power=cid_power)
    if pcb & 0xe6 == 0xa2:
        if len(data) != pos:
            raise ProtocolError('R-block must not contain INF')
        return Block('R', pcb & 1, nak=bool(pcb & 16), cid=cid, cid_power=cid_power)
    if pcb & 0xf7 == 0xc2:
        if len(data) != pos:
            raise ProtocolError('S(DESELECT) must not contain INF')
        return Block('DESELECT', cid=cid, cid_power=cid_power)
    if pcb & 0xf7 == 0xf2:
        inf = bytes(data[pos:])
        if len(inf) != 1 or not 1 <= inf[0] & 63 <= 59:
            raise ProtocolError('S(WTX) requires one byte, WTXM 1..59')
        return Block('WTX', cid=cid, inf=inf, cid_power=cid_power)
    raise ProtocolError(f'Unsupported or reserved PCB {pcb:02X}')


def encode(block):
    if block.cid_power != 0:
        raise ValueError('PCD CID power bits must be zero')
    if block.bn not in (0,1) or (block.cid is not None and not 0 <= block.cid <= 14):
        raise ValueError('Invalid block number/CID')
    if not isinstance(block.inf, bytes):
        raise ValueError('INF must be bytes')
    if block.kind == 'I':
        if block.nak:
            raise ValueError('I-block has no NAK flag')
        pcb = 0x02 | block.bn | (0x10 if block.chaining else 0)
    elif block.kind == 'R':
        if block.chaining or block.nad is not None:
            raise ValueError('R-block has no chaining/NAD')
        pcb = 0xa2 | block.bn | (0x10 if block.nak else 0)
    elif block.kind in ('WTX','DESELECT'):
        if block.bn or block.chaining or block.nak or block.nad is not None:
            raise ValueError('S-block has no block number/chaining/NAK/NAD')
        pcb = 0xf2 if block.kind == 'WTX' else 0xc2
    else:
        raise ValueError('Unsupported block type')
    prologue = bytearray([pcb | (8 if block.cid is not None else 0) | (4 if block.nad is not None else 0)])
    if block.cid is not None: prologue.append(block.cid)
    if block.nad is not None: prologue.append(block.nad)
    body = bytes(prologue) + block.inf
    raw = body + crc_a(body)
    decode(raw)  # enforce length and reserved-value rules for controls
    return raw


def describe(raw):
    block = decode(raw)
    return dict(pcb=f'{raw[0]:02X}', type=block.kind,
                block_number=block.bn if block.kind in ('I','R') else None,
                chaining=block.chaining, nak=block.nak, cid=block.cid,
                nad=block.nad, cid_power=block.cid_power, inf=block.inf.hex(' '), crc_valid=True,
                crc_a=raw[-2:].hex(' '), frame_bytes=len(raw))


class IsoDep:
    """One activated PCD connection. Shared block number starts at 0 after RATS.

    PICC ACK to a chained PCD I(N) carries N; PCD ACK while receiving chaining
    carries the NEXT expected N. No automatic RF reset on errors.
    """
    def __init__(self, link, *, fsc, fsd, fwt_ms, cid=None, logger=None,
                 max_retries=2, max_wtx=20, max_frames=2048, max_inf=65536,
                 exchange_timeout_s=60, tx_inf_limit=None):
        if fsc not in (16,24,32,40,48,64,96,128,256) or fsd not in (16,24,32,40,48,64,96,128,256):
            raise ValueError('Unsupported FSC/FSD')
        if not math.isfinite(fwt_ms) or not 0 < fwt_ms <= FWT_MAX_MS or cid not in (None,*range(15)):
            raise ValueError('Invalid FWT/CID')
        if (any(type(value) is not int for value in (max_retries,max_wtx,max_frames,max_inf))
                or min(max_retries,max_wtx) < 0 or min(max_frames,max_inf) <= 0
                or not math.isfinite(exchange_timeout_s) or exchange_timeout_s <= 0):
            raise ValueError('Invalid transport budget')
        self.link, self.fsc, self.fsd, self.fwt_ms, self.cid = link, fsc, fsd, fwt_ms, cid
        self.log = logger or (lambda *a, **k: None)
        self.max_retries, self.max_wtx, self.max_frames, self.max_inf = max_retries,max_wtx,max_frames,max_inf
        self.exchange_timeout_s = exchange_timeout_s
        self.block_number = 0
        self.active = True
        self._busy = False
        self.max_tx_inf = fsc - 3 - int(cid is not None)
        if tx_inf_limit is not None:
            if type(tx_inf_limit) is not int or not 1<=tx_inf_limit<=self.max_tx_inf:
                raise ValueError('TX INF limit exceeds negotiated frame capacity')
            self.max_tx_inf = tx_inf_limit

    def _control(self, nak=False):
        return encode(Block('R', bn=self.block_number, nak=nak, cid=self.cid))

    def _step(self, frame, timeout_ms, retry):
        self._frames += 1
        if self._frames > self.max_frames or time.monotonic() + timeout_ms/1000 > self._deadline:
            raise SessionError('Exchange frame/time budget exhausted')
        if len(frame) > self.fsc:
            raise ProtocolError('TX frame exceeds FSC, including prologue and CRC')
        self.log('isodep_tx', raw=frame.hex(' '), **describe(frame), timeout_ms=timeout_ms,
                 retry=retry, rf_on=getattr(self.link,'rf_on',None))
        started = time.monotonic()
        try:
            raw = self.link.transceive(frame, timeout_ms)
        except Exception as exc:
            self.log('isodep_link_error', error=str(exc), error_type=type(exc).__name__, retry=retry,
                     elapsed_ms=(time.monotonic()-started)*1000, rf_on=getattr(self.link,'rf_on',None))
            raise
        try:
            if len(raw) > self.fsd:
                raise ProtocolError('RX frame exceeds negotiated FSD')
            block = decode(raw)
        except SessionError as exc:
            self.log('isodep_rx_invalid', raw=raw.hex(' '), error=str(exc),
                     crc_valid=(len(raw)>=3 and crc_a(raw[:-2])==raw[-2:]), retry=retry)
            raise
        self.log('isodep_rx', raw=raw.hex(' '), **describe(raw), retry=retry,
                 elapsed_ms=(time.monotonic()-started)*1000, rf_on=getattr(self.link,'rf_on',None))
        if block.cid != self.cid or block.nad is not None:
            raise ProtocolError('Unexpected CID or unsupported NAD')
        return block

    def _receive(self, outgoing, recovery, retry_base=0):
        timeout_ms = self.fwt_ms + 49152/13560  # reader delta-FWT ~3.625 ms
        retry = 0
        while True:
            try:
                block = self._step(outgoing, timeout_ms, retry_base+retry)
            except (FrameError, LinkTimeout) as exc:
                retry += 1
                if retry > self.max_retries:
                    raise SessionError('Receive recovery limit exceeded') from exc
                self.log('isodep_retry', reason=str(exc), retry=retry, action='R-block recovery')
                outgoing = recovery
                timeout_ms = self.fwt_ms + 49152/13560
                continue
            if block.kind != 'WTX':
                return block
            self._wtx += 1
            if self._wtx > self.max_wtx:
                raise SessionError('WTX budget exceeded')
            multiplier = block.inf[0] & 63
            timeout_ms = min(self.fwt_ms * multiplier, FWT_MAX_MS) + 49152/13560
            # PICC may report power level in the upper bits; PCD echoes WTXM only.
            outgoing = encode(Block('WTX', cid=self.cid, inf=bytes([multiplier])))
            self.log('isodep_wtx', multiplier=multiplier, power_level=block.inf[0]>>6,
                     timeout_ms=timeout_ms, count=self._wtx)

    def exchange(self, inf, *, deadline=None):
        if not self.active or self._busy:
            raise SessionError('Session inactive or an exchange is already running')
        if not isinstance(inf, bytes) or len(inf) > self.max_inf:
            raise ValueError('INF must be bytes within the configured size limit')
        if deadline is not None and not math.isfinite(deadline):
            raise ValueError('Deadline must be finite')
        self._busy = True
        self._frames = self._wtx = 0
        self._deadline = time.monotonic() + self.exchange_timeout_s
        if deadline is not None:
            self._deadline = min(self._deadline, deadline)
        chunks = [inf[i:i+self.max_tx_inf] for i in range(0,len(inf),self.max_tx_inf)] or [b'']
        try:
            for index, chunk in enumerate(chunks):
                more = index < len(chunks)-1
                sent = encode(Block('I', bn=self.block_number, chaining=more, cid=self.cid, inf=chunk))
                attempts = 0
                while True:
                    block = self._receive(sent, self._control(nak=True), attempts)
                    if block.kind == 'R':
                        resend = ((not block.nak and block.bn != self.block_number)
                                  or (block.nak and block.bn == self.block_number))
                        if resend:
                            attempts += 1
                            if attempts > self.max_retries:
                                raise SessionError('I-block retransmission limit exceeded')
                            self.log('isodep_retry', retry=attempts, action='same I-block', block_number=self.block_number)
                            continue
                        if more and not block.nak and block.bn == self.block_number:
                            self.block_number ^= 1
                            break
                        raise ProtocolError('Unexpected R-block for current send state')
                    if more or block.kind != 'I' or block.bn != self.block_number:
                        raise ProtocolError('Expected I response with current block number')
                    break
            result = bytearray()
            duplicate_count = 0
            while True:
                result.extend(block.inf)
                if len(result) > self.max_inf:
                    raise SessionError('Reassembled response exceeds size limit')
                self.block_number ^= 1
                if not block.chaining:
                    return bytes(result)
                previous = block
                while True:
                    ack = self._control()
                    block = self._receive(ack, ack, duplicate_count)
                    if block.kind != 'I':
                        raise ProtocolError('Expected chained I-block')
                    if block.bn == self.block_number:
                        duplicate_count = 0
                        break
                    if replace(block,cid_power=0) != replace(previous,cid_power=0):
                        raise ProtocolError('Duplicate block number with different content')
                    duplicate_count += 1
                    if duplicate_count > self.max_retries:
                        raise SessionError('Duplicate response limit exceeded')
                    self.log('isodep_retry', retry=duplicate_count, action='ACK duplicate without appending')
        except Exception as exc:
            self.active = False
            self.log('isodep_invalidated', error=str(exc), application_result='unknown; do not auto-replay',
                     rf_on=getattr(self.link,'rf_on',None))
            raise
        finally:
            self._busy = False
