# 🎙 语音听写 —— 手机/本机说话,Ubuntu 光标处上屏

对着手机或电脑说中文,文字直接落到**当前光标位置**——终端、编辑器、浏览器、聊天框都行。
中文标点、换行、emoji 无损;不模拟按键、不占剪贴板、断网可用。

## 功能一览

- **本机语音**:按 Alt+V → 说话 → 再按 Alt+V,SenseVoice 本地离线识别,说完约 1 秒出字
- **手机语音**:手机浏览器说话 → 点「发送」,约 0.15 秒上屏(借用手机输入法的语音识别)
- 双通道共用上屏引擎:自研 IBus 输入法,commit 直达光标
- 开机自启(systemd 用户服务),全程无 root 运行

## 适合谁

- 不想打字:对着电脑口述长文,说到哪落到哪
- 用语音起草初稿,再手动润色
- 手机语音输入顺手,但往电脑传字麻烦:手机直接当语音外设

## 安装

前置:Ubuntu 22.04/24.04 + GNOME;需已装 pip(24.04 默认没有,
`sudo apt install python3-pip` 是唯一用到 root 的一步)。

```bash
git clone https://gitcode.com/a5671550/voicedict.git
cd voicedict && ./install.sh
```

脚本自动完成:装 Python 依赖 → 注册 IBus 引擎 → 建自启服务 →
添加「语音听写」输入源 → 绑定 Alt+V 快捷键。

**启用本机语音**(可选,另下载 233MB 模型):

```bash
pip install --user --break-system-packages numpy sherpa-onnx   # 加 -i https://pypi.tuna.tsinghua.edu.cn/simple 走清华镜像
mkdir -p models && cd models
wget https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09.tar.bz2
tar xjf *.tar.bz2 && mv sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09 asr-sensevoice && rm *.tar.bz2
cd .. && systemctl --user restart dictation
```

## 使用

**本机语音(日常推荐)**
1. Super+Space 切到「语音听写」🎙 输入源并常驻(引擎对键盘透传,正常打字不受影响)
2. 光标点进任意输入位置
3. **Alt+V → 说话 → 再按 Alt+V** → 文字落屏

**手机语音**
1. 手机浏览器打开 `http://<电脑IP>:8765`,建议"添加到主屏幕"
2. 用输入法的语音识别说话 → 点「发送」→ 文字落屏

没停在「语音听写」输入源时发送,日志会提示该切输入源还是点输入框。

## 配置与运维

`dictation.py` 顶部配置区,常用项:

| 配置 | 默认 | 说明 |
|---|---|---|
| `TOKEN` | mysecret123 | 防局域网乱入;改后重跑 install.sh 更新快捷键里的令牌 |
| `DELAY` | 0.05 | 手机发送→上屏的缓冲秒数 |
| `ALLOWLIST` | None | 只允许在指定程序上屏,如 `frozenset(["firefox"])` |
| `AUTO_SWITCH_IME` | False | True=发送时自动切引擎 commit 后切回 |
| `MIC_HW_VOLUME` | 40% | 内置麦克风录音增益(自动管理,蓝牙麦不受影响) |

```bash
journalctl --user -u dictation -f          # 实时日志
systemctl --user restart dictation         # 改配置后重启生效
systemctl --user disable --now dictation   # 彻底停用
```

排查"没听清":`/tmp/voicedict_last.wav` 是最近一次本机录音,`pw-play` 试听即可确认采集。

## 原理一句话

键盘键码层没有中文,"远程键盘"打不出中文;本项目只传字符串,上屏走输入法的
commit 通道,PipeWire 采集 + sherpa-onnx 离线识别,全程用户态。
