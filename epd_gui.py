"""Chinese desktop UI; hardware is opened only by explicit Detect/Write actions."""
import argparse
from collections import deque
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from queue import Queue,Empty
from threading import Thread,Event
import json
import os
import time
import webbrowser
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from PIL import Image,ImageTk
from image_codec import ImageOptions,prepare_image
from epd_protocol import build_transfer_plan,load_pin
from epd_dependencies import ensure_dependencies
from epd_export import export_job
from epd_writer import EpdWriter,WriteJob,JobEvent
from epd_logging import sanitize_text,JobLog
from epd_paths import data_dir
from epd_history import RefreshHistory

BASE=data_dir()


def enable_high_dpi():
    if os.name=='nt':
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError,OSError): pass


class PreviewState:
    def __init__(self):
        self.version=0; self.prepared_version=-1; self.prepared=None; self.busy=False; self.deferred=None
    def request_preview(self):
        if self.busy: raise ValueError('正在执行任务，不能修改图片')
        self.version+=1; self.deferred=None; return self.version
    def accept_preview(self,version,prepared):
        if version!=self.version: return False
        if self.busy:
            self.deferred=(version,prepared); return False
        self.prepared=prepared; self.prepared_version=version; return True
    def resume_preview(self):
        if not self.deferred or self.busy: return False
        version,prepared=self.deferred; self.deferred=None
        return self.accept_preview(version,prepared)
    @property
    def ready(self): return self.prepared is not None and self.prepared_version==self.version
    def snapshot(self,port,plan,pin,**timing):
        if not self.ready or self.busy or not port or plan is None: raise ValueError('图片尚未就绪或串口未选择')
        # Worker never mutates images, but copy to keep snapshot isolated from Tk state.
        from dataclasses import replace
        prepared=replace(self.prepared,source_rgb=self.prepared.source_rgb.copy(),preview_rgb=self.prepared.preview_rgb.copy())
        job=WriteJob(port,prepared,plan,pin,**timing)
        self.busy=True
        return job


