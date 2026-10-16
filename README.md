# 🎙 语音听写 —— 手机说话，Ubuntu 上屏

用手机输入法的语音识别说话，文字自动落到电脑上**当前光标所在的位置**。
中文、全角标点、换行、emoji 全部无损；不模拟按键翻译中文；打字节奏零干扰。

```
手机浏览器(http://<电脑IP>:8765)
   │ 输入法语音说话 → 点「发送」
   ▼
dictation.py (网页 + 推送 + 上屏调度)
   │ 0.2s 缓冲(可调)
   ▼
┌─ 主通道: IBus「语音听写」引擎 ─────────────────────┐
│  停在「语音听写」输入源时: commit_text 直接落到光标   │
│  不占剪贴板 · 终端通吃 · 零按键模拟                  │
└───────────────────────────────────────────────────┘
   │ (引擎不可用时自动降级, 永不失联)
   ├─ 剪贴板 + Ctrl+V / Ctrl+Shift+V(终端)   [wl-copy + 内置uinput虚拟键盘]
   └─ AT-SPI 只读: 识别焦点是否在终端、显示目标窗口名
```

**核心思想**：键盘键码层面没有中文，任何"远程键盘"都打不出中文。
所以只传字符串，上屏走输入法的「commit 提交文本」通道（即输入法第⑤层），
而不是模拟按键。

---

## 一、文件清单

| 文件 | 作用 |
|---|---|
| `dictation.py` | 主程序：FastAPI 网页/推送/SSE + 四级上屏调度（约 700 行） |
| `ibus_voicedict_engine.py` | 自研 IBus 输入法引擎，被 ibus-daemon 按需拉起，收字符串即 `commit_text` |
| `voicedict.xml.in` | 引擎注册模板（`install.sh` 填入实际路径后安装到 `~/.local/share/ibus/component/`） |
| `install.sh` | 一键安装：依赖、udev、IBus 组件、systemd 服务、输入源 |
| `LICENSE` / `.gitignore` | MIT 协议 / Git 忽略规则 |
| `~/.config/systemd/user/dictation.service` | 用户服务：开机登录自启、崩溃自动拉起 |
| `~/.config/systemd/user/org.freedesktop.IBus.session.GNOME.service.d/override.conf` | 给 ibus-daemon 注入 `IBUS_COMPONENT_PATH`，让它扫描用户组件目录 |
| `/etc/udev/rules.d/60-uinput.rules` | `/dev/uinput` 对当前桌面用户授权（uaccess） |

---

## 二、依赖（本机已全部装好）

```bash
# Python 依赖（装在用户目录）
pip install --user --break-system-packages fastapi uvicorn requests

# 系统工具
sudo apt install wl-clipboard ydotool    # 剪贴板粘贴兜底通道

# uinput 权限（一次性）
echo 'KERNEL=="uinput", MODE="0660", GROUP="input", TAG+="uaccess"' | sudo tee /etc/udev/rules.d/60-uinput.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --name-match=uinput

# 组件注册后刷新（改过 voicedict.xml 时需要）
rm -f ~/.cache/ibus/bus/registry && systemctl --user restart org.freedesktop.IBus.session.GNOME.service
```

无 root 替代品：AT-SPI 引擎绑定 `gi.repository.Atspi`、IBus 绑定、uinput 直写
都是系统自带/纯 Python 实现。

---

## 三、日常使用（定型工作流）

1. 手机浏览器打开 `http://192.168.1.222:8765`（建议"添加到主屏幕"）
2. 电脑输入源**切到「语音听写」🎙 并常驻**（Super+Space 或顶栏输入法菜单）：
   **手机说中文、键盘打英文，两不误**（引擎对键盘完全透传）；要用拼音打字时再切回
3. 光标点进任意输入位置——终端、编辑器、浏览器、聊天框都行
4. 手机说话 → 点「发送」→ **约 150ms 落屏**（缓冲 0.05s + 毫秒级通道开销）

不在「语音听写」输入源时发送：自动降级为剪贴板粘贴
（普通输入框 Ctrl+V / 终端 Ctrl+Shift+V，用完自动还原剪贴板，图片等非文本除外）。
想要"自动切引擎 → commit → 切回"的瞬时模式，把 `AUTO_SWITCH_IME` 改为 `True`。

### 配置区（`dictation.py` 顶部）

```python
URL                 = "http://127.0.0.1:8765"  # 客户端连接服务端的地址
TOKEN               = "mysecret123"            # 防局域网乱入，随手改掉
HOST, PORT          = "0.0.0.0", 8765
DELAY               = 0.05     # 点「发送」→上屏的缓冲秒数
ALLOWLIST           = None     # 限制自动上屏的程序，如 frozenset(["gnome-text-editor"])
RESTORE_CLIPBOARD   = True     # 粘贴完成后恢复剪贴板原文本
MAX_TEXT_LEN        = 20000    # 单次推送最大字数
AUTO_SWITCH_IME     = False    # True=发送时自动切引擎commit后切回；False=只在「语音听写」输入源时走 commit
```

改完执行 `systemctl --user restart dictation` 生效。

### 运维命令

```bash
journalctl --user -u dictation -f          # 实时日志（通道选择一目了然）
systemctl --user restart dictation         # 重启
systemctl --user disable --now dictation   # 彻底停用
tail -f /tmp/voicedict_engine.log          # IBus 引擎自身日志
```

