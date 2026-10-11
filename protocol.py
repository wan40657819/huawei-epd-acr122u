"""Pure ISO14443-A helpers. No serial access or hardware operations."""

def crc_a(data):
    crc = 0x6363
    for value in data:
        value ^= crc & 0xff
        value ^= (value << 4) & 0xff
        crc = ((crc >> 8) ^ (value << 8) ^ (value << 3) ^ (value >> 4)) & 0xffff
    return crc.to_bytes(2, 'little')


def validate_bcc(data):
    if len(data) != 5:
        raise ValueError(f'UID cascade response must contain 5 bytes, got {len(data)}')
    expected = data[0] ^ data[1] ^ data[2] ^ data[3]
    if data[4] != expected:
        raise ValueError(f'BCC mismatch: received {data[4]:02X}, expected {expected:02X}')
    return data[:4]


def validate_crc(frame):
    if len(frame) < 3 or crc_a(frame[:-2]) != frame[-2:]:
        raise ValueError(f'Invalid CRC_A or frame length: {frame.hex(" ")}')
    return frame[:-2]


def cascade_piece(uid_part, sak, level):
    if len(uid_part) != 4 or level not in (1, 2, 3):
        raise ValueError('Invalid cascade level or UID part length')
    more = bool(sak & 0x04)
    ct = uid_part[0] == 0x88
    if more != ct or (level == 3 and more):
        raise ValueError('Inconsistent cascade tag / SAK cascade bit / level')
    return uid_part[1:] if ct else uid_part


def parse_ats(data):
    """Parse ATS without its trailing CRC_A; caller must validate CRC first."""
    if not data or data[0] != len(data) or not 1 <= len(data) <= 20:
        raise ValueError('ATS TL does not match payload length (CRC excluded), or out of range')
    t0 = data[1] if len(data) > 1 else None
    if t0 is not None and (t0 & 0x80 or t0 & 0x0f > 8):
        raise ValueError('Unsupported/reserved ATS T0/FSCI')
    result = dict(tl=data[0], t0=t0, ta=None, tb=None, tc=None)
    pos = 2 if t0 is not None else 1
    for field, mask in [('ta', 0x10), ('tb', 0x20), ('tc', 0x40)]:
        if t0 is not None and t0 & mask:
            if pos >= len(data):
                raise ValueError(f'ATS is missing optional {field.upper()}')
            result[field] = data[pos]
            pos += 1
    fsci = t0 & 15 if t0 is not None else 2
    fwi = result['tb'] >> 4 if result['tb'] is not None else 4
    sfgi = result['tb'] & 15 if result['tb'] is not None else 0
    if fwi == 15 or sfgi == 15:
        raise ValueError('Reserved ATS FWI/SFGI value 15')
    result.update(fsci=fsci, fsc=[16,24,32,40,48,64,96,128,256][fsci],
                  fwi=fwi, sfgi=sfgi, fwt_ms=4096 / 13560 * 2**fwi,
                  sfgt_ms=4096 / 13560 * 2**sfgi if sfgi else None,
                  cid_supported=bool((result['tc'] or 0) & 2),
                  nad_supported=bool((result['tc'] or 0) & 1),
                  historical=data[pos:].hex(' '))
    return result
