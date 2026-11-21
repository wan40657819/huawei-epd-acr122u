"""Connection device discovery: PN532 HSU serial ports and ACR122U PC/SC readers."""
from epd_dependencies import ensure_dependencies

ACR122_PREFIX='acr122:'


def device_backend(value):
    """Human-readable backend name for a stored connection value."""
    return 'ACR122U' if str(value).startswith(ACR122_PREFIX) else 'PN532'


def split_device(value):
    """Return (kind, target) where kind is 'acr122' or 'serial'."""
    value=str(value)
    if value.startswith(ACR122_PREFIX):
        return 'acr122',value[len(ACR122_PREFIX):]
    return 'serial',value


def enumerate_devices():
    """Return ([(label, value)], warnings); never opens a serial port or RF field."""
    entries=[]; warnings=[]
    try:
        ensure_dependencies(serial_required=True)
        from serial.tools import list_ports
        for port in list_ports.comports():
            label=f'PN532 串口 · {port.device} · {port.description or "未知设备"}'
            entries.append((label,port.device))
    except Exception as exc:
        warnings.append(str(exc))
    try:
        from acr122_reader import list_acr122_readers
        for name in list_acr122_readers():
            entries.append((f'ACR122U · {name}',ACR122_PREFIX+name))
    except Exception as exc:
        warnings.append(f'ACR122U/PCSC 不可用：{exc}')
    return entries,warnings