class PreviewPane(ttk.Frame):
    def __init__(self,parent,title):
        super().__init__(parent,padding=8)
        self.original=None; self.photo=None
        bar=ttk.Frame(self); bar.pack(fill='x')
        ttk.Label(bar,text=title,style='Sub.TLabel').pack(side='left')
        self.zoom=tk.StringVar(value='适应窗口')
        control=ttk.Combobox(bar,textvariable=self.zoom,values=('适应窗口','100%'),state='readonly',width=10)
        control.pack(side='right'); control.bind('<<ComboboxSelected>>',lambda e:self.render())
        viewport=ttk.Frame(self); viewport.pack(fill='both',expand=True,pady=(8,0))
        area=ttk.Frame(viewport)
        self.canvas=tk.Canvas(area,bg='#e8edf3',highlightthickness=0)
        sx=ttk.Scrollbar(area,orient='horizontal',command=self.canvas.xview)
        sy=ttk.Scrollbar(area,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=sx.set,yscrollcommand=sy.set)
        self.canvas.grid(row=0,column=0,sticky='nsew'); sy.grid(row=0,column=1,sticky='ns'); sx.grid(row=1,column=0,sticky='ew')
        area.rowconfigure(0,weight=1); area.columnconfigure(0,weight=1)
        def layout(event):
            # Keep the actual canvas in a 2:3 portrait aspect, including on resize.
            cw=max(1,min(event.width-sy.winfo_reqwidth(),(event.height-sx.winfo_reqheight())*2/3))
            area.place(relx=.5,rely=.5,anchor='center',width=int(cw)+sy.winfo_reqwidth(),
                       height=int(cw*1.5)+sx.winfo_reqheight())
        viewport.bind('<Configure>',layout)
        self.canvas.bind('<Configure>',lambda e:self.render())
    def set_image(self,image):
        self.original=image; self.render()
    def render(self):
        self.canvas.delete('all')
        if self.original is None:
            self.canvas.create_text(max(80,self.canvas.winfo_width()/2),max(50,self.canvas.winfo_height()/2),text='选择图片后显示预览',fill='#68798a')
            return
        w=max(1,self.canvas.winfo_width()); h=max(1,self.canvas.winfo_height())
        image=self.original
        if self.zoom.get()!='100%':
            scale=min((w-20)/image.width,(h-20)/image.height)
            image=image.resize((max(1,int(image.width*scale)),max(1,int(image.height*scale))),Image.Resampling.NEAREST)
        self.photo=ImageTk.PhotoImage(image)
        x=max(0,(w-image.width)//2); y=max(0,(h-image.height)//2)
        self.canvas.create_image(x,y,image=self.photo,anchor='nw')
        self.canvas.configure(scrollregion=(0,0,max(w,image.width),max(h,image.height)))


class EpdApp:
    def __init__(self,root,*,offline=False):
        self.root=root; self.offline=offline; self.state=PreviewState(); self.queue=Queue()
        self.path=None; self.plan=None; self.worker=None; self.preview_worker=None; self.preview_pending=None
        self.pending_plan=None
        self.active_job=None; self.history_recorded=False; self.history_loaded=False
        history_error=None
        try: self.history=RefreshHistory(BASE/'refresh-history.json')
        except Exception as exc:
            history_error=str(exc); self.history=RefreshHistory(BASE/'refresh-history.json',read_existing=False)
        self.preview_after=None; self.cancel=Event(); self.closing=False; self.started=None; self.last_log=None
        self.controls=[]; self.port_map={}; self.records={'操作日志':deque(maxlen=2000),'通信详情':deque(maxlen=2000)}
        self.config_path=BASE/'epd-gui-settings.json'
        self.pin_error=None
        try: self.pin=load_pin()
        except Exception as exc: self.pin=b''; self.pin_error=str(exc)
        config={}; config_error=None
        try:
            if self.config_path.exists():
                config=json.loads(self.config_path.read_text(encoding='utf-8'))
                if not isinstance(config,dict): raise ValueError('配置应为JSON对象')
        except Exception as exc: config={}; config_error=str(exc)
        self.root.title('华为3.98寸墨水屏图片工具'+(' · 离线模式' if offline else ''))
        self.ui_scale=max(1,float(root.tk.call('tk','scaling'))*.75)
        self.root.geometry(f'{min(int(1180*self.ui_scale),root.winfo_screenwidth()-40)}x{min(int(980*self.ui_scale),root.winfo_screenheight()-80)}')
        self.root.minsize(int(960*self.ui_scale),int(680*self.ui_scale))
        style=ttk.Style(root)
        if 'vista' in style.theme_names(): style.theme_use('vista')
        style.configure('TLabel',font=('Microsoft YaHei UI',10))
        style.configure('TButton',font=('Microsoft YaHei UI',10),padding=(10,5))
        style.configure('Sub.TLabel',font=('Microsoft YaHei UI',11,'bold'))
        style.configure('Title.TLabel',font=('Microsoft YaHei UI',16,'bold'))
        self.status=tk.StringVar(value='请选择图片，处理后预览将与发送内容一致')
        self.filename=tk.StringVar(value='尚未选择图片')
        self.device=tk.StringVar(value='设备：尚未检测')
        self.progress_text=tk.StringVar(value='等待选图')
        self.elapsed=tk.StringVar(value='已用时 00:00')
        self.port=tk.StringVar()
        self.fit=tk.StringVar(value=config.get('fit') if config.get('fit') in ('留白','裁剪') else '留白')
        self.rotation=tk.StringVar(value=str(config.get('rotation',0)) if config.get('rotation',0) in (0,90,180,270) else '0')
        self.brightness=tk.DoubleVar(value=self._numeric(config.get('brightness',1)))
        self.contrast=tk.DoubleVar(value=self._numeric(config.get('contrast',1)))
        self.dither=tk.StringVar(value='Floyd–Steinberg' if config.get('dither') is True else '关闭')
        self.black_white=tk.BooleanVar(value=config.get('black_white') is True)
        value=config.get('scale_percent',100)
        self.scale_percent=tk.DoubleVar(value=value if type(value) in (int,float) and 10<=value<=300 else 100)
        self.timing_options=(
            ('上传包间隔(ms)','packet_interval_ms',(0,20,50,100,200,500),0),
            ('刷新前等待(s)','pre_refresh_wait_s',(0,1,2,3,5,10),0),
            ('D4上电等待(ms)','power_on_delay_ms',(100,500,1000,2000,3000,5000),2000),
            ('状态查询(ms)','poll_interval_ms',(120,250,500,1000),500))
        self.timing_vars={key:tk.StringVar(value=str(config.get(key) if type(config.get(key)) is int and config.get(key) in values else default))
                          for title,key,values,default in self.timing_options}
        self.search=tk.StringVar(); self.autoscroll=tk.BooleanVar(value=True)
        self.file_logging=tk.BooleanVar(value=False)
        self.file_logging_enabled=Event()
        self._build()
        legacy_ports=config.get('backend_ports',{})
        preferred=legacy_ports.get('PN532','COM17') if config.get('backend')=='PM3' and isinstance(legacy_ports,dict) else config.get('port','COM17')
        self.refresh_ports(preferred)
        for v in (self.fit,self.rotation,self.brightness,self.contrast,self.dither,self.black_white,self.scale_percent): v.trace_add('write',self.parameters_changed)
        self.search.trace_add('write',lambda *args:self.render_logs())
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.report_callback_exception=self.callback_exception
        self.local_log('欢迎使用。选择图片后，可先导出四色预览及离线数据。')
        if offline: self.local_log('离线模式：设备检测与刷图已禁用，不会打开串口。')
        if config_error: self.local_log('配置读取失败，已使用默认值：'+config_error,'WARN')
        if self.pin_error: self.local_log(self.pin_error+'；刷图已禁用。','ERROR')
        if history_error: self.local_log('刷新历史读取失败：'+history_error,'WARN')
        self.render_history()
        self._buttons(); self.root.after(50,self.poll)

    @staticmethod
    def _numeric(value):
        try: return max(.5,min(1.5,float(value)))
        except (ValueError,TypeError): return 1.0

    def button(self,parent,text,command,**pack):
        b=ttk.Button(parent,text=text,command=command); b.pack(side='left',padx=3,**pack)
        self.controls.append(b); return b

    def _build(self):
        outer=ttk.Frame(self.root,padding=14); outer.pack(fill='both',expand=True)
        top=ttk.Frame(outer); top.pack(fill='x')
        self.title_label=ttk.Label(top,text='华为3.98寸墨水屏图片工具',style='Title.TLabel')
        self.title_label.pack(side='left')
        ttk.Label(top,textvariable=self.device).pack(side='right')
        conn=ttk.Frame(outer); conn.pack(fill='x',pady=(10,7))
        ttk.Label(conn,text='PN532 串口').pack(side='left',padx=(0,8))
        self.port_control=ttk.Combobox(conn,textvariable=self.port,state='readonly',width=30)
        self.port_control.pack(side='left'); self.controls.append(self.port_control)
        self.refresh_button=self.button(conn,'刷新列表',self.refresh_ports)
        self.detect_button=self.button(conn,'检测设备',self.detect)
        self.open_logs_button=ttk.Button(conn,text='打开日志文件夹',command=self.open_logs); self.open_logs_button.pack(side='right')
        self.file_logging_control=ttk.Checkbutton(conn,text='日志保存至文件',variable=self.file_logging,command=self.toggle_file_logging)
        self.file_logging_control.pack(side='right',padx=(8,10))
        images=ttk.Frame(outer); images.pack(fill='x',pady=4)
        self.button(images,'选择图片',self.select_image)
        ttk.Label(images,textvariable=self.filename).pack(side='left',padx=8)
        settings=ttk.Frame(outer); settings.pack(fill='x',pady=4)
        image_controls_start=len(self.controls)
        self.black_white_control=ttk.Checkbutton(settings,text='黑白',variable=self.black_white)
        self.black_white_control.pack(side='left',padx=6); self.controls.append(self.black_white_control)
        for title,var,values,width in (('适配',self.fit,('留白','裁剪'),7),('旋转',self.rotation,('0','90','180','270'),6),('抖动',self.dither,('关闭','Floyd–Steinberg'),17)):
            ttk.Label(settings,text=title).pack(side='left',padx=(6,3))
            c=ttk.Combobox(settings,textvariable=var,values=values,state='readonly',width=width); c.pack(side='left'); self.controls.append(c)
        for title,var in (('亮度',self.brightness),('对比度',self.contrast)):
            ttk.Label(settings,text=title).pack(side='left',padx=(10,2))
            slider=ttk.Scale(settings,variable=var,from_=.5,to=1.5,length=110); slider.pack(side='left'); self.controls.append(slider)
        scaling=ttk.Frame(outer); scaling.pack(fill='x',pady=3)
        ttk.Label(scaling,text='图片缩放(%)').pack(side='left',padx=6)
        slider=ttk.Scale(scaling,variable=self.scale_percent,from_=10,to=300,length=220)
        slider.pack(side='left'); self.controls.append(slider)
        size=ttk.Spinbox(scaling,textvariable=self.scale_percent,from_=10,to=300,increment=1,width=7)
        size.pack(side='left',padx=6); self.controls.append(size)
        self.button(scaling,'100%',lambda:self.scale_percent.set(100))
        ttk.Label(scaling,text='缩小居中留白 · 放大居中裁切 · 屏幕尺寸552×768').pack(side='left',padx=8)
        self.button(settings,'重置处理',self.reset)
        self.image_controls=self.controls[image_controls_start:]
        timing=ttk.Frame(outer); timing.pack(fill='x',pady=4)
        self.timing_controls=[]
        for title,key,values,default in self.timing_options:
            ttk.Label(timing,text=title).pack(side='left',padx=(6,3))
            c=ttk.Combobox(timing,textvariable=self.timing_vars[key],values=tuple(map(str,values)),state='readonly',width=6)
            c.pack(side='left'); self.controls.append(c); self.timing_controls.append(c)
        export=ttk.Frame(outer); export.pack(fill='x',pady=4)
        self.export_png_button=self.button(export,'导出四色图片',self.export_png)
        self.export_data_button=self.button(export,'导出离线数据',self.export_data)
        self.preview_info=ttk.Label(export,text='目标 552 × 768 · 黑 / 白 / 黄 / 红'); self.preview_info.pack(side='right')
        split=ttk.Frame(outer); split.pack(fill='both',expand=True,pady=6)
        self.original_pane=PreviewPane(split,'原图'); self.result_pane=PreviewPane(split,'实际发送的四色点阵')
        self.original_pane.grid(row=0,column=0,sticky='nsew'); self.result_pane.grid(row=0,column=1,sticky='nsew')
        split.rowconfigure(0,weight=1); split.columnconfigure(0,weight=1,uniform='preview'); split.columnconfigure(1,weight=1,uniform='preview')
        self.history_panel=ttk.Frame(split,padding=6,width=130)
        self.history_panel.grid(row=0,column=2,sticky='nsew')
        ttk.Label(self.history_panel,text='刷新历史 · 最近5张').pack()
        self.history_buttons=[]; self.history_photos=[]
        for index in range(5):
            b=ttk.Button(self.history_panel,compound='top',command=lambda i=index:self.load_history(i))
            b.pack(fill='both',expand=True,pady=2); self.history_buttons.append(b)
        self.history_panel.bind('<Configure>',lambda event:self.render_history())
        run=ttk.Frame(outer); run.pack(fill='x',pady=(4,3))
        self.write_button=ttk.Button(run,text='开始刷图',command=self.write); self.write_button.pack(side='left',padx=3)
        self.cancel_button=ttk.Button(run,text='取消',command=self.request_cancel); self.cancel_button.pack(side='left',padx=3)
        ttk.Label(run,textvariable=self.status).pack(side='left',padx=12)
        ttk.Label(run,textvariable=self.elapsed).pack(side='right')
        self.progress=ttk.Progressbar(outer,mode='determinate',maximum=100); self.progress.pack(fill='x',pady=3)
        ttk.Label(outer,textvariable=self.progress_text).pack(anchor='w')
        logbar=ttk.Frame(outer); logbar.pack(fill='x',pady=(8,2))
        ttk.Label(logbar,text='日志搜索').pack(side='left')
        ttk.Entry(logbar,textvariable=self.search,width=30).pack(side='left',padx=6)
        ttk.Checkbutton(logbar,text='自动滚动',variable=self.autoscroll).pack(side='right')
        notebook=ttk.Notebook(outer); notebook.pack(fill='x')
        self.log_texts={}
        for name in self.records:
            page=ttk.Frame(notebook); notebook.add(page,text=name)
            text=tk.Text(page,height=5,font=('Consolas',9),wrap='none',state='disabled',bg='#f8fafc',relief='flat')
            sy=ttk.Scrollbar(page,command=text.yview); sx=ttk.Scrollbar(page,orient='horizontal',command=text.xview)
            text.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
            text.grid(row=0,column=0,sticky='nsew'); sy.grid(row=0,column=1,sticky='ns'); sx.grid(row=1,column=0,sticky='ew')
            page.columnconfigure(0,weight=1)
            for level,color in (('INFO','#263b50'),('WARN','#9b6500'),('ERROR','#b3261e')): text.tag_configure(level,foreground=color)
            self.log_texts[name]=text
        footer=ttk.Frame(outer); footer.pack(fill='x',pady=(8,0))
        self.bilibili_link=ttk.Label(footer,text='B站：氧化钙硼',foreground='#1769aa',cursor='hand2')
        self.bilibili_link.pack(side='left')
        self.bilibili_link.bind('<Button-1>',lambda event:webbrowser.open('https://space.bilibili.com/5902526'))
        self.gitee_link=ttk.Label(footer,text='开源仓库：https://gitee.com/kur0neko',foreground='#1769aa',cursor='hand2')
        self.gitee_link.pack(side='right')
        self.gitee_link.bind('<Button-1>',lambda event:webbrowser.open('https://gitee.com/kur0neko'))

    def toggle_file_logging(self):
        if self.file_logging.get(): self.file_logging_enabled.set()
        else: self.file_logging_enabled.clear()
        self.local_log('文件日志已开启；从此刻开始保存。' if self.file_logging.get() else '文件日志已关闭；界面日志继续显示。')

    def task_writer(self):
        writer=EpdWriter()
        writer.log_factory=lambda callback,pin:JobLog(
            BASE/'logs/epd_gui'/datetime.now().strftime('%Y%m%d_%H%M%S_%f'),
            callback,pin,enabled=self.file_logging_enabled.is_set)
        return writer

    def refresh_ports(self,preferred=None):
        if self.state.busy: return
        previous=self.port_map.get(self.port.get(),preferred or 'COM17')
        try:
            ensure_dependencies(serial_required=True)
            from serial.tools import list_ports
            values=list(list_ports.comports())
            self.port_map={f'{p.device} · {p.description}':p.device for p in values}
            self.port_control['values']=list(self.port_map)
            self.port.set(next((label for label,p in self.port_map.items() if p==previous),''))
        except Exception as exc: self.local_log(str(exc),'ERROR')
        self._buttons()

    def _buttons(self):
        busy=self.state.busy
        for c in self.controls:
            c.configure(state='disabled' if busy else 'readonly' if isinstance(c,ttk.Combobox) else 'normal')
        if self.history_loaded:
            for c in self.image_controls: c.configure(state='disabled')
        for i,b in enumerate(self.history_buttons):
            b.configure(state='disabled' if busy or i>=len(self.history.entries) else 'normal')
        ready=self.state.ready and not busy
        for c in (self.export_png_button,self.export_data_button): c.configure(state='normal' if ready else 'disabled')
        connected=bool(self.port_map.get(self.port.get()))
        self.detect_button.configure(state='normal' if connected and not busy and not self.offline else 'disabled')
        self.write_button.configure(state='normal' if ready and connected and not self.offline and not self.pin_error else 'disabled')
        self.cancel_button.configure(state='normal' if busy else 'disabled')

    def select_image(self):
        filename=filedialog.askopenfilename(title='选择图片',filetypes=[('图片','*.png *.jpg *.jpeg *.bmp')])
        if filename:
            self.history_loaded=False
            self.path=Path(filename); self.filename.set(self.path.name); self.parameters_changed()

    def options(self):
        return ImageOptions('cover' if self.fit.get()=='裁剪' else 'contain',int(self.rotation.get()),
                            self.brightness.get(),self.contrast.get(),self.dither.get()!='关闭',self.black_white.get(),self.scale_percent.get())

    def parameters_changed(self,*args):
        if self.state.busy or self.path is None: return
        version=self.state.request_preview()
        try: options=self.options()
        except (tk.TclError,ValueError):
            self.preview_pending=None; self.status.set('请输入有效的图片缩放百分比'); self._buttons(); return
        self.preview_pending=(version,self.path,options)
        self.status.set('正在准备四色预览…'); self._buttons()
        if self.preview_after is not None: self.root.after_cancel(self.preview_after)
        self.preview_after=self.root.after(180,self.start_preview)

    def start_preview(self):
        self.preview_after=None
        if self.preview_worker and self.preview_worker.is_alive(): return
        if self.preview_pending is None or self.state.busy: return
        version,path,options=self.preview_pending; self.preview_pending=None
        def work():
            try:
                prepared=prepare_image(path,options)
                plan=build_transfer_plan(prepared.packed)
                self.queue.put(('preview',version,prepared,plan))
            except Exception as exc: self.queue.put(('preview_error',version,str(exc)))
        self.preview_worker=Thread(target=work,name='image-preview',daemon=True); self.preview_worker.start()

    def reset(self):
        self.fit.set('留白'); self.rotation.set('0'); self.brightness.set(1); self.contrast.set(1); self.dither.set('关闭'); self.black_white.set(False); self.scale_percent.set(100)

    def render_history(self):
        if not hasattr(self,'history_buttons'): return
        photos=[]
        height=max(20,min(90,(self.history_panel.winfo_height()-30)//5-28))
        for i,b in enumerate(self.history_buttons):
            if i<len(self.history.entries):
                image=self.history.load(i).preview_rgb.copy()
                image.thumbnail((90,height),Image.Resampling.NEAREST)
                photo=ImageTk.PhotoImage(image); photos.append(photo)
                b.configure(image=photo,text=self.history.entries[i]['time'][11:19])
            else: b.configure(image='',text='暂无记录')
        self.history_photos=photos
        self._buttons()

    def load_history(self,index):
        if self.state.busy or index>=len(self.history.entries): return
        try:
            prepared=self.history.load(index); plan=build_transfer_plan(prepared.packed)
            version=self.state.request_preview()
            self.preview_pending=None; self.pending_plan=None
            if self.preview_after is not None: self.root.after_cancel(self.preview_after); self.preview_after=None
            self.state.accept_preview(version,prepared); self.plan=plan
            self.path=None; self.history_loaded=True
            self.filename.set('历史点阵 · '+self.history.entries[index]['time'])
            self.original_pane.set_image(None); self.result_pane.set_image(prepared.preview_rgb)
            self.preview_info.configure(text='552 × 768 · 历史点阵原样载入')
            self.status.set('历史点阵已就绪，可以导出或开始刷图')
            self.progress_text.set(f'53个压缩块 · {len(plan.packets)}个D3子包')
            self._buttons()
        except Exception as exc: self.local_log('历史载入失败：'+str(exc),'ERROR')

    def export_png(self):
        if not self.state.ready: return
        path=filedialog.asksaveasfilename(title='导出四色点阵',defaultextension='.png',filetypes=[('PNG','*.png')])
        if path:
            self.state.prepared.preview_rgb.save(path); self.local_log('已导出四色点阵：'+path)

    def export_data(self):
        if not self.state.ready: return
        parent=filedialog.askdirectory(title='选择离线数据保存目录')
        if not parent: return
        prepared=self.state.prepared; plan=self.plan
        self._start_task(lambda:export_job(prepared,plan,Path(parent)/datetime.now().strftime('offline_%Y%m%d_%H%M%S_%f'),save_logs=self.file_logging_enabled.is_set()),'正在导出离线数据')

    def detect(self):
        port=self.port_map.get(self.port.get())
        if self.offline or not port or self.state.busy: return
        writer=self.task_writer()
        self._start_task(lambda:writer.detect(port,self.cancel,self.enqueue),'正在检测设备')

    def write(self):
        if self.offline or self.pin_error: return
        writer=self.task_writer()
        timing=self.timing_values()
        job=self.state.snapshot(self.port_map.get(self.port.get()),self.plan,self.pin,
            packet_interval_s=timing['packet_interval_ms']/1000,
            pre_refresh_wait_s=timing['pre_refresh_wait_s'],power_on_delay_ms=timing['power_on_delay_ms'],
            poll_interval_s=timing['poll_interval_ms']/1000)
        self.active_job=job; self.history_recorded=False
        self._start_task(lambda:writer.write(job,self.cancel,self.enqueue),'正在开始刷图')

    def _start_task(self,work,message):
        if self.worker and self.worker.is_alive(): return
        self.state.busy=True; self.cancel.clear(); self.started=time.monotonic()
        self.status.set(message); self.progress_text.set(message)
        self.progress.configure(mode='indeterminate'); self.progress.start(12); self._buttons()
        def run():
            try: self.queue.put(('job_done',work()))
            except Exception as exc: self.queue.put(('job_error',str(exc)))
        self.worker=Thread(target=run,name='epd-job',daemon=False); self.worker.start()

    def enqueue(self,event): self.queue.put(('event',event))

    def request_cancel(self):
        if self.state.busy:
            self.cancel.set(); self.status.set('正在停止，等待当前通信结束…'); self.cancel_button.configure(state='disabled')

    def local_log(self,message,level='INFO'):
        message=sanitize_text(message,self.pin)
        self.records['操作日志'].append((datetime.now().strftime('%H:%M:%S')+' '+message,level)); self.render_logs()

    def process_event(self,event):
        row=event.payload; kind=event.kind; stamp=row.get('time','')[11:23]
        if (self.active_job is not None and not self.history_recorded and
                kind=='application_tx' and row.get('inf','').startswith('f0 d4 ')):
            self.history_recorded=True
            try:
                self.history.add(self.active_job.prepared,self.filename.get()); self.render_history()
            except Exception as exc: self.local_log('刷新历史保存失败：'+str(exc),'ERROR')
        text=json.dumps(row,ensure_ascii=False,default=str)
        level='ERROR' if kind=='error' else 'WARN' if kind=='warning' else 'INFO'
        self.records['通信详情'].append((text,level))
        if kind=='stage':
            self.status.set(row['message']); self.progress_text.set(row['message'])
            self.progress.stop(); self.progress.configure(mode='indeterminate'); self.progress.start(12)
        elif kind=='progress':
            self.progress.stop(); self.progress.configure(mode='determinate',value=100*row['confirmed']/row['total'])
            self.progress_text.set(f"已确认 {row['confirmed']} / {row['total']} 子包 · 块 {row['block']} / 包 {row['subpacket']}")
        elif kind=='poll': self.progress_text.set(f"等待刷新 {row['elapsed_s']:.1f} 秒 · 已查询 {row['count']} 次")
        elif kind=='device': self.device.set(f"设备：{row['uid'].upper()} · {row['width']}×{row['height']} · 四色")
        if kind in ('stage','device','error','warning','result','progress','timing'):
            message=row.get('message') or self.progress_text.get() if kind=='progress' else row.get('message') or f"任务结果：{row.get('outcome',kind)}"
            self.records['操作日志'].append((stamp+' '+message,level))

    def render_logs(self):
        if not hasattr(self,'log_texts'): return
        query=self.search.get().lower()
        for name,text in self.log_texts.items():
            position=text.yview()[0]
            text.configure(state='normal'); text.delete('1.0','end')
            for line,level in self.records[name]:
                if query in line.lower(): text.insert('end',line+'\n',level)
            text.configure(state='disabled')
            if self.autoscroll.get(): text.see('end')
            else: text.yview_moveto(position)

    def poll(self):
        dirty=False
        for _ in range(300):
            try: item=self.queue.get_nowait()
            except Empty: break
            kind=item[0]
            if kind=='preview':
                _,version,prepared,plan=item
                if self.state.accept_preview(version,prepared):
                    self.plan=plan
                    self.original_pane.set_image(prepared.source_rgb); self.result_pane.set_image(prepared.preview_rgb)
                    self.status.set('图片已就绪，可以导出或开始刷图')
                    self.progress_text.set(f'53个压缩块 · {len(plan.packets)}个D3子包 · 压缩后 {plan.compressed_bytes:,} 字节')
                    self.preview_info.configure(text=f'552 × 768 · 四色 · 原图 {prepared.source_rgb.width}×{prepared.source_rgb.height}')
                    self._buttons(); self.local_log('四色预览及传输计划生成完成。')
                elif version==self.state.version and self.state.busy:
                    self.pending_plan=(version,plan)
            elif kind=='preview_error':
                if item[1]==self.state.version:
                    self.status.set('图片处理失败'); self.local_log(item[2],'ERROR'); self._buttons()
            elif kind=='event': self.process_event(item[1]); dirty=True
            elif kind in ('job_done','job_error'):
                self.active_job=None
                self.state.busy=False; self.progress.stop(); self.progress.configure(mode='determinate',value=0)
                if self.state.resume_preview() and self.pending_plan:
                    self.plan=self.pending_plan[1]; self.pending_plan=None
                    self.original_pane.set_image(self.state.prepared.source_rgb)
                    self.result_pane.set_image(self.state.prepared.preview_rgb)
                self.worker=None
                if kind=='job_error':
                    self.status.set('任务失败'); self.local_log(item[1],'ERROR')
                    self.progress_text.set(sanitize_text(item[1],self.pin))
                elif isinstance(item[1],Path):
                    self.last_log=item[1]; self.status.set('离线数据导出完成'); self.progress_text.set('离线数据已保存，没有打开串口'); self.local_log('离线数据保存于：'+str(item[1]))
                else:
                    result=item[1]; self.last_log=result.log_directory
                    descriptions={'success':'刷图完成，设备已报告刷新完成','detected':'设备检测通过','failed':'任务失败',
                                  'cancelled':'设备已报告刷新完成' if result.device_completed else '已取消，设备最终状态未确认',
                                  'cleanup_failed':'设备完成，但关闭RF或串口失败' if result.device_completed else '检测结束，但清理失败'}
                    self.status.set(descriptions[result.outcome])
                    self.progress_text.set(result.error or result.cleanup_error or descriptions[result.outcome])
                    if result.outcome=='success': self.progress.configure(value=100)
                    if result.log_directory: self.local_log('已保存的日志位于：'+str(result.log_directory))
                self._buttons(); self.save_settings()
        if dirty: self.render_logs()
        if self.started is not None and self.state.busy:
            seconds=int(time.monotonic()-self.started); self.elapsed.set(f'已用时 {seconds//60:02d}:{seconds%60:02d}')
        if self.preview_pending and not (self.preview_worker and self.preview_worker.is_alive()) and self.preview_after is None:
            self.start_preview()
        if self.closing and not self.state.busy:
            self.save_settings(); self.root.destroy(); return
        self.root.after(50,self.poll)

    def timing_values(self):
        return {key:int(var.get()) for key,var in self.timing_vars.items()}

    def save_settings(self):
        try:
            config=dict(port=self.port_map.get(self.port.get(),''),fit=self.fit.get(),rotation=int(self.rotation.get()),
                        brightness=self.brightness.get(),contrast=self.contrast.get(),dither=self.dither.get()!='关闭',
                        black_white=self.black_white.get(),scale_percent=self.scale_percent.get())
            config.update(self.timing_values())
            self.config_path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception as exc: self.local_log('设置保存失败：'+str(exc),'WARN')

    def open_logs(self):
        path=self.last_log or BASE/'logs/epd_gui'; path.mkdir(parents=True,exist_ok=True)
        os.startfile(str(path))

    def close(self):
        self.closing=True
        if self.state.busy: self.request_cancel()
        else:
            self.save_settings(); self.root.destroy()

    def callback_exception(self,exc,value,tb):
        message=sanitize_text(str(value),self.pin)
        self.local_log(message,'ERROR'); messagebox.showerror('操作失败',message,parent=self.root)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--offline',action='store_true')
    parser.add_argument('--self-test',metavar='OUTPUT_DIRECTORY',help='离线打包自检，不打开硬件')
    args=parser.parse_args(argv)
    enable_high_dpi()
    if args.self_test:
        from package_smoke import run
        run(args.self_test)
        return 0
    root=tk.Tk()
    EpdApp(root,offline=args.offline); root.mainloop()
    return 0


if __name__=='__main__': raise SystemExit(main())
