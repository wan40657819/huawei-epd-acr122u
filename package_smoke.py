"""Opt-in packaged offline check; never opens serial or sends RF commands."""
from pathlib import Path
from unittest.mock import patch
import json
import sys
import time
import tkinter as tk
from PIL import Image


def run(destination):
    import epd_gui
    from epd_logging import JobLog
    from epd_protocol import build_transfer_plan
    from image_codec import prepare_image,ImageOptions
    pin=bytes.fromhex('0102030405') # Synthetic offline test data only.
    target=Path(destination); target.mkdir(parents=True,exist_ok=True)
    source=target/'sample.png'; Image.new('RGB',(552,768),'white').save(source)
    root=tk.Tk(); root.withdraw()
    try:
        with patch('serial.Serial',side_effect=AssertionError('Hardware forbidden')):
            app=epd_gui.EpdApp(root,offline=True)
            assert root.title()=='华为3.98寸墨水屏图片工具 · 离线模式'
            assert app.title_label.cget('text')=='华为3.98寸墨水屏图片工具'
            assert not hasattr(app,'backend_control') and not hasattr(app,'client_control')
            assert not any(hasattr(app,name) for name in ('force_power','force_rf_output','retry_on_error','force_interval_control'))
            assert not any(n.startswith('pm3_') for n in sys.modules)
            assert not app.file_logging.get() and not app.file_logging_enabled.is_set()
            writer=app.task_writer(); rows=[]
            logger=writer.log_factory(rows.append,pin)
            logger.emit('stage',message='no file')
            assert logger.directory is None
            app.file_logging.set(True); app.toggle_file_logging()
            logger=JobLog(target/'enabled-log',rows.append,pin,enabled=app.file_logging_enabled.is_set)
            logger.emit('stage',message='saved')
            app.file_logging.set(False); app.toggle_file_logging()
            logger.emit('stage',message='not saved')
            assert len((target/'enabled-log/events.ndjson').read_text().splitlines())==1
            with patch('epd_gui.webbrowser.open') as opened:
                root.deiconify(); root.update()
                app.bilibili_link.event_generate('<Button-1>')
                app.gitee_link.event_generate('<Button-1>')
                assert [c.args[0] for c in opened.call_args_list]==[
                    'https://space.bilibili.com/5902526','https://gitee.com/kur0neko']
            prepared=prepare_image(source,ImageOptions())
            plan=build_transfer_plan(prepared.packed)
            assert len(prepared.packed)==105984 and plan.blocks==53
            app.original_pane.set_image(prepared.source_rgb)
            app.result_pane.set_image(prepared.preview_rgb); root.update()
            from PIL import ImageGrab
            box=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())
            screenshot_error=None
            try: ImageGrab.grab(bbox=box).save(target/'gui.png')
            except OSError as exc: screenshot_error=str(exc)
            report=dict(ok=True,frozen=bool(getattr(sys,'frozen',False)),executable=sys.executable,
                bundled_prefix=sys.prefix,blocks=plan.blocks,packets=len(plan.packets),
                default_logging=False,logging_toggle=True,links=True,preview=True,
                backends=['PN532 HSU','ACR122U PC/SC'],connection_selector=True,
                connection_values=list(app.port_map.values()),
                title=root.title(),experimental_modes_removed=True,
                screenshot_saved=screenshot_error is None,screenshot_error=screenshot_error)
            (target/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception:
        import traceback
        (target/'error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        raise
    finally:
        root.destroy()
