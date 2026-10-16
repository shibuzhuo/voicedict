# 🎙 语音听写 —— 手机说话或本机说话,Ubuntu 上屏

两条输入路径,同一个上屏通道:
- **手机**:用手机输入法的语音识别说话,浏览器发送
- **本机**:按 **Alt+V** 直接对电脑说话,sherpa-onnx 本地离线识别

文字自动落到**当前光标所在的位置**。中文、全角标点、换行、emoji 全部无损;
不模拟按键、不占剪贴板、断网可用。

```
手机浏览器(http://<电脑IP>:8765) ──┐
                                    ├→ dictation.py ──→ IBus「语音听写」引擎
Alt+V → 麦克风 → sherpa-onnx 识别 ──┘      (网页+推送+调度)   → commit_text 落到光标
```

**原理**:键盘键码层面没有中文,"远程键盘"打不出中文;所以只传字符串,
上屏走输入法的 commit 通道(输入法第⑤层),而不是模拟按键。全程纯用户态。

## 安装

```bash
git clone https://gitcode.com/a5671550/voicedict.git
cd voicedict && ./install.sh
```

前置:系统需已安装 pip(Ubuntu 24.04 默认没有:`sudo apt install python3-pip`,
这是唯一可能用到 root 的一步)。脚本自动完成:装 Python 依赖 → 注册 IBus 引擎 →
创建 systemd 用户服务(开机自启)→ 添加「语音听写」输入源 → 注册 Alt+V 快捷键。

**本机语音识别**(可选)需另装模型:

```bash
pip install --user --break-system-packages numpy sherpa-onnx   # 清华镜像加 -i https://pypi.tuna.tsinghua.edu.cn/simple
mkdir -p models && cd models
wget https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30.tar.bz2
tar xjf *.tar.bz2 && mv sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30 asr-zh-int8 && rm *.tar.bz2
cd .. && systemctl --user restart dictation   # 启动时后台预加载模型
```

模型 126MB,int8 量化,双核 CPU 实时率约 20%。没装模型时仅手机通道可用,不影响运行。

## 使用

**本机语音(推荐)**:
1. 输入源切到「语音听写」🎙 并常驻(引擎对键盘透传,打字不受影响)
2. 光标点进任意输入位置
3. 按 **Alt+V** → 弹"录音中"通知 → 说话 → 再按 Alt+V → 文字落屏

**手机**:浏览器打开 `http://<电脑IP>:8765` → 语音说话 → 点「发送」→ 约 150ms 落屏。
建议"添加到主屏幕"。

没停在「语音听写」输入源时发送,电脑会弹通知并提示该切输入源还是点输入框。

## 配置(`dictation.py` 顶部)

```python
TOKEN            = "mysecret123"  # 防局域网乱入,随手改(改后重跑 install.sh 更新快捷键)
DELAY            = 0.05           # 发送→上屏缓冲秒数
ALLOWLIST        = None           # 只允许在指定程序上屏,如 frozenset(["firefox"])
MAX_TEXT_LEN     = 20000          # 单次推送上限
AUTO_SWITCH_IME  = False          # True=发送时自动切引擎 commit 后切回
LOCAL_ASR_DIR    = .../models/asr-zh-int8   # 本机识别模型目录,存在才启用
```

改完 `systemctl --user restart dictation` 生效。

```bash
journalctl --user -u dictation -f          # 实时日志(识别过程可见)
systemctl --user disable --now dictation   # 彻底停用
tail -f /tmp/voicedict_engine.log          # IBus 引擎日志
```

## 文件

| 文件 | 作用 |
|---|---|
| `dictation.py` | 主程序:网页+推送+本机识别+上屏调度 |
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
- GNOME 自定义快捷键**不能绑单独的修饰键**(如右 Alt),必须带普通键
- ibus-daemon 对引擎的 stderr 是黑洞,引擎日志必须写文件
- 组件 XML 字段不全会产生 NULL 警告甚至被 GNOME 拒绝激活,对照 libpinyin.xml 补齐
- gnome-terminal 是原生 Wayland + 只读无障碍接口,终端上屏正解就是 IBus commit
- AT-SPI 全树扫描可达数百毫秒,commit 快路径只查监听缓存(毫秒级)
- sherpa-onnx 1.13+ 改用 `OnlineRecognizer.from_transducer()` 工厂方法,
  旧版 `OnlineRecognizerTransducerConfig` 已移除
- 内置麦克风每次录音开头约 1.5 秒上电冲击噪声,增益调低+数字放大可压底噪

## 后续想法

- 静音自动停止(is_endpoint 检测,免按第二次)
- RNNoise 实时降噪接入 PipeWire(老笔记本底噪大)
- 手机网页直接调用电脑麦克风(统一两条路径)
