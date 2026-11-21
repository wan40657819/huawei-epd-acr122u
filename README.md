Add ACR122U support based on the [huawei-epd-pn532](https://gitee.com/kur0neko/huawei-epd-pn532) project by kur0neko.

Due to an antenna coupling issue with the ACR122U, only very simple drawings with a D3 payload < 46B can be uploaded to the e-ink panel. Additionally, power from the antenna alone is insufficient, so an external power supply is required. Both 9V and 6V were tested:
* **9V:** Worked
* **6V:** Failed

*This work was assisted by AI.*

# 划重点：若不想配置任何环境，请直接在开源页右侧下载Release版本（发行版）！双击即可运行上位机软件，一步到位

# 华为3.98寸墨水屏图片工具

本项目由 kur0neko 以 **PolyForm Noncommercial 1.0.0** 许可公开源码，允许许可范围内的非商业使用、修改和分享，商业用途不在本许可授权范围内。详见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。

使用 PN532（HSU 串口或 ACR122U USB PC/SC）为兼容的 552 × 768 四色墨水屏生成并传输图片的 Windows 桌面工具。界面支持原图与四色预览、黑白处理、图片适配与旋转、10%～300%缩放、亮度/对比度、默认Atkinson抖动（可选Floyd–Steinberg或关闭）、最近5次刷新历史、离线数据导出、上传进度和可选通信日志。

仅支持 PN532 系列读写器（HSU 串口或 ACR122U 的 PC/SC 直通），不包含 PM3 客户端或第三方应用。项目基于现有设备的协议研究，未声称兼容所有同尺寸屏幕，也不是华为官方软件。

## 硬件与环境

- 屏幕配置：552 × 768，黑/白/黄/红，支持本项目采用的图像压缩和命令格式。
- PN532 使用 HSU/UART 串口通信，115200 波特率。Windows 需要正确识别设备串口；板子供电与射频耦合必须满足实际刷新需求。
- ACR122U 使用 USB PC/SC（pyscard）。Windows 首次使用需以管理员身份启用该读卡器的 PC/SC Escape 命令，详见下节；程序通过 ACR122U 直通 PN532 命令发送本项目所需的 7 位唤醒帧和 ISO-DEP 数据，普通 PC/SC APDU 接口不能完成该屏幕的唤醒。
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

## 使用 ACR122U（USB PC/SC）

1. `requirements-gui.txt` 已包含 `pyscard`，按上节安装依赖即可。
2. **首次使用**需以管理员身份打开 PowerShell，在项目目录执行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\enable-acr122-escape.ps1
   ```

   脚本会为已连接的 ACR122U 在 `Device Parameters` 及其 `WudfUsbccidDriver` 子项写入 `EscapeCommandEnable=1`，然后请拔插读卡器。若不想运行脚本，也可手动在
   `HKLM\SYSTEM\CurrentControlSet\Enum\USB\VID_072F&PID_2200\<实例>\Device Parameters`
   和其 `WudfUsbccidDriver` 子项下新建 DWORD `EscapeCommandEnable=1`。未启用时程序会停止并显示具体注册表路径，不会静默改用其他通信方式。
3. 启动程序，在“连接设备”中选择 `ACR122U · ACS ACR122U PICC Interface 0`，把屏幕放在读卡器天线上，先“检测设备”，再“开始刷图”。
4. ACR122U 的天线尺寸和供电能力与独立 PN532 模块不同。开发验证中，ACR122U 能完成目标屏幕检测、本地 PIN 验证，并完成十八次完整四色刷新（全黑、全红、四色竖条纹、20 列渐变、黑白交替竖带、三种 2D 棋盘、经典 8 px 方格棋盘、250/375 px 大格四色棋盘，每次约 25 秒）。**程序内置提醒机制**：ACR122U 上的刷图任务若单包载荷超过实测验证的 42 字节包络，会在打开硬件前给出警告提醒（GUI 日志以 WARN 显示）但不再拦截写入——实测超包络的包会让标签在帧内掉电，单次失败可能卡住读卡器约 1 分钟；照片等大载荷图片建议改用串口 PN532。传输层与串口路径逐字节一致，并按串口同样的主机超时执行。多色内容需要在 GUI 时序中把“刷新前等待”设为 1 秒：不设等待时标签在 `D4` 处请求 WTX 后掉电静默；等待也不宜超过 2 秒。要让多色图片可靠传输，需保证每个 2000 字节块解压后内容一致或周期极短（例如按块边界对齐的竖条纹、8 px 小方格棋盘）。大格棋盘有可离线预测的设计规则：设备按列主序打包（每字节 4 个纵向像素、自右向左），格宽取 125/250/375 px（格宽×192 字节恰为 2000 的整数倍）并把残余窄列留在左缘，即可全程单模式、38–42 B 传输；未对齐宽度（如 138 px）的格缝会切在块中产生超限包而失败。完整刷新需外部供电（无源状态下刷新返回 `68CA` 低电压报警）。

ACR122U 路径复用项目已验证的 PN532 寄存器、7 位唤醒、防冲突、RATS 和 ISO-DEP 代码，只把 HSU 串口传输替换为 PC/SC 直通命令。普通 PC/SC APDU 方式无法发送该屏幕需要的特殊唤醒帧，也无法保持项目验证过的应用分包。

## 可选设备认证配置

默认直接使用内置 PIN，**不需要创建配置文件，也不需要手机抓包或绑定日志**。如果自己的屏幕不接受内置值，并且你已经知道正确 PIN，可以用本地配置覆盖：

```powershell
Copy-Item epd-device-config.example.json epd-device-config.json
notepad epd-device-config.json
```

`pin_hex` 必须为 5 字节十六进制字符串，可包含空格。模板已填写维护者授权公开的内置值，需要覆盖时改成自己的有效 PIN。配置文件放在源码入口目录，使用 EXE 时放在 EXE 同目录。修改后重新启动程序。

没有配置文件时使用内置 PIN，不会因此禁用刷图。若配置文件存在但格式无效，则禁用刷图并提示错误，不会静默改用内置值；可修复或移除配置文件后重启。个人覆盖配置已列入 `.gitignore`。

## 使用流程

1. 接入 PN532 串口或 ACR122U（见“使用 ACR122U”），在“连接设备”中选择实际设备，将屏幕放在天线工作区域。
2. 选择图片，调整适配、旋转、亮度、对比度、抖动、黑白及缩放，以右侧点阵预览为准。缩放以适配结果为100%，缩小时居中留白，放大时居中裁切；实际发送尺寸仍为552×768。两个大预览画布保持2:3竖向比例。
3. 可先导出预览或离线数据，再执行“检测设备”。
4. 执行“开始刷图”。默认认证、上传、触发刷新与状态查询在同一 RF 会话内完成。
5. 任务中取消会等待当前通信结束，设备最终画面可能无法确认。

默认及“重置处理”使用Atkinson；旧版布尔型抖动开关升级后在GUI中默认迁移为Atkinson，之后保存的算法选择会在重启时恢复。旧历史点阵不重处理。Atkinson按整数向零截断并逐次限幅，保留原量化调色板，因此不宣称完整复刻第三方APP的缩放、调色板及色彩管理。Floyd–Steinberg仍使用原Pillow实现。

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

输出 `dist/EInkTool.exe`，包含 Python、Tk、Pillow、pyserial、pyscard、lzokay 和授权公开的默认 PIN，无需额外配置认证文件或安装 Python。USB 串口驱动仍需系统识别。构建不会将个人覆盖配置、日志、图片或设置打入 EXE。使用 ACR122U 时，目标电脑仍需执行一次 `enable-acr122-escape.ps1`；`build-exe.ps1` 会把该脚本复制到 `dist`。

公开发行 EXE 请从本目录重新构建，以保证行为和公开源码一致。

发布或转发源码、修改版和 EXE 时，应保留许可原文（或其网址）及 `NOTICE` 中的署名通知。建议将 `LICENSE`、`NOTICE` 与 EXE 一同发布；这两个文本文件不是运行库，不影响 EXE 独立运行。`build-exe.ps1` 会将它们复制到 `dist`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe verify_gui_offline.py
.\.venv\Scripts\python.exe epd_gui.py --self-test logs/source-self-test
.\dist\EInkTool.exe --self-test logs/exe-self-test
```

传输和日志测试使用合成 PIN、生成图片及串口模拟数据；ACR122U 传输测试注入假 PC/SC 链路，不打开真实读卡器。配置测试还验证内置 PIN 在没有配置文件时生效。不依赖私人抓包或打开真实硬件。GUI 测试需要可用的桌面会话。模拟测试通过和 EXE 离线自检通过不代表真实设备认证或刷新通过；ACR122U 实机还需要启用 Escape 命令并满足天线耦合/供电条件。

## 代码结构

| 文件 | 功能 |
| --- | --- |
| `epd_gui.py` | GUI、预览、任务队列和日志开关 |
| `image_codec.py` | 四色量化、抖动、点阵编码 |
| `epd_history.py` | 最近5次刷新点阵的本地保存与原样载入 |
| `epd_protocol.py` / `epd_writer.py` | 设备信息、认证配置、图像分包和刷图工作流 |
| `epd_devices.py` | PN532 串口与 ACR122U PC/SC 设备枚举、连接值解析 |
| `iso_dep.py` / `pn532_iso_dep.py` | ISO-DEP 编码、状态管理和 PN532 会话 |
| `acr122_reader.py` | ACR122U PC/SC 直通 PN532 传输（pyscard） |
| `wakeup_probe.py` / `protocol.py` | 已验证底层 HSU/RF 通信和 CRC/激活辅助函数 |
| `enable-acr122-escape.ps1` | ACR122U 在 Windows 上的一次性 Escape 命令注册表设置 |
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
