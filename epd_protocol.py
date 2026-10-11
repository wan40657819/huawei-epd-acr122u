"""Application protocol for the capture-verified display profile, no I/O."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from epd_dependencies import ensure_dependencies
from epd_paths import app_dir

READ_INFO=bytes.fromhex('00D1000000')
# Published with the maintainer's explicit authorization; verified on their display.
# Compatibility with other devices remains to be tested, so allow a local override.
DEFAULT_PIN=bytes.fromhex('5265699919')
REFRESH=bytes.fromhex('F0D4858000')


def build_refresh_command(power_on_delay_ms):
    """D4 P1: skip detection (bit 7), NFC power-on delay (100 ms units)."""
    if type(power_on_delay_ms) is not int or not 100<=power_on_delay_ms<=12700 or power_on_delay_ms%100:
        raise ValueError('D4上电等待必须为100至12700ms，且为100ms的整数倍')
    return bytes((0xF0,0xD4,0x80 | (power_on_delay_ms//100),0x80,0))
READ_STATUS=bytes.fromhex('F0DE000001')
# Device application stride must remain the captured 250 bytes. Constrain RF
# frames separately: changing application stride broke multi-subpacket LZO blocks.
PN532_COMPRESSED_CHUNK=250
PN532_RF_INF_LIMIT=240


def load_pin(path=None):
    path=Path(path) if path is not None else app_dir()/'epd-device-config.json'
    if not path.exists(): return DEFAULT_PIN
    try:
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        pin=bytes.fromhex(data['pin_hex'])
        if len(pin)!=5: raise ValueError('length')
        return pin
    except (ValueError,KeyError,TypeError) as exc:
        raise ValueError('设备配置无效：pin_hex必须是5字节十六进制；不会尝试其他PIN') from exc


@dataclass(frozen=True)
class DeviceInfo:
    uid: bytes
    width: int
    height: int
    colors: int
    compression: bool
    firmware: int
    tlv: dict


@dataclass(frozen=True)
class TransferPacket:
    inf: bytes
    block: int
    subpacket: int
    last_in_block: bool


@dataclass(frozen=True)
class TransferPlan:
    packets: tuple
    packed_sha256: str
    compressed_bytes: int
    blocks: int


def require_success(response: bytes, context: str) -> bytes:
    if len(response)<2 or response[-2:]!=b'\x90\x00':
        sw=response[-2:].hex().upper() if len(response)>=2 else '缺失'
        meanings={'6992':'数据解压失败','6993':'解压后的数据长度不正确','68CA':'低电压报警','68CB':'屏幕刷新超时报警'}
        explanation=f'（{meanings[sw]}）' if sw in meanings else ''
        raise ValueError(f'{context}失败，状态字：{sw}{explanation}')
    return response[:-2]


def parse_device_info(response: bytes) -> DeviceInfo:
    data=require_success(response,'设备信息读取')
    fields={}; i=0
    while i<len(data):
        if i+2>len(data): raise ValueError('D1 TLV头截断')
        tag,n=data[i:i+2]; i+=2
        if tag in fields or i+n>len(data): raise ValueError('D1 TLV重复或截断')
        fields[tag]=data[i:i+n]; i+=n
    for tag,n in ((0xa0,7),(0xb1,1),(0xb2,1),(0xc0,4),(0xc1,4)):
        if len(fields.get(tag,b''))!=n: raise ValueError(f'D1字段{tag:02X}缺失或长度异常')
    width=int.from_bytes(fields[0xa0][5:7],'big')
    height=int.from_bytes(fields[0xa0][3:5],'big')
    colors=fields[0xb1][0]
    # Observed app correction, scoped to this exact screen geometry.
    if (width,height,colors)==(552,1536,2): height,colors=768,4
    return DeviceInfo(fields[0xc1],width,height,colors,bool(fields[0xc0][3]),fields[0xb2][0],fields)


def validate_target(info: DeviceInfo, activation_uid: bytes):
    if len(activation_uid)!=4 or info.uid!=activation_uid:
        raise ValueError('激活UID与设备信息UID不一致或UID格式不支持，停止写入')
    if (info.width,info.height,info.colors)!=(552,768,4) or not info.compression:
        raise ValueError('设备尺寸、颜色或压缩支持与当前配置不符')


def split_compressed(data: bytes, block: int, *, chunk_size=250) -> tuple:
    if not 1<=chunk_size<=250 or not data or not 0<=block<=255 or len(data)>chunk_size*256:
        raise ValueError('压缩块长度或编号超出范围')
    pieces=[data[i:i+chunk_size] for i in range(0,len(data),chunk_size)]
    return tuple(TransferPacket(bytes([0xf0,0xd3,0,int(i==len(pieces)-1),len(p)+2,block,i])+p,
                                block,i,i==len(pieces)-1) for i,p in enumerate(pieces))


def build_transfer_plan(packed: bytes) -> TransferPlan:
    if len(packed)!=105984: raise ValueError('点阵必须为105984字节')
    ensure_dependencies(lzo_required=True)
    import lzokay
    packets=[]; total=0
    for block,start in enumerate(range(0,len(packed),2000)):
        raw=packed[start:start+2000]
        compressed=lzokay.compress(raw)
        if lzokay.decompress(compressed,len(raw))!=raw: raise ValueError('LZO往返校验失败')
        # This binding pads its return to requested capacity; test a smaller
        # capacity to establish that the stream actually emits len(raw) bytes.
        try:
            lzokay.decompress(compressed,len(raw)-1)
        except lzokay.OutputOverrunError:
            pass
        else:
            raise ValueError('LZO实际解压长度不符合原块长度')
        total+=len(compressed)
        packets.extend(split_compressed(compressed,block,chunk_size=PN532_COMPRESSED_CHUNK))
    return TransferPlan(tuple(packets),hashlib.sha256(packed).hexdigest(),total,53)


def parse_refresh_status(response: bytes) -> bool:
    data=require_success(response,'刷新状态查询')
    if data not in (b'\x00',b'\x01'): raise ValueError('设备返回未知刷新状态')
    return data==b'\x00'
