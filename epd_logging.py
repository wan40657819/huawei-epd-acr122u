"""GUI-only incremental logs; capture existing logger without altering RF code."""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import json
import re
import time


def sanitize_text(value,pin):
    if not pin: return value
    pattern=re.compile(r'[\s]*'.join(f'{v:02x}' for v in pin),re.I)
    return pattern.sub('XX '*4+'XX',value).replace(repr(pin),'[认证数据已遮盖]')


class JobLog:
    def __init__(self,directory: Path,on_event,pin: bytes,*,enabled=lambda:True):
        self.target=Path(directory)
        self.directory=None
        self.enabled=enabled
        self.on_event=on_event
        self.pin=pin
        self.pattern=re.compile(r'[\s]*'.join(f'{v:02x}' for v in pin),re.I) if pin else None
        self.finished=False
    def _ensure_directory(self):
        if self.directory is None:
            self.target.mkdir(parents=True,exist_ok=False)
            self.directory=self.target
    def save_preview(self,image):
        if self.enabled():
            self._ensure_directory()
            image.save(self.directory/'preview.png')

    def _sanitize(self,value):
        if isinstance(value,bytes): value=value.hex(' ')
        if isinstance(value,str):
            return sanitize_text(value,self.pin)
        if isinstance(value,dict): return {str(k):self._sanitize(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)): return [self._sanitize(v) for v in value]
        return value

    def emit(self,kind,**values):
        original=dict(time=datetime.now().isoformat(),monotonic_ns=time.monotonic_ns(),kind=kind,**values)
        row=self._sanitize(original)
        if row!=original: row['hex_redacted']=True
        encoded=json.dumps(row,ensure_ascii=False,default=str)
        if self.enabled():
            self._ensure_directory()
            with (self.directory/'events.ndjson').open('a',encoding='utf-8') as f: f.write(encoded+'\n')
            with (self.directory/'events.txt').open('a',encoding='utf-8') as f: f.write(encoded+'\n')
        self.on_event(row)

    @contextmanager
    def capture_probe(self):
        import wakeup_probe as probe
        original=probe.log
        probe.log=self.emit
        try: yield self
        finally: probe.log=original

    def finish(self,summary):
        if self.finished: return
        self.finished=True
        if not self.enabled() or self.directory is None: return
        with (self.directory/'events.json').open('w',encoding='utf-8') as out:
            out.write('[\n'); first=True
            source=self.directory/'events.ndjson'
            if source.exists():
                with source.open(encoding='utf-8') as stream:
                    for row in stream:
                        if not first: out.write(',\n')
                        out.write(row.rstrip()); first=False
            out.write('\n]\n')
        (self.directory/'job-summary.json').write_text(json.dumps(self._sanitize(summary),ensure_ascii=False,indent=2,default=str),encoding='utf-8')
