"""Recent refresh attempts store exact panel pixels, never the source image."""
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import base64
import json
from PIL import Image
from image_codec import ImageOptions,PreparedImage,PALETTE,pack_codes


class RefreshHistory:
    def __init__(self,path,*,read_existing=True):
        self.path=Path(path); self.entries=[]; self._cache={}
        if read_existing and self.path.exists():
            entries=json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(entries,list) or len(entries)>5: raise ValueError('Invalid refresh history')
            self.entries=entries
            for i in range(len(entries)): self.load(i)

    def add(self,prepared,label):
        entry=dict(label=label,time=datetime.now().isoformat(timespec='seconds'),
                   codes=base64.b64encode(prepared.codes).decode('ascii'),
                   source_sha256=prepared.source_sha256,options=asdict(prepared.options))
        entries=([entry]+self.entries)[:5]
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(entries,ensure_ascii=False),encoding='utf-8')
        temporary.replace(self.path)
        self.entries=entries; self._cache.clear()

    def load(self,index):
        if index in self._cache: return self._cache[index]
        row=self.entries[index]
        codes=base64.b64decode(row['codes'],validate=True)
        packed=pack_codes(codes)
        preview=Image.frombytes('P',(552,768),codes)
        preview.putpalette([v for rgb in PALETTE for v in rgb]+[0]*(768-12))
        preview=preview.convert('RGB')
        options=ImageOptions(**row['options'])
        prepared=PreparedImage(preview.copy(),preview,codes,packed,row['source_sha256'],options)
        self._cache[index]=prepared
        return prepared
