"""Real Tk desktop smoke check. Explicitly blocks every serial open."""
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
import json
import time
import tkinter as tk
from PIL import Image,ImageDraw,ImageGrab
from epd_dependencies import ensure_dependencies
from epd_gui import EpdApp,enable_high_dpi


def main(*,screenshot=True):
    ensure_dependencies(serial_required=True)
    import serial
    target=Path(__file__).resolve().parent/'logs/gui-validation'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    target.mkdir(parents=True)
    sample=Image.new('RGB',(800,1100),'white'); draw=ImageDraw.Draw(sample)
    for i,color in enumerate(('black','white',(245,205,0),(200,0,0))):
        draw.rectangle((i*200,200,i*200+199,900),fill=color)
    sample.save(target/'source.png')
    settings=Path(__file__).resolve().parent/'epd-gui-settings.json'
    old_settings=settings.read_bytes() if settings.exists() else None
    enable_high_dpi()
    root=tk.Tk()
    assertions=[]
    def wait(predicate,limit=8):
        end=time.monotonic()+limit
        while not predicate():
            if time.monotonic()>end: raise TimeoutError('GUI verification timed out')
            root.update(); time.sleep(.02)
        root.update()
    try:
        with patch.object(serial,'Serial',side_effect=AssertionError('No hardware permitted')) as serial_open:
            legacy=json.loads(old_settings) if old_settings else {}
            legacy.update(force_power=True,force_rf_output=True,retry_on_error=True,force_poll_interval_ms=10,dither=True)
            settings.write_text(json.dumps(legacy),encoding='utf-8')
            app=EpdApp(root,offline=True)
            assert app.dither.get()=='Atkinson' and app.options().dither=='Atkinson'
            assert not any(hasattr(app,name) for name in ('force_power','force_rf_output','retry_on_error','force_interval_control'))
            for title,key,values,default in app.timing_options:
                app.timing_vars[key].set(str(default))
            assert app.timing_values()==dict(packet_interval_ms=0,pre_refresh_wait_s=0,
                power_on_delay_ms=2000,poll_interval_ms=500)
            app.timing_vars['packet_interval_ms'].set('50')
            app.timing_vars['pre_refresh_wait_s'].set('3')
            assert root.title()=='华为3.98寸墨水屏图片工具 · 离线模式'
            assert app.title_label.cget('text')=='华为3.98寸墨水屏图片工具'
            assert not hasattr(app,'backend_control') and not hasattr(app,'client_control')
            assert str(app.detect_button['state'])=='disabled'
            assert str(app.write_button['state'])=='disabled'
            app.detect(); app.write()
            app.save_settings()
            saved=json.loads(settings.read_text(encoding='utf-8'))
            assert 'backend' not in saved and 'pm3_client' not in saved
            assert saved['packet_interval_ms']==50 and saved['pre_refresh_wait_s']==3
            assert saved['power_on_delay_ms']==2000 and saved['poll_interval_ms']==500
            assert saved['dither']=='Atkinson'
            assert not set(saved)&{'force_power','force_rf_output','retry_on_error','force_poll_interval_ms'}
            assertions.append('PN532-only UI and updated titles; offline actions blocked')
            app.path=target/'source.png'; app.filename.set('四色测试图 · 离线验证'); app.parameters_changed()
            wait(lambda:app.state.ready)
            assertions.append('actual Tk: preview and transfer plan prepared')
            version=app.state.version
            job=app.state.snapshot('offline',app.plan,app.pin,packet_interval_s=.05,pre_refresh_wait_s=3,
                power_on_delay_ms=2000,poll_interval_s=.5)
            app._buttons()
            assert all(str(c['state'])=='disabled' for c in app.timing_controls)
            app.timing_vars['pre_refresh_wait_s'].set('5')
            assert job.pre_refresh_wait_s==3 and job.packet_interval_s==.05
            app.state.busy=False; app._buttons()
            assert all(str(c['state'])=='readonly' for c in app.timing_controls)
            assertions.append('experimental power/retry controls and persisted flags removed')
            assert app.state.version==version
            assertions.append('timing values saved; job snapshot immutable; timing controls locked during tasks')
            app.fit.set('裁剪'); app.dither.set('Floyd–Steinberg')
            wait(lambda:app.state.ready)
            assert app.state.prepared.options.dither=='Floyd–Steinberg'
            assertions.append('crop and dither controls regenerate current preview')
            app.black_white.set(True)
            wait(lambda:app.state.ready)
            assert app.state.prepared.options.black_white
            assert set(app.state.prepared.codes)<={0,1}
            app.save_settings()
            assert json.loads(settings.read_text(encoding='utf-8'))['black_white'] is True
            assertions.append('black-white control regenerates monochrome preview and saves setting')
            app.scale_percent.set(50)
            wait(lambda:app.state.ready)
            assert app.state.prepared.options.scale_percent==50
            app.save_settings()
            assert json.loads(settings.read_text(encoding='utf-8'))['scale_percent']==50
            assertions.append('50 percent image scaling regenerates preview and saves setting')
            app.result_pane.zoom.set('100%'); app.result_pane.render(); root.update()
            app.result_pane.zoom.set('适应窗口'); app.result_pane.render()
            with patch('epd_gui.filedialog.asksaveasfilename',return_value=str(target/'exported.png')):
                app.export_png()
            assert (target/'exported.png').exists()
            with patch('epd_gui.filedialog.askdirectory',return_value=str(target)):
                app.export_data()
            wait(lambda:not app.state.busy)
            assert app.last_log and (app.last_log/'apdus.json').exists()
            assertions.append('actual PNG and offline APDU export completed through UI actions')
            from epd_history import RefreshHistory
            from epd_writer import JobEvent
            app.history=RefreshHistory(target/'history.json')
            expected=app.state.prepared.packed
            app.active_job=app.state.snapshot('offline',app.plan,app.pin)
            app.history_recorded=False
            app.process_event(JobEvent('application_tx',dict(inf='f0 d4 85 80 00')))
            app.process_event(JobEvent('application_tx',dict(inf='f0 d4 85 80 00')))
            assert len(app.history.entries)==1
            app._buttons()
            assert str(app.history_buttons[0]['state'])=='disabled'
            app.state.busy=False; app.active_job=None; app._buttons()
            old_version=app.state.version; old_prepared=app.state.prepared
            app.history_buttons[0].invoke()
            assert app.state.ready and app.state.prepared.packed==expected
            assert app.original_pane.original is None
            assert app.result_pane.original is not None
            assert app.path is None and app.history_loaded
            assert str(app.black_white_control['state'])=='disabled'
            assert not app.state.accept_preview(old_version,old_prepared)
            assertions.append('D4 attempt stored once; history load keeps exact pixels, clears source preview and locks processing')
            app.search.set('导出'); root.update(); app.search.set('')
            root.update()
            box=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())
            if screenshot: ImageGrab.grab(bbox=box).save(target/'gui.png')
            else: assertions.append('screen capture explicitly skipped; Tk widget checks still executed')
            root.geometry(f'{int(960*app.ui_scale)}x{int(680*app.ui_scale)}'); root.update()
            assert app.result_pane.canvas.winfo_width()>100
            for pane in (app.original_pane,app.result_pane):
                ratio=pane.canvas.winfo_width()/pane.canvas.winfo_height()
                assert abs(ratio-2/3)<.025,ratio
            assertions.append('both preview canvases retain 2:3 portrait aspect after resize')
            with patch('epd_gui.filedialog.askopenfilename',return_value=str(target/'source.png')):
                app.select_image()
            wait(lambda:app.state.ready)
            assert not app.history_loaded and app.original_pane.original is not None
            assert str(app.black_white_control['state'])=='normal'
            assertions.append('selecting a new source exits history mode and restores image controls')
            assertions.append('window resize and log search exercised')
            assert not serial_open.called
            assertions.append('serial.Serial never called')
            app.close()
    finally:
        try: root.destroy()
        except tk.TclError: pass
        if old_settings is None: settings.unlink(missing_ok=True)
        else: settings.write_bytes(old_settings)
    (target/'verification.json').write_text(json.dumps(dict(mode='offline GUI, real Tk window',checks=assertions),ensure_ascii=False,indent=2),encoding='utf-8')
    print(target)


if __name__=='__main__': main()
