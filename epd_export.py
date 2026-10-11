"""Export image job without importing serial transport or opening hardware."""
from dataclasses import asdict
from pathlib import Path
import json
from image_codec import PALETTE
from epd_protocol import PN532_COMPRESSED_CHUNK,PN532_RF_INF_LIMIT


def job_metadata(prepared,plan):
    return dict(source_sha256=prepared.source_sha256,options=asdict(prepared.options),palette=PALETTE,
                packed_sha256=plan.packed_sha256,compressed_bytes=plan.compressed_bytes,
                blocks=plan.blocks,packets=len(plan.packets),width=552,height=768,
                compressed_chunk_limit=PN532_COMPRESSED_CHUNK,
                rf_inf_limit=PN532_RF_INF_LIMIT,
                uncompressed_bytes=len(prepared.packed),last_block_bytes=1984,padding_bytes=0)


def export_job(prepared,plan,destination: Path,*,save_logs=True):
    import hashlib
    if hashlib.sha256(prepared.packed).hexdigest()!=plan.packed_sha256:
        raise ValueError('图片与传输计划不一致')
    target=Path(destination); target.mkdir(parents=True,exist_ok=False)
    prepared.preview_rgb.save(target/'preview.png')
    (target/'packed.bin').write_bytes(prepared.packed)
    blocks=target/'blocks'; blocks.mkdir()
    for block in range(plan.blocks):
        (blocks/f'{block:03d}.lzo').write_bytes(b''.join(p.inf[7:] for p in plan.packets if p.block==block))
    rows=[dict(block=p.block,subpacket=p.subpacket,last=p.last_in_block,inf=p.inf.hex(' ')) for p in plan.packets]
    (target/'apdus.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    summary=dict(mode='offline',**job_metadata(prepared,plan))
    (target/'job-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    if save_logs:
        (target/'events.txt').write_text('离线导出完成；没有打开串口。\n',encoding='utf-8')
        (target/'events.json').write_text(json.dumps([dict(kind='offline_export',**summary)],ensure_ascii=False,indent=2),encoding='utf-8')
    return target
