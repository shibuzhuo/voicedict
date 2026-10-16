#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
语音听写 IBus 引擎（阶段一：手机字符串 → commit_text）

工作方式：
  * ibus-daemon 在你首次切到「语音听写」输入源时按需启动本进程
  * dictation.py 收到手机文字后，连 Unix socket 把文本送进来
  * 本引擎调 commit_text() 把文字直接提交到当前聚焦的输入框
    —— 不占剪贴板、终端/普通输入框通吃、不受输入法中英文状态影响
  * 对物理键盘完全透传（process_key_event 一律不处理），
    日常可以一直挂在这个输入源上，不影响正常打字
"""

import json
import os
import socket
import sys
import time

LOG_PATH = "/tmp/voicedict_engine.log"


def log(msg):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

import gi
gi.require_version("IBus", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import IBus, GLib

ENGINE_NAME = "voicedict"
SOCK_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
                         "voicedict.sock")


class VoiceDictEngine(IBus.Engine):
    __gtype_name__ = "VoiceDictEngine"

    def __init__(self):
        super().__init__()
        self.focused = False
        _ENGINES.append(self)
        self._dbg("实例创建")

    def _dbg(self, msg):
        log(f"{id(self) & 0xffff:x} {msg}")

    def do_enable(self):
        self._dbg("enable")

    def do_disable(self):
        self._dbg("disable")
        self.focused = False

    def do_focus_in(self):
        self.focused = True
        self._dbg("focus_in")

    def do_focus_out(self):
        self.focused = False
        self._dbg("focus_out")

    def do_destroy(self):
        self._dbg("销毁")
        if self in _ENGINES:
            _ENGINES.remove(self)

    def do_process_key_event(self, keyval, keycode, state):
        return False   # 全部透传：挂在本输入源上打字不受影响

    def commit_from_phone(self, text):
        if not self.focused:
            self._dbg("commit 被拒: 无焦点")
            return False
        try:
            self.commit_text(IBus.Text.new_from_string(text))
            self._dbg(f"已 commit {len(text)} 字")
            return True
        except Exception as e:
            self._dbg(f"commit 异常: {e}")
            return False


# IBus 会为每个输入上下文(每个应用)各建一个引擎实例，只有聚焦的那个能 commit
_ENGINES = []


def _handle_line(line):
    try:
        text = json.loads(line or "{}").get("text", "")
    except Exception:
        return "BADJSON"
    if not text:
        return "EMPTY"
    for e in _ENGINES:
        if e.focused:
            return "OK" if e.commit_from_phone(text) else "FAIL"
    log(f"NOFOCUS 实例数={len(_ENGINES)}")
    return "NOFOCUS"


def start_socket_server():
    try:
        os.unlink(SOCK_PATH)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    srv.listen(8)
    srv.setblocking(False)

    def on_accept(s, _):
        try:
            conn, _addr = s.accept()
        except BlockingIOError:
            return True
        conn.setblocking(False)
        buf = bytearray()

        def on_readable(c, _):
            try:
                chunk = c.recv(65536)
            except Exception:
                return False
            if chunk:
                buf.extend(chunk)
                return True
            # 对端写完关闭，处理整行并答复
            try:
                reply = _handle_line(bytes(buf).decode("utf-8", "replace").strip())
            except Exception:
                reply = "ERROR"
            try:
                c.sendall((reply + "\n").encode())
            except Exception:
                pass
            try:
                c.close()
            except Exception:
                pass
            return False

        GLib.io_add_watch(conn, GLib.IO_IN, on_readable)
        return True

    GLib.io_add_watch(srv, GLib.IO_IN, on_accept)


COMPONENT_NAME = "com.voicedict.component"


def main():
    IBus.init()
    bus = IBus.Bus()
    if not bus.is_connected():
        print("连不上 ibus-daemon", file=sys.stderr)
        sys.exit(1)
    loop = GLib.MainLoop()
    bus.connect("disconnected", lambda *a: loop.quit())
    factory = IBus.Factory.new(bus.get_connection())
    factory.add_engine(ENGINE_NAME, VoiceDictEngine)
    if "--ibus" in sys.argv:
        # 关键：向总线认领组件名，ibus-daemon 由此知道引擎已就绪
        # （不认领的话 SetGlobalEngine 会一直等到超时，gnome-shell 也切不上来）
        bus.request_name(COMPONENT_NAME, 0)
        log(f"已认领总线名 {COMPONENT_NAME}")
    start_socket_server()
    log(f"引擎就绪 pid={os.getpid()} socket={SOCK_PATH}")
    loop.run()


if __name__ == "__main__":
    main()
