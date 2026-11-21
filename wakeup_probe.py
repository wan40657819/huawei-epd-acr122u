"""Experimental PN532 HSU short-frame probe; no PIN, APDU or tag writes."""
import datetime as dt
import argparse
import json
from pathlib import Path
import struct
import sys
import time

import serial
from protocol import validate_bcc, crc_a, validate_crc, cascade_piece, parse_ats

OUT = None
events = []


def start_logging(path):
    global OUT
    OUT = Path(path)
    OUT.mkdir(parents=True, exist_ok=False)
    events.clear()

def log(kind, **values):
    row = dict(time=dt.datetime.now().isoformat(), monotonic_ns=time.monotonic_ns(), kind=kind, **values)
    events.append(row)
    print(json.dumps(row), flush=True)
    if OUT is not None:
        (OUT / 'events.json').write_text(json.dumps(events, indent=2), encoding='utf-8')
        with (OUT / 'events.txt').open('a', encoding='utf-8') as stream:
            stream.write(f'[{row["time"]}] {kind} ' + json.dumps(values, ensure_ascii=False) + '\n')


class RFStatusError(RuntimeError):
    def __init__(self, message, status, registers):
        super().__init__(message)
        self.status = status
        self.registers = registers

class Reader:
    transport_name = 'PN532 HSU (115200)'

    def __init__(self, port, *, full_init=False):
        self.s = serial.Serial(port, 115200, timeout=0.05, write_timeout=1)
        self.rf_on = False
        self.deadline = None
        try:
            if full_init:
                # Exact user-verified bootstrap; reuse existing HSU parser/ACK path.
                self._command_prefix = b'\x55\x55' + bytes(14)
                self._require_ack = True
                if self.command(0x14, [1, 0x14, 0]) != b'':
                    raise RuntimeError('Unexpected SAMConfiguration payload')
                self._require_ack = False
            else:
                self.s.write(bytes.fromhex('55 55 00 00 00'))
                time.sleep(0.15)
                self.s.reset_input_buffer()
        except Exception:
            self.s.close()
            raise

    def command(self, cmd, data=b'', timeout=2):
        deadline = getattr(self, 'deadline', None)
        if deadline is not None:
            timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            raise TimeoutError('HSU deadline exhausted before sending')
        end = time.monotonic() + timeout
        if deadline is not None: end = min(end, deadline)
        body = bytes([0xd4, cmd]) + bytes(data)
        if len(body) > 265:
            raise ValueError('PN532 command exceeds 265-byte HSU body limit')
        if len(body) <= 255:
            frame = b'\x00\x00\xff' + bytes([len(body), -len(body) & 255]) + body + bytes([-sum(body) & 255, 0])
        else:
            hi, lo = divmod(len(body), 256)
            frame = b'\x00\x00\xff\xff\xff' + bytes([hi, lo, -(hi+lo) & 255]) + body + bytes([-sum(body) & 255, 0])
        prefix = getattr(self, '_command_prefix', b'')
        self._command_prefix = b''
        self._bounded_write(prefix + frame, end)
        raw = bytearray()
        cursor = 0
        ack_received = False
        while time.monotonic() < end:
            if deadline is not None and hasattr(self.s, 'timeout'):
                self.s.timeout = max(0, min(0.05, end - time.monotonic()))
            raw.extend(self.s.read(self.s.in_waiting or 1))
            while len(raw) >= cursor + 6:
                if raw[cursor:cursor+3] != b'\x00\x00\xff':
                    cursor += 1
                    continue
                n, lcs = raw[cursor+3:cursor+5]
                offset = 5
                if n == 0 and lcs == 255:
                    if getattr(self, '_require_ack', False) and raw[cursor+5] != 0:
                        raise RuntimeError('Invalid bootstrap ACK')
                    ack_received = True
                    cursor += 6
                    continue
                if n == 255 and lcs == 255:
                    if len(raw) < cursor + 8:
                        break
                    hi, lo, extended_lcs = raw[cursor+5:cursor+8]
                    if (hi + lo + extended_lcs) & 255:
                        log('hsu_invalid', reason='extended_length_checksum', tx=frame.hex(' '), rx=raw.hex(' '))
                        raise RuntimeError('Invalid extended frame length checksum')
                    n, offset = hi * 256 + lo, 8
                    if not 1 <= n <= 265:
                        raise RuntimeError('Unsupported extended response length')
                elif (n + lcs) & 255:
                    log('hsu_invalid', reason='length_checksum', tx=frame.hex(' '), rx=raw.hex(' '))
                    raise RuntimeError('Invalid frame length checksum')
                if len(raw) < cursor + n + offset + 2:
                    break
                packet = bytes(raw[cursor+offset:cursor+offset+n])
                if (sum(packet) + raw[cursor+offset+n]) & 255 or raw[cursor+offset+1+n] != 0:
                    log('hsu_invalid', reason='data_checksum_or_postamble', tx=frame.hex(' '), rx=raw.hex(' '))
                    raise RuntimeError('Invalid response checksum/postamble')
                log('hsu', tx=frame.hex(' '), rx=raw.hex(' '))
                if packet[:2] != bytes([0xd5, cmd+1]):
                    raise RuntimeError('Unexpected PN532 response: ' + packet.hex())
                if getattr(self, '_require_ack', False) and not ack_received:
                    raise RuntimeError('Missing bootstrap ACK')
                self._bounded_write(bytes.fromhex('00 00 FF 00 FF 00'), end)
                return packet[2:]
        log('hsu_timeout', tx=frame.hex(' '), rx=raw.hex(' '))
        raise TimeoutError('No complete PN532 response')

    def _bounded_write(self, data, end):
        remaining = end - time.monotonic()
        if remaining <= 0: raise TimeoutError('HSU deadline exhausted before write/ACK')
        old = getattr(self.s, 'write_timeout', None)
        configurable = hasattr(self.s, 'write_timeout')
        if configurable: self.s.write_timeout = min(old if old is not None else remaining, remaining)
        try: self.s.write(data)
        finally:
            if configurable: self.s.write_timeout = old

    def read(self, addresses):
        result = self.command(6, b''.join(struct.pack('>H', a) for a in addresses))
        if len(result) != len(addresses):
            raise RuntimeError('Unexpected register response length')
        return dict(zip(addresses, result))

    def write(self, values):
        self.command(8, b''.join(struct.pack('>HB', a, v) for a, v in values.items()))

    def field(self, enabled):
        self.command(0x32, [1, 3 if enabled else 2])
        self.rf_on = enabled
        log('rf_field', enabled=enabled)

    def abort(self):
        """HSU ACK abort (NXP manual 6.2.2), without resetting or cycling RF."""
        self.s.write(bytes.fromhex('00 00 FF 00 FF 00'))
        time.sleep(0.1)
        stale = self.s.read(self.s.in_waiting)
        log('hsu_abort', drained=stale.hex(' '), rf_on=self.rf_on)

    def reset_timeout(self):
        if hasattr(self.s, 'timeout'): self.s.timeout = 0.05

    def close(self):
        self.s.close()


