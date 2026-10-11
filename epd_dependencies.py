"""Dependency resolution without installation or network access."""
import importlib


def ensure_dependencies(*, serial_required=False, lzo_required=False):
    for wanted,name in ((serial_required,'serial'),(lzo_required,'lzokay')):
        if not wanted: continue
        try:
            importlib.import_module(name)
        except ImportError as exc:
            package='pyserial==3.5' if name=='serial' else 'lzokay==2.1.0'
            raise RuntimeError(f'缺少依赖 {name}，请运行：python -m pip install {package}') from exc
