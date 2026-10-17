# 划重点：若不想配置任何环境，请直接在开源页右侧下载Release版本（发行版）！双击即可运行上位机软件，一步到位

# 华为3.98寸墨水屏图片工具

本项目由 kur0neko 以 **PolyForm Noncommercial 1.0.0** 许可公开源码，允许许可范围内的非商业使用、修改和分享，商业用途不在本许可授权范围内。详见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。

使用 PN532 为兼容的 552 × 768 四色墨水屏生成并传输图片的 Windows 桌面工具。界面支持原图与四色预览、黑白处理、图片适配与旋转、10%～300%缩放、亮度/对比度、Floyd–Steinberg 抖动、最近5次刷新历史、离线数据导出、上传进度和可选通信日志。

仅支持 PN532，不包含 PM3 客户端或第三方应用。项目基于现有设备的协议研究，未声称兼容所有同尺寸屏幕，也不是华为官方软件。

## 硬件与环境

- 屏幕配置：552 × 768，黑/白/黄/红，支持本项目采用的图像压缩和命令格式。
- PN532 使用 HSU/UART 串口通信，115200 波特率。Windows 需要正确识别设备串口；板子供电与射频耦合必须满足实际刷新需求。
- 源码环境建议 Python 3.14、Windows 10/11 64 位；测试环境包含 Tkinter。单文件 EXE 不要求目标电脑安装 Python。
- 已按维护者授权内置其验证过的 5 字节认证 PIN，通常无需额外配置。维护者将其作为通用值提供，但所有同型号设备的兼容性仍需实测；程序不猜测或扫描 PIN。

## 从源码运行

在项目目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
.\.venv\Scripts\python.exe epd_gui.py
```

仅验证图片处理、禁止设备操作：

```powershell
.\.venv\Scripts\python.exe epd_gui.py --offline
```

也可在已经安装依赖的 Python 环境下双击 `start-epd-gui.cmd`。

## 可选设备认证配置

默认直接使用内置 PIN，**不需要创建配置文件，也不需要手机抓包或绑定日志**。如果自己的屏幕不接受内置值，并且你已经知道正确 PIN，可以用本地配置覆盖：

```powershell
Copy-Item epd-device-config.example.json epd-device-config.json
notepad epd-device-config.json
```

`pin_hex` 必须为 5 字节十六进制字符串，可包含空格。模板已填写维护者授权公开的内置值，需要覆盖时改成自己的有效 PIN。配置文件放在源码入口目录，使用 EXE 时放在 EXE 同目录。修改后重新启动程序。

没有配置文件时使用内置 PIN，不会因此禁用刷图。若配置文件存在但格式无效，则禁用刷图并提示错误，不会静默改用内置值；可修复或移除配置文件后重启。个人覆盖配置已列入 `.gitignore`。

## 使用流程

1. 接入 PN532，选择实际串口，将屏幕放在天线工作区域。
2. 选择图片，调整适配、旋转、亮度、对比度、抖动、黑白及缩放，以右侧点阵预览为准。缩放以适配结果为100%，缩小时居中留白，放大时居中裁切；实际发送尺寸仍为552×768。两个大预览画布保持2:3竖向比例。
3. 可先导出预览或离线数据，再执行“检测设备”。
4. 执行“开始刷图”。默认认证、上传、触发刷新与状态查询在同一 RF 会话内完成。
5. 任务中取消会等待当前通信结束，设备最终画面可能无法确认。

默认上传包间隔 0 ms、刷新前等待 0 s、D4 上电等待 2000 ms、状态查询间隔 500 ms，可在 GUI 调整。只读检测成功不代表完整刷新一定成功；供电不足或射频通信异常仍可能导致刷新失败。

右侧刷新历史保存最近5次已提交D4的任务，包括随后失败或取消的尝试，不表示设备刷新成功。点击小图会原样载入保存的点阵，原图区留空，图片处理控件锁定；选择新图片后恢复处理。历史保存在本地`refresh-history.json`，重启保留，与通信日志开关无关，不提交到仓库。

当前不提供强制供电、强制射频输出及错误后重试功能。D4只提交一次，普通刷新遇到应用层错误、传输错误、超时或取消即结束并清理RF及串口；刷新前保场等待仍保留。WTX默认每条命令最多50次，第51次失效，不累计DE查询次数；D4及后续DE共用默认60秒刷新预算。收发返回后也检查截止时间，迟到响应不会被认定成功。

“日志保存至文件”每次启动默认关闭；界面仍显示实时日志。勾选后从当前时刻保存，取消勾选停止后续写盘，不补录之前的事件。结束时保持勾选会生成 TXT、NDJSON、JSON 和总结；结束时未勾选则已保存的 TXT/NDJSON 保留，不再写总结。日志默认位于 `logs/epd_gui`，设置文件为 `epd-gui-settings.json`；所在目录不可写时回退到 `%LOCALAPPDATA%/EInkTool`。

主动导出图片或离线数据不受文件日志开关限制。日志会遮盖当前任务的认证 PIN，分享前仍请检查是否包含设备标识或图片内容。

## 打包单文件 EXE

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm EInkTool.spec
```