def diagnostics(reader):
    # FIFOLevel only; do not consume FIFO through a diagnostic read.
    values = reader.read([0x6331, 0x6334, 0x6335, 0x6336, 0x6338,
                          0x633a, 0x633c, 0x633d, 0x633e])
    return {f'{key:04X}': f'{value:02X}' for key, value in values.items()}


def rf_transceive(reader, payload, *, stage, tx_bits, args, software_crc=False):
    started = time.monotonic()
    # Keep the proven 0x42 path. Firmware owns FIFO/IRQ/StartSend handling.
    # Disable hardware CRC in both directions: response retains on-air CRC.
    reader.write({0x6302: 0, 0x6303: 0, 0x633d: tx_bits % 8})
    config = reader.read([0x6302, 0x6303, 0x6304, 0x6305, 0x633d])
    if (config[0x6302] != 0 or config[0x6303] != 0
            or config[0x633d] != tx_bits % 8 or config[0x6304] & 3 != 3):
        raise RuntimeError('RF configuration readback failed')
    time.sleep(args.frame_delay_ms / 1000)
    log('rf_tx', stage=stage, data=payload.hex(' '), tx_bits=tx_bits,
        rf_on=bool(config[0x6304] & 3 == 3),
        tx_crc_hardware=False, rx_crc_hardware=False, software_crc=software_crc, bitrate=106,
        timeout_ms_actual=args.timeout_ms_actual,
        config={f'{k:04X}': f'{v:02X}' for k, v in config.items()})
    exchange_started = time.monotonic()
    try:
        response = reader.command(0x42, payload, timeout=max(2, args.timeout_ms_actual / 1000 + 1))
    except Exception as exc:
        log('rf_transport_failure', stage=stage, error=str(exc),
            elapsed_ms=(time.monotonic() - started) * 1000)
        raise
    exchange_ms = (time.monotonic() - exchange_started) * 1000
    registers = diagnostics(reader)
    log('rf_rx', stage=stage, status=response[0] if response else None,
        data=response[1:].hex(' '), diagnostics=registers,
        exchange_host_ms=exchange_ms, stage_host_ms=(time.monotonic() - started) * 1000)
    if not response or response[0] != 0:
        raise RFStatusError(f'{stage}: PN532 status {response[:1].hex() or "missing"}',
                            response[0] if response else None, registers)
    if int(registers['6336'], 16) != 0 or int(registers['633C'], 16) & 7:
        raise RFStatusError(f'{stage}: CIU error or incomplete received byte', -1, registers)
    return response[1:]


