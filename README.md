# 🎙 语音听写 —— 手机说话,Ubuntu 上屏

用手机输入法的语音识别说话,文字自动落到电脑上**当前光标所在的位置**。
中文、全角标点、换行、emoji 全部无损;不模拟按键、不占剪贴板。

```
手机浏览器(http://<电脑IP>:8765)
   │ 语音说话 → 点「发送」
   ▼
dictation.py (网页 + 推送)
   ▼
IBus「语音听写」引擎 → commit_text 直接落到光标
```

**原理**:键盘键码层面没有中文,"远程键盘"打不出中文;所以只传字符串,
上屏走输入法的 commit 通道(输入法第⑤层),而不是模拟按键。全程纯用户态,日常使用零权限。

## 安装

```bash
git clone https://gitcode.com/a5671550/voicedict.git
cd voicedict && ./install.sh
```

前置:系统需已安装 pip(Ubuntu 24.04 默认没有:`sudo apt install python3-pip`,
这是唯一可能用到 root 的一步)。脚本自动完成:装 Python 依赖(fastapi/uvicorn/requests)
→ 注册 IBus 引擎 → 创建 systemd 用户服务(开机自启)→ 添加「语音听写」输入源。

## 使用

1. 手机浏览器打开 `http://<电脑IP>:8765`,建议"添加到主屏幕"
2. 电脑切到「语音听写」🎙 输入源(Super+Space)并**常驻**——引擎对键盘完全透传,
   打英文不受影响;要用拼音时再切回
3. 光标点进任意输入位置(终端/编辑器/浏览器/聊天框都行)
4. 手机说话 → 发送 → 约 150ms 落屏

没停在「语音听写」输入源时发送,电脑会弹通知并提示该切输入源还是点输入框。

## 配置(`dictation.py` 顶部)

```python
TOKEN            = "mysecret123"  # 防局域网乱入,随手改
DELAY            = 0.05           # 发送→上屏缓冲秒数
ALLOWLIST        = None           # 只允许在指定程序上屏,如 frozenset(["firefox"])
MAX_TEXT_LEN     = 20000          # 单次推送上限
AUTO_SWITCH_IME  = False          # True=发送时自动切引擎 commit 后切回
```

改完 `systemctl --user restart dictation` 生效。

```bash
journalctl --user -u dictation -f          # 实时日志
systemctl --user disable --now dictation   # 彻底停用
tail -f /tmp/voicedict_engine.log          # 引擎日志
```

## 文件

| 文件 | 作用 |
|---|---|
| `dictation.py` | 主程序:网页 + 推送 + IBus 上屏调度 |
| `ibus_voicedict_engine.py` | IBus 引擎,被 ibus-daemon 按需拉起,收字符串即 commit |
| `voicedict.xml.in` | 引擎注册模板 |
| `install.sh` | 一键安装 |

## 踩坑实录(精要)

- 引擎必须在总线上 `request_name` 认领组件名,ibus-daemon 才认为就绪,
  否则 `SetGlobalEngine` 一直超时——对照 ibus-table 源码才发现,文档无记载
- ibus-daemon 默认不扫 `~/.local/share/ibus/component/`,需 systemd drop-in 注入
  `IBUS_COMPONENT_PATH`,且改完要删 `~/.cache/ibus/bus/registry` 再重启
- 程序化切换输入法只能走 `ibus engine <name>`:GNOME 吞合成 Super 键,
  改 gsettings 也被忽略
- ibus-daemon 对引擎的 stderr 是黑洞,引擎日志必须写文件
- 组件 XML 字段不全会产生 NULL 警告甚至被 GNOME 拒绝激活,对照 libpinyin.xml 补齐
- gnome-terminal 是原生 Wayland + 只读无障碍接口,终端上屏正解就是 IBus commit
- AT-SPI 全树扫描可达数百毫秒,commit 快路径只查监听缓存(毫秒级)

## 规划:本机语音识别(未实施)

快捷键 → PipeWire 采麦克风 → sherpa-onnx 本地流式中文识别 → 同一个 IBus 引擎 commit。
引擎骨架已就位,只需把"手机 socket 来源"换成"识别结果来源",断网可用。
参考:Numen、sherpa-onnx、ibus-typing-booster。