---

## 四、四级上屏通道详解

| 优先级 | 通道 | 原理 | 适用 |
|---|---|---|---|
| 1 | **IBus commit** | 停在「语音听写」输入源时，引擎收 socket 字符串 → `commit_text()` | 一切可输入处（含终端），不占剪贴板 |
| 2 | 剪贴板粘贴 | `wl-copy` 写剪贴板 + uinput 虚拟键盘按 Ctrl+V / 终端 Ctrl+Shift+V | 引擎不可用时；AT-SPI 识别终端自动选对快捷键 |
| 3 | （历史）AT-SPI 插入 | `EditableText.insert_text` 直写控件 | 已按需求裁剪，仅保留只读识别 |
| — | X11 注入 | 改键映射 + XTEST，Unicode keysym 绕输入法 | 已按需求裁剪 |

日志里的关键字：
- `已 commit N 字（IBus 输入法通道，不占剪贴板）` ← 主通道成功
- `目标是终端，使用 Ctrl+Shift+V` / `已粘贴 N 字` ← 兜底通道
- `目标窗口: 标题 [应用名·角色]` ← AT-SPI 识别到的焦点目标

---

## 五、踩坑实录（重要技术结论）

1. **AT-SPI `insert_text` 的 length 参数是 UTF-8 字节数，不是字符数**。
   传字符数会导致中文被截断（传 10 只落 3 字）。
2. **ydotool 0.1.8（Ubuntu 24.04 仓库版）用键名语法**：`ydotool key ctrl+shift+v`，
   不是 1.0 的 evdev 数字码（`29:1 47:1...` 会被当成字面键打出 "244442"）。
3. **ibus-daemon 切换引擎时，要求引擎进程在总线上 `request_name` 认领组件名才算就绪**。
   不认领 → `SetGlobalEngine` 一直等到超时，gnome-shell 也切不上来。
   这是对照系统自带 ibus-table 源码（`/usr/share/ibus-table/engine/main.py`）找到的，文档几乎无记载。
4. **这台机器的 ibus-daemon 不扫描 `~/.local/share/ibus/component/`**，
   需用 systemd drop-in 注入 `IBUS_COMPONENT_PATH`（含系统目录，冒号分隔），
   且改完必须 `rm ~/.cache/ibus/bus/registry` 再重启 ibus。
5. **GNOME 会吞合成 Super 键**：uinput 打 Ctrl+Shift+V 有效，
   但 Super+Space 触发不了输入法切换；`gsettings set ... current` 也被忽略。
   程序化切换只能走 `ibus engine <name>`（引擎 request_name 修好后可用）。
6. **ydotool 0.1.8 每次运行都新建/销毁内核虚拟设备**，会造成桌面闪动；
   主程序内置了纯 Python 常驻 uinput 键盘（`_UinputKbd` 类，`os.write` + ioctl），
   一次创建长期持有，抖动清零。
7. **ibus-daemon 对引擎子进程的 stderr 是黑洞**，引擎日志必须写文件
   （`/tmp/voicedict_engine.log`）。
8. 组件 XML 的引擎描述**字段不全会在序列化时产生 NULL 警告**并可能被 GNOME 拒绝激活，
   对照 libpinyin.xml 补齐 `icon/homepage/rank/layout_variant/...` 等字段。
9. gnome-terminal 是**原生 Wayland + 只读无障碍接口**：X11 注入够不着、AT-SPI 插不进，
   终端的上屏正解是剪贴板 + Ctrl+Shift+V 或 IBus commit。
10. 任务栏偶发"动一下"：排查过 apport 崩溃报告（清 `/var/crash`）、
    ydotool 设备抖动（已根治），最终确认多为 Electron 应用（如 ZCode）自建的一过性辅助窗口，无害。
11. **延迟优化**：AT-SPI 全树扫描在缓存未命中时可达几百毫秒~秒级，
    只允许出现在粘贴兜底路径；commit 快路径只查监听缓存（毫秒级 `focused_cheap()`），
    查不到就跳过日志直接上屏。配合 DELAY=0.05，发送→落屏约 150ms。

---

## 六、安装到新机器

```bash
git clone https://gitcode.com/a5671550/voicedict.git
cd voicedict && ./install.sh
```

脚本自动完成：装依赖（wl-clipboard/ydotool/pip 包）→ uinput 授权 →
注册 IBus 引擎 → 创建 systemd 服务（开机自启）→ 添加「语音听写」输入源。
装完 `journalctl --user -u dictation -f` 应显示四通道全绿，手机发送即可测试。

---

## 七、阶段二规划（本机语音，未实施）

```
快捷键触发 → PipeWire 采麦克风 → sherpa-onnx 本地流式中文识别
    → 同一个 IBus 引擎 commit_text → 光标处
```

引擎骨架（`ibus_voicedict_engine.py`）已就位，届时只需把"手机 socket 来源"
换成"识别结果来源"。收益：不用掏手机、毫秒级响应、断网可用。

参考资料：Numen（Wayland 语音操作）、sherpa-onnx 流式中文示例、
ibus-table / ibus-typing-booster（Python IBus 引擎范本）。