def activation(reader, args):
    atqa = rf_transceive(reader, b'\x7a', stage='SPECIAL_WAKE_7BIT', tx_bits=7, args=args)
    if atqa != b'\x04\x00':
        raise ValueError(f'Expected baseline ATQA 04 00, received {atqa.hex(" ")}')
    log('stage_pass', stage='SPECIAL_WAKE_7BIT', atqa=atqa.hex(' '))
    uid = b''
    for level, sel in enumerate((0x93, 0x95, 0x97), 1):
        part = rf_transceive(reader, bytes([sel, 0x20]),
                            stage=f'ANTICOLLISION_CL{level}', tx_bits=16, args=args)
        uid_part = validate_bcc(part)
        log('stage_pass', stage=f'ANTICOLLISION_CL{level}', uid_part=uid_part.hex(' '),
            bcc=f'{part[4]:02X}', bcc_valid=True, cascade_tag=uid_part[0] == 0x88)
        if args.stage == 'anticollision':
            log('done_read_only', completed_stage='anticollision', iso_dep_activated=False)
            return
        select_body = bytes([sel, 0x70]) + part
        select_frame = select_body + crc_a(select_body)
        raw_sak = rf_transceive(reader, select_frame, stage=f'SELECT_CL{level}',
                               tx_bits=len(select_frame)*8, args=args, software_crc=True)
        sak_data = validate_crc(raw_sak)
        if len(sak_data) != 1:
            raise ValueError('SAK must be exactly one byte plus CRC_A')
        sak = sak_data[0]
        uid += cascade_piece(uid_part, sak, level)
        log('stage_pass', stage=f'SELECT_CL{level}', sak=f'{sak:02X}',
            crc_a=raw_sak[-2:].hex(' '), crc_valid=True, cascade=bool(sak & 4))
        if not sak & 4:
            break
    log('selected', uid=uid.hex(' '), uid_bytes=len(uid), sak=f'{sak:02X}',
        supports_iso_dep=bool(sak & 0x20))
    if args.stage == 'select':
        log('done_read_only', completed_stage='select', iso_dep_activated=False)
        return
    if not sak & 0x20:
        raise ValueError('SAK does not advertise ISO-DEP; RATS not sent')
    rats = b'\xe0\x80'
    frame = rats + crc_a(rats)
    raw_ats = rf_transceive(reader, frame, stage='RATS', tx_bits=32,
                            args=args, software_crc=True)
    ats = parse_ats(validate_crc(raw_ats))
    log('stage_pass', stage='RATS_ATS', raw=raw_ats.hex(' '),
        crc_a=raw_ats[-2:].hex(' '), crc_valid=True, parsed=ats)
    log('done_read_only', completed_stage='ats', iso_dep_activated=True,
        note='May continue application-layer research in a future session; no APDU sent')
    return dict(uid=uid.hex(' '), atqa=atqa.hex(' '), sak=sak, ats=ats,
                ats_raw=raw_ats.hex(' '), fsdi=8, fsd=256, cid=0, bitrate=106)

