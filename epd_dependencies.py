"""Dependency resolution without installation or network access."""
import importlib


def ensure_dependencies(*, serial_required=False, lzo_required=False, pcsc_required=False):
    for wanted,name,package in (
            (serial_required,'serial','pyserial==3.5'),
            (lzo_required,'lzokay','lzokay==2.1.0'),
            (pcsc_required,'smartcard','pyscard>=2.3,<3')):
        if not wanted: continue
        try:
            importlib.import_module(name)
        except ImportError as exc:
            raise RuntimeError(f'缺少依赖 {name}，请运行：python -m pip install {package}') from exc
