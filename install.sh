#!/usr/bin/env bash
# 语音听写 一键安装（Ubuntu 22.04/24.04 + GNOME Wayland）
# 用法: git clone 后在仓库目录执行 ./install.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ENGINE_NAME="voicedict"

echo "==> 1/6 安装依赖（需要 sudo）"
sudo apt install -y wl-clipboard ydotool
pip install --user fastapi uvicorn requests 2>/dev/null || \
  pip install --user --break-system-packages fastapi uvicorn requests

echo "==> 2/6 配置 /dev/uinput 权限（内置虚拟键盘需要）"
echo 'KERNEL=="uinput", MODE="0660", GROUP="input", TAG+="uaccess"' | \
  sudo tee /etc/udev/rules.d/60-voicedict.rules >/dev/null
sudo udevadm control --reload-rules
sudo udevadm trigger --name-match=uinput 2>/dev/null || sudo udevadm trigger

echo "==> 3/6 注册 IBus 引擎组件"
mkdir -p "$HOME/.local/share/ibus/component"
sed "s|@ENGINE_DIR@|$DIR|" "$DIR/voicedict.xml.in" \
  > "$HOME/.local/share/ibus/component/voicedict.xml"

echo "==> 4/6 让 ibus-daemon 扫描用户组件目录（多数发行版默认不扫）"
mkdir -p "$HOME/.config/systemd/user/org.freedesktop.IBus.session.GNOME.service.d"
cat > "$HOME/.config/systemd/user/org.freedesktop.IBus.session.GNOME.service.d/override.conf" <<EOF
[Service]
Environment=IBUS_COMPONENT_PATH=/usr/share/ibus/component:$HOME/.local/share/ibus/component
EOF

echo "==> 5/6 创建主服务（开机自启）"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/dictation.service" <<EOF
[Unit]
Description=手机语音听写 (dictation.py)
After=graphical-session.target

[Service]
ExecStart=/usr/bin/python3 $DIR/dictation.py
Restart=on-failure
RestartSec=3
Environment=WAYLAND_DISPLAY=wayland-0
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u)

[Install]
WantedBy=graphical-session.target
EOF

echo "==> 6/6 注册输入源并启动"
python3 - <<PYEOF
import subprocess
def gs(*a):
    return subprocess.run(["gsettings", *a], capture_output=True, text=True).stdout.strip()
srcs = gs("get", "org.gnome.desktop.input-sources", "sources")
if "$ENGINE_NAME" not in srcs:
    new = ("[('ibus', '$ENGINE_NAME')]" if srcs == "@a(ss) []"
           else srcs[:-1] + ", ('ibus', '$ENGINE_NAME')]")
    gs("set", "org.gnome.desktop.input-sources", "sources", new)
    print("已添加输入源:", new)
else:
    print("输入源已存在")
PYEOF

# 刷新组件缓存并重启相关服务
rm -f "$HOME/.cache/ibus/bus/registry"
systemctl --user daemon-reload
systemctl --user restart org.freedesktop.IBus.session.GNOME.service
sleep 2
systemctl --user enable --now dictation.service

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "✅ 安装完成！"
echo "   手机浏览器打开: http://${IP:-<电脑IP>}:8765"
echo "   Super+Space 切到「语音听写」🎙 输入源即可开始"
echo "   日志: journalctl --user -u dictation -f"