def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True, help='PN532 HSU COM port')
    parser.add_argument('--probe', action='store_true', help='Original nine-frame wake/control experiment')
    parser.add_argument('--stage', choices=['anticollision', 'select', 'ats'])
    parser.add_argument('--prepower-ms', type=float, default=100)
    parser.add_argument('--frame-delay-ms', type=float, default=0)
    parser.add_argument('--timeout-ms', type=float, default=100)
    args = parser.parse_args()
    if args.probe and args.stage:
        parser.error('--probe and --stage are mutually exclusive')
    if not (0 < args.timeout_ms <= 3276.8) or not (0 <= args.prepower_ms <= 5000) or not (0 <= args.frame_delay_ms <= 1000):
        parser.error('Timing parameters out of supported range')
    timeout_index = next(i for i in range(1, 17) if 0.1 * 2 ** (i-1) >= args.timeout_ms)
    args.timeout_ms_actual = 0.1 * 2 ** (timeout_index-1)
    OUT = Path(__file__).resolve().parent / 'logs' / dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    OUT.mkdir(parents=True)
    log('session', parameters=vars(args), note='Experimental host timing; not RF timing')
    reader = Reader(args.port)
    saved = {}
    active = False
    try:
        firmware = reader.command(2)
        log('firmware', port=args.port, baud=115200, data=firmware.hex(' '))
        if len(firmware) != 4 or firmware[0] != 0x32:
            raise RuntimeError('Device is not an identified PN532')
        if not args.probe and not args.stage:
            return
        reader.command(0x14, [1, 0x14, 1])
        reader.command(0x12, [0x14])  # normal ISO14443-A parity, disable automatic RATS
        reader.command(0x32, [2, 0, 0x0b, 0x0a if args.probe else timeout_index])
        reader.command(0x32, [4, 0])  # no communication retries
        settings = {0x6302:0, 0x6303:0, 0x6305:0x40, 0x633d:7,
                    0x633c:0x10, 0x6308:0x85, 0x6309:0x4d, 0x6313:0x6f,
                    0x6315:0x87, 0x6316:0x59, 0x6317:0xf4, 0x6318:0x3f, 0x6319:0x11}
        saved = reader.read(list(settings))
        active = True
        if args.stage:
            reader.field(False)
            time.sleep(0.3)
            reader.write(settings)
            reader.field(True)
            time.sleep(args.prepower_ms / 1000)
            activation(reader, args)
            return
        for delay in (0.02, 0.1, 0.5):
            for opcode in (0x7a, 0x26, 0x52):
                reader.field(False)
                time.sleep(0.3)
                reader.write(settings)
                reader.field(True)
                time.sleep(delay)
                config = reader.read([0x6302,0x6303,0x6304,0x6305,0x633d])
                if config[0x633d] & 7 != 7 or config[0x6302] & 0xf3 or config[0x6303] & 0xf3:
                    raise RuntimeError('Short-frame configuration readback failed')
                response = reader.command(0x42, [opcode])
                diag = reader.read([0x6336,0x633c,0x633d])
                log('short_frame', opcode=f'{opcode:02X}', bits=7, crc=False,
                    bitrate=106, field_settle_seconds=delay,
                    config={hex(k):hex(v) for k,v in config.items()},
                    status=response[0] if response else None,
                    received=response[1:].hex(' '),
                    diagnostics={hex(k):hex(v) for k,v in diag.items()})
    except Exception as exc:
        log('failure', error=str(exc), type=type(exc).__name__)
        if active:
            try:
                log('failure_diagnostics', registers=diagnostics(reader))
            except Exception as diagnostic_error:
                log('diagnostic_failure', error=str(diagnostic_error))
        raise
    finally:
        try:
            if active:
                reader.field(False)
            if saved:
                reader.write(saved)
        finally:
            reader.s.close()
            log('closed', output=str(OUT))

if __name__ == '__main__':
    main()