输出 `dist/EInkTool.exe`，包含 Python、Tk、Pillow、pyserial、lzokay 和授权公开的默认 PIN，无需额外配置认证文件或安装 Python。USB 串口驱动仍需系统识别。构建不会将个人覆盖配置、日志、图片或设置打入 EXE。

公开发行 EXE 请从本目录重新构建，以保证行为和公开源码一致。

发布或转发源码、修改版和 EXE 时，应保留许可原文（或其网址）及 `NOTICE` 中的署名通知。建议将 `LICENSE`、`NOTICE` 与 EXE 一同发布；这两个文本文件不是运行库，不影响 EXE 独立运行。`build-exe.ps1` 会将它们复制到 `dist`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe verify_gui_offline.py
.\.venv\Scripts\python.exe epd_gui.py --self-test logs/source-self-test
.\dist\EInkTool.exe --self-test logs/exe-self-test
```

传输和日志测试使用合成 PIN、生成图片及串口模拟数据；配置测试还验证内置 PIN 在没有配置文件时生效。不依赖私人抓包或打开真实硬件。GUI 测试需要可用的桌面会话。模拟测试通过和 EXE 离线自检通过不代表真实设备认证或刷新通过。

## 代码结构

| 文件 | 功能 |
| --- | --- |
| `epd_gui.py` | GUI、预览、任务队列和日志开关 |
| `image_codec.py` | 四色量化、抖动、点阵编码 |
| `epd_history.py` | 最近5次刷新点阵的本地保存与原样载入 |
| `epd_protocol.py` / `epd_writer.py` | 设备信息、认证配置、图像分包和刷图工作流 |
| `iso_dep.py` / `pn532_iso_dep.py` | ISO-DEP 编码、状态管理和 PN532 会话 |
| `wakeup_probe.py` / `protocol.py` | 已验证底层 HSU/RF 通信和 CRC/激活辅助函数 |
| `epd_logging.py` / `epd_paths.py` | 可选文件日志、脱敏和便携路径 |
| `epd_export.py` / `epd_dependencies.py` | 离线导出和依赖检查 |
| `research_cli.py` | 独立研究入口；GUI 不使用该入口 |
| `package_smoke.py` / `verify_gui_offline.py` | GUI 与 EXE 离线验证 |

底层收发、寄存器配置和激活流程沿用已验证实现。发布整理仅移除了工作区依赖路径；没有重写 PN532 驱动。

## 发布说明与许可证

本目录按维护者明确授权公开默认认证 PIN，其位于 `epd_protocol.py` 与配置模板中。不包含第三方 APK/IPA、反编译文件、PM3 代码、私人抓包、原始日志或个人覆盖配置。

项目自有源码采用 [PolyForm Noncommercial License 1.0.0](LICENSE)，版权署名为 **kur0neko**。允许在许可规定的用途范围内使用、修改和分发，要求保留许可及署名通知。未经另外授权，商业用途不在本许可范围内。

标准条款还明确允许特定公益组织、教育机构、公共研究机构、公共安全/卫生机构、环保组织及政府机构使用，不因其资金来源或资金义务而取消许可。具体边界以英文 `LICENSE` 原文为准。因商业用途限制，本项目属于**源码公开、非商业许可**，不属于 [OSI 定义](https://opensource.org/osd)的开源软件。

本许可仅适用于项目维护者有权授权的内容；Python、Tk、Pillow、pyserial、lzokay 与 PyInstaller 等第三方依赖仍适用各自的许可证。

作者：**kur0neko** · [B站：氧化钙硼](https://space.bilibili.com/5902526) · [Gitee](https://gitee.com/kur0neko)
