# 🎙 语音听写 —— 手机说话，Ubuntu 上屏（IBus 主通道版）

用手机输入法的语音识别说话，文字自动落到电脑上**当前光标所在的位置**。
中文、全角标点、换行、emoji 全部无损；不模拟按键、不占剪贴板。

```
手机浏览器(http://<电脑IP>:8765)
   │ 输入法语音说话 → 点「发送」
   ▼
dictation.py (网页 + 推送 + 上屏调度)
   │ 0.2s 缓冲(可调)
   ▼
┌─ IBus「语音听写」引擎 ─────────────────────────────┐
│  停在「语音听写」输入源时: commit_text 直接落到光标   │
│  不占剪贴板 · 终端通吃 · 零按键模拟                  │
└─────────────────────────────────────────────────────┘
   AT-SPI 只读: 白名单安全门 + 日志显示目标窗口名
```

**核心思想**：键盘键码层面没有中文，任何"远程键盘"都打不出中文。
所以只传字符串，上屏走输入法的「commit 提交文本」通道（即输入法第⑤层），
而不是模拟按键。也因此**无需任何 root 权限和系统工具**，纯用户态运行。

---

## 一、文件清单

| 文件 | 作用 |
|---|---|
| `dictation.py` | 主程序：FastAPI 网页/推送/SSE + IBus 上屏调度（约 680 行） |
| `ibus_voicedict_engine.py` | 自研 IBus 输入法引擎，被 ibus-daemon 按需拉起，收字符串即 `commit_text` |
| `voicedict.xml.in` | 引擎注册模板（`install.sh` 填入实际路径后安装到 `~/.local/share/ibus/component/`） |
| `install.sh` | 一键安装：pip 依赖、IBus 组件、systemd 服务、输入源（**全程免 root**） |
| `LICENSE` / `.gitignore` | MIT 协议 / Git 忽略规则 |
| `~/.config/systemd/user/dictation.service` | 用户服务：开机登录自启、崩溃自动拉起 |
| `~/.config/systemd/user/org.freedesktop.IBus.session.GNOME.service.d/override.conf` | 给 ibus-daemon 注入 `IBUS_COMPONENT_PATH`，让它扫描用户组件目录 |

---

## 二、依赖（仅 Python 包）

```bash
pip install --user fastapi uvicorn requests
# Ubuntu 24.04 需加 --break-system-packages
```

AT-SPI 引擎绑定（`gi.repository.Atspi`）、IBus 绑定都是系统自带，无需额外安装。

---

## 三、日常使用（定型工作流）

1. 手机浏览器打开 `http://<电脑IP>:8765`（建议"添加到主屏幕"）
2. 电脑输入源**切到「语音听写」🎙 并常驻**（Super+Space 或顶栏输入法菜单）：
   **手机说中文、键盘打英文，两不误**（引擎对键盘完全透传）；要用拼音打字时再切回
3. 光标点进任意输入位置——终端、编辑器、浏览器、聊天框都行
4. 手机说话 → 点「发送」→ **约 150ms 落屏**（缓冲 0.05s + 毫秒级通道开销）

不在「语音听写」输入源时发送：电脑弹通知提醒切换输入源。
想要"自动切引擎 → commit → 切回"的瞬时模式，把 `AUTO_SWITCH_IME` 改为 `True`。

### 配置区（`dictation.py` 顶部）

```python
URL                 = "http://127.0.0.1:8765"  # 客户端连接服务端的地址
TOKEN               = "mysecret123"            # 防局域网乱入，随手改掉
HOST, PORT          = "0.0.0.0", 8765
DELAY               = 0.05     # 点「发送」→上屏的缓冲秒数
ALLOWLIST           = None     # 限制自动上屏的程序，如 frozenset(["gnome-text-editor"])
MAX_TEXT_LEN        = 20000    # 单次推送最大字数
AUTO_SWITCH_IME     = False    # True=发送时自动切引擎commit后切回；False=只在「语音听写」输入源时走 commit
```

改完执行 `systemctl --user restart dictation` 生效。

### 运维命令

```bash
journalctl --user -u dictation -f          # 实时日志
systemctl --user restart dictation         # 重启
systemctl --user disable --now dictation   # 彻底停用
tail -f /tmp/voicedict_engine.log          # IBus 引擎自身日志
```

---

## 四、踩坑实录（重要技术结论）

1. **ibus-daemon 切换引擎时，要求引擎进程在总线上 `request_name` 认领组件名才算就绪**。
   不认领 → `SetGlobalEngine` 一直等到超时，gnome-shell 也切不上来。
   这是对照系统自带 ibus-table 源码（`/usr/share/ibus-table/engine/main.py`）找到的，文档几乎无记载。
2. **这台机器的 ibus-daemon 不扫描 `~/.local/share/ibus/component/`**，
   需用 systemd drop-in 注入 `IBUS_COMPONENT_PATH`（含系统目录，冒号分隔），
   且改完必须 `rm ~/.cache/ibus/bus/registry` 再重启 ibus。
3. **GNOME 会吞合成 Super 键**、`gsettings` 改输入源也被忽略，
   程序化切换只能走 `ibus engine <name>`（引擎 request_name 修好后可用）。
4. **ibus-daemon 对引擎子进程的 stderr 是黑洞**，引擎日志必须写文件
   （`/tmp/voicedict_engine.log`）。
5. 组件 XML 的引擎描述**字段不全会在序列化时产生 NULL 警告**并可能被 GNOME 拒绝激活，
   对照 libpinyin.xml 补齐 `icon/homepage/rank/layout_variant/...` 等字段。
6. gnome-terminal 是**原生 Wayland + 只读无障碍接口**：按键注入够不着、AT-SPI 插不进，
   终端的上屏正解是 IBus commit。
7. **延迟优化**：AT-SPI 全树扫描在缓存未命中时可达几百毫秒~秒级，
   commit 快路径只查监听缓存（毫秒级 `focused_cheap()`），
   查不到就跳过日志直接上屏。配合 DELAY=0.05，发送→落屏约 150ms。

---

## 五、安装到新机器

```bash
git clone https://gitcode.com/a5671550/voicedict.git
cd voicedict && ./install.sh
```

脚本自动完成：装 pip 依赖 → 注册 IBus 引擎 → 创建 systemd 服务（开机自启）→
添加「语音听写」输入源，全程免 root。
装完 `journalctl --user -u dictation -f` 应显示 IBus 通道就绪，手机发送即可测试。

---

## 六、阶段二规划（本机语音，未实施）

```
快捷键触发 → PipeWire 采麦克风 → sherpa-onnx 本地流式中文识别
    → 同一个 IBus 引擎 commit_text → 光标处
```

引擎骨架（`ibus_voicedict_engine.py`）已就位，届时只需把"手机 socket 来源"
换成"识别结果来源"。收益：不用掏手机、毫秒级响应、断网可用。

参考资料：Numen（Wayland 语音操作）、sherpa-onnx 流式中文示例、
ibus-table / ibus-typing-booster（Python IBus 引擎范本）。
