# Run: python -m PyInstaller --noconfirm EInkTool.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

base=Path(SPECPATH)
datas=[]
binaries=[]
hiddenimports=['serial','serial.tools.list_ports','serial.tools.list_ports_windows','package_smoke',
               'acr122_reader','smartcard','smartcard.System','smartcard.scard','smartcard.pcsc']
for package in ('lzokay','smartcard'):
    data,binary,hidden=collect_all(package)
    datas+=data; binaries+=binary; hiddenimports+=hidden
a=Analysis([str(base/'epd_gui.py')],pathex=[str(base)],binaries=binaries,datas=datas,
           hiddenimports=hiddenimports,excludes=['pytest','numpy','matplotlib','IPython',
               'epd_backend','pm3_transport','pm3_iso_dep','pm3_research_cli'])
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='EInkTool',debug=False,
        strip=False,upx=False,console=False,disable_windowed_traceback=False)
