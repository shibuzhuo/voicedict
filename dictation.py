#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dictation.py —— 语音听写 → Ubuntu 上屏(网页服务 + 本机识别 + IBus 上屏)

用法:
    python3 dictation.py            # 通常由 systemd 用户服务运行;前台调试直接跑

依赖:
    pip install fastapi uvicorn
    (本机语音识别再加: pip install numpy sherpa-onnx,模型放 models/asr-sensevoice/)

两条输入路径:
    手机: 浏览器打开 http://<电脑IP>:8765 → 输入法语音说话 → 点「发送」
    本机: 按 Alt+V → 说话 → 再按 Alt+V(SenseVoice 离线识别,说完出字)

上屏通道(唯一):
    IBus 输入法引擎 commit_text —— 自研「语音听写」引擎(ibus_voicedict_engine.py)。
    停在「语音听写」输入源时 commit 到光标(AUTO_SWITCH_IME=True 可自动切换)。
    不占剪贴板、不模拟按键、终端/普通输入框通吃(即输入法第⑤层原生通道)。
    (AT-SPI 仅只读使用: 白名单安全门 + 日志显示目标窗口名)

架构一条线: FastAPI 收文字 → 队列 → 投递线程 → IBus commit → 光标处
"""

import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time

try:
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse
except ImportError as e:
    print("缺少依赖，请先执行:  pip install fastapi uvicorn")
    print(f"缺的模块: {e}")
    raise SystemExit(1)

# ════════════════════════ 配置区（按需修改）════════════════════════
TOKEN               = "mysecret123"            # 随手改掉，防局域网乱入
HOST, PORT          = "0.0.0.0", 8765          # 服务端监听地址和端口
DELAY               = 0.05     # 手机「发送」→上屏的缓冲秒数（要留切窗口时间就调大）
ALLOWLIST           = None     # 只允许在这些程序里自动上屏（匹配应用名/窗口标题），如 frozenset(["gnome-text-editor", "WPS", "firefox"])
MAX_TEXT_LEN        = 20000    # 单次推送最大字数，防误发
AUTO_SWITCH_IME     = False    # True=发送时自动切引擎(commit后切回)；False=只在你停在「语音听写」输入源时才走 commit
LOCAL_SENSEVOICE_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "models", "asr-sensevoice")  # 本机识别模型(SenseVoice,非流式)
MIC_HW_VOLUME       = "40%"    # 录音前强制的硬件增益(实测:40%干净,45-60%死区,100%削波);None=不管理
# ═════════════════════════════════════════════════════════════════════

IS_LINUX = sys.platform.startswith("linux")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ════════════════════════ 网页 ════════════════════════

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>语音听写 → 电脑</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 16px; min-height: 100vh;
    padding-bottom: calc(20px + env(safe-area-inset-bottom));
    display: flex; flex-direction: column;
    font-family: system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #f5f5f7; color: #111;
  }
  @media (prefers-color-scheme: dark) { body { background: #111; color: #eee; } }
  h1 { font-size: 18px; margin: 0 0 8px; display: flex; align-items: baseline; gap: 10px; }
  #status { font-size: 13px; opacity: .7; margin-bottom: 10px; min-height: 18px; }
  textarea {
    width: 100%; min-height: 270px; max-height: 60vh; overflow-y: auto;
    font-size: 17px; line-height: 1.6; padding: 12px;
    border-radius: 14px; border: 1px solid #ccc;
    background: #fff; color: inherit; resize: none;
  }
  @media (prefers-color-scheme: dark) { textarea { background: #1c1c1e; border-color: #333; } }
  .row { display: flex; align-items: center; gap: 12px; margin-top: 12px; }
  #count { font-size: 13px; opacity: .65; white-space: nowrap; }
  #lastsent {
    margin-top: 10px; font-size: 13px; opacity: .6; line-height: 1.5;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden;
  }
  #send {
    margin-left: auto; font-size: 18px; font-weight: 600; color: #fff;
    background: #2f6fed; border: 0; border-radius: 14px; padding: 14px 36px;
    touch-action: manipulation;
  }
  #send:disabled { opacity: .5; }
  .toast {
    position: fixed; left: 50%; transform: translateX(-50%);
    bottom: calc(96px + env(safe-area-inset-bottom));
    background: #0a7d32dd; color: #fff; padding: 10px 18px;
    border-radius: 10px; font-size: 14px;
    opacity: 0; transition: opacity .25s; pointer-events: none;
    max-width: 86vw; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .toast.show { opacity: 1; }
</style>
</head>
<body>
<h1>🎙 语音听写</h1>
<div id="status">正在检查电脑端连接…</div>
<textarea id="txt" autocomplete="off" enterkeyhint="done"
  placeholder="点一下这里 → 切到输入法语音说话 → 认字无误后点「发送」"></textarea>
<div class="row">
  <span id="count">0 字</span>
  <button id="send">发送</button>
</div>
<div id="lastsent"></div>
<div class="toast" id="toast"></div>
<script>
  const TOKEN = __TOKEN__;
  const $ = id => document.getElementById(id);
  const draftKey = "dictation_draft";
  const ta = $("txt");
  ta.value = sessionStorage.getItem(draftKey) || "";

  const fitBox = () => {   // 输入框随内容自动长高，空着时保持小巧
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, innerHeight * 0.6) + "px";
  };
  const updateCount = () => {
    $("count").textContent = ta.value.length + " 字";
    sessionStorage.setItem(draftKey, ta.value);   // 草稿兜底：误关页面重开还在
    fitBox();
  };

  ta.addEventListener("input", updateCount);
  window.addEventListener("resize", fitBox);
  updateCount();

  // —— 最后一次发送记录（localStorage 持久，刷新/重开页面仍在） ——
  const lastKey = "dictation_last";
  const showLast = () => {
    const el = $("lastsent");
    try {
      const j = JSON.parse(localStorage.getItem(lastKey) || "null");
      if (!j) { el.textContent = ""; el.title = ""; return; }
      const t = new Date(j.at);
      const hh = [t.getHours(), t.getMinutes()].map(n => String(n).padStart(2, "0")).join(":");
      el.textContent = "上次发送(" + hh + "): " + j.text;
      el.title = j.text;   // 长按可看全文
    } catch (e) { el.textContent = ""; }
  };
  showLast();

  let toastTimer;
  const toast = (msg, ok = true) => {
    const t = $("toast");
    t.textContent = msg;
    t.style.background = ok ? "#0a7d32dd" : "#c0392bdd";
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 2400);
  };

  async function checkHealth() {
    try {
      const j = await (await fetch("/health")).json();
      $("status").textContent = j.ok
        ? "✅ 电脑端已连接，随时可以发送"
        : "⚠️ 电脑端未就绪";
    } catch (e) {
      $("status").textContent = "❌ 连不上服务端";
    }
  }
  checkHealth();
  setInterval(checkHealth, 5000);

  async function send() {
    const text = ta.value.trim();
    if (!text) { toast("先说点什么吧", false); return; }
    $("send").disabled = true;
    try {
      const r = await fetch("/push?token=" + encodeURIComponent(TOKEN), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text })
      });
      const j = await r.json();
      if (r.ok && j.ok) {
        toast("已发送 " + j.length + " 字");
        localStorage.setItem(lastKey, JSON.stringify({ text: text, at: Date.now() }));
        showLast();
        ta.value = "";
        sessionStorage.removeItem(draftKey);
        updateCount();
      } else {
        toast(j.error || "发送失败", false);
      }
    } catch (e) {
      toast("网络错误：" + e, false);
    } finally {
      $("send").disabled = false;
      ta.focus();   // 方便接着说下一段
    }
  }
  $("send").addEventListener("click", send);
  window.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") send();
  });
</script>
</body>
</html>
"""

PAGE = PAGE.replace("__TOKEN__", json.dumps(TOKEN, ensure_ascii=False))


# ════════════════════════ FastAPI 服务端 ════════════════════════

app = FastAPI(title="phone-dictation", docs_url=None, redoc_url=None, openapi_url=None)

# 投递队列:HTTP 线程收文字,主线程取走上屏(进程内,替代原 localhost SSE 回环)
deliver_q: "queue.Queue" = queue.Queue(maxsize=100)


def _token_ok(request: Request) -> bool:
    tok = request.query_params.get("token") or request.headers.get("x-token", "")
    return tok == TOKEN


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


@app.get("/health")
def health():
    # 服务端与投递线程同进程:能应答即表示整条链路活着
    return {"ok": True, "clients": 1}


@app.post("/push")
async def push(request: Request):
    if not _token_ok(request):
        return JSONResponse({"ok": False, "error": "token 错误"}, status_code=401)

    text = request.query_params.get("text", "")
    ctype = request.headers.get("content-type", "").lower()
    if not text and "json" in ctype:
        try:
            body = await request.json()
            text = str(body.get("text", ""))
        except Exception:
            return JSONResponse({"ok": False, "error": "JSON 解析失败"}, status_code=400)

    text = text or ""
    if not text.strip():
        return JSONResponse({"ok": False, "error": "text 为空"}, status_code=400)
    if len(text) > MAX_TEXT_LEN:
        return JSONResponse({"ok": False, "error": f"超过单次上限 {MAX_TEXT_LEN} 字"},
                            status_code=413)

    try:
        deliver_q.put_nowait(text)
    except queue.Full:
        return JSONResponse({"ok": False, "error": "投递队列满，稍后再试"},
                            status_code=503)
    return {"ok": True, "length": len(text)}


@app.api_route("/local-voice", methods=["GET", "POST"])
def local_voice(request: Request):
    """切换本机录音识别:Alt+V 快捷键或浏览器触发"""
    if not _token_ok(request):
        return JSONResponse({"ok": False, "error": "token 错误"}, status_code=401)
    if not IS_LINUX:
        return JSONResponse({"ok": False, "error": "仅支持 Linux"}, status_code=400)
    recording, msg = _local_voice_toggle()
    return {"ok": True, "recording": recording, "message": msg}


def run_server(block: bool = True):
    if block:
        uvicorn.run(app, host=HOST, port=PORT, log_level="info", access_log=False)
        return None
    # 服务端放后台线程跑
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT,
                                           log_level="info", access_log=False))
    t = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    t.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("服务端启动失败（8765 端口被占用？）")
    return server


# ════════════════════════ Linux(Ubuntu/GNOME) 上屏实现 ════════════════════════
# 唯一上屏通道：IBus「语音听写」引擎 commit_text（不占剪贴板、不模拟按键）。
# AT-SPI 仅只读使用：白名单安全门 + 日志显示目标窗口名；不参与文字写入。

if IS_LINUX:

    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi as _Atspi, GLib as _GLib
        _ATSPI_OK = True
    except Exception as _e:
        _ATSPI_OK = False
        print(f"[{time.strftime('%H:%M:%S')}] AT-SPI 不可用({_e})，仅影响日志显示与白名单识别", flush=True)

    if _ATSPI_OK:
        class _AtspiHub:
            """只读监听全局焦点变化，用于识别终端目标和显示窗口名"""

            def __init__(self) -> None:
                self._focused = None
                self._lock = threading.Lock()
                try:
                    self._listener = _Atspi.EventListener.new(self._on_event)
                    self._listener.register("object:state-changed:focused")
                    self._loop = _GLib.MainLoop.new(None, False)
                    threading.Thread(target=self._loop.run, daemon=True,
                                     name="atspi-loop").start()
                except Exception:
                    self._listener = None

            def _on_event(self, event) -> None:
                try:
                    if event.detail1 and "focused" in (event.type or ""):
                        with self._lock:
                            self._focused = event.source
                except Exception:
                    pass

            def focused_cheap(self):
                """只查监听缓存，不扫全树（毫秒级，日志/快路径用）"""
                with self._lock:
                    acc = self._focused
                try:
                    if acc is not None and \
                            acc.get_state_set().contains(_Atspi.StateType.FOCUSED):
                        return acc
                except Exception:
                    pass
                return None

            def focused_any(self):
                """不管可不可编辑，返回当前聚焦的节点（诊断/白名单用）"""
                with self._lock:
                    acc = self._focused
                try:
                    if acc is not None and \
                            acc.get_state_set().contains(_Atspi.StateType.FOCUSED):
                        return acc
                except Exception:
                    pass
                return self._scan()

            @staticmethod
            def _scan():
                focused = []

                def walk(a):
                    try:
                        if a.get_state_set().contains(_Atspi.StateType.FOCUSED):
                            focused.append(a)
                    except Exception:
                        return
                    try:
                        for i in range(a.get_child_count()):
                            walk(a.get_child_at_index(i))
                    except Exception:
                        pass

                try:
                    desktop = _Atspi.get_desktop(0)
                    for i in range(desktop.get_child_count()):
                        walk(desktop.get_child_at_index(i))
                except Exception:
                    return None
                if not focused:
                    return None
                # 优先非 gnome-shell 的（shell 的 WINDOW 节点常年带 FOCUSED 假状态）
                try:
                    non_shell = [a for a in focused
                                 if (a.get_application().get_name() or "")
                                 != "gnome-shell"]
                    return (non_shell or focused)[0]
                except Exception:
                    return focused[0]

            @staticmethod
            def describe(acc):
                app = title = ""
                try:
                    app = acc.get_application().get_name() or ""
                except Exception:
                    pass
                try:
                    node = acc
                    for _ in range(6):
                        if node.get_role() == _Atspi.Role.FRAME:
                            title = node.get_name() or ""
                            break
                        node = node.get_parent()
                except Exception:
                    pass
                return app, title

        _atspi = _AtspiHub()

    _IBUS_SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"),
                              "voicedict.sock")

    _IBUS_ENGINE = "voicedict"

    def _ibus_commit(text: str) -> str:
        """经「语音听写」IBus 引擎 commit_text 上屏（第⑤层原生通道）。
        返回引擎回复: "OK" / "NOFOCUS"(引擎在跑但无聚焦输入框) / "FAIL"(commit 异常)
        / "NOENGINE"(引擎未激活、连不上或无有效回复)。
        AUTO_SWITCH_IME=True: 自动切引擎 → commit → 切回；
        False: 只在用户停在「语音听写」输入源时生效，否则提示切换。"""
        if not os.path.exists(_IBUS_SOCK):
            return "NOENGINE"
        prev_engine = None
        try:
            if AUTO_SWITCH_IME:
                prev_engine = subprocess.run(
                    ["ibus", "engine"], capture_output=True, text=True,
                    timeout=5).stdout.strip()
                if prev_engine != _IBUS_ENGINE:
                    subprocess.run(["ibus", "engine", _IBUS_ENGINE],
                                   capture_output=True, timeout=20)
                    time.sleep(0.35)   # 等焦点输入框绑定到语音听写引擎
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(_IBUS_SOCK)
            s.sendall(json.dumps({"text": text}).encode())
            s.shutdown(socket.SHUT_WR)
            reply = s.recv(100).decode("utf-8", "replace").strip()
            s.close()
            if reply in ("OK", "NOFOCUS", "FAIL", "EMPTY", "BADJSON", "ERROR"):
                return reply
            return "NOENGINE"
        except Exception:
            return "NOENGINE"
        finally:
            if AUTO_SWITCH_IME and prev_engine and prev_engine != _IBUS_ENGINE:
                try:
                    subprocess.run(["ibus", "engine", prev_engine],
                                   capture_output=True, timeout=10)
                except Exception:
                    pass

    def _linux_backend_summary() -> str:
        ibus = "✅" if os.path.exists(_IBUS_SOCK) else "待激活(切到「语音听写」输入源后生效)"
        return ("IBus commit " + ibus
                + "，焦点识别(AT-SPI) " + ("✅" if _ATSPI_OK else "❌(仅影响日志)"))


# ════════════════════════ 上屏投递 ════════════════════════

def deliver(text: str) -> None:
    text = str(text or "")
    if not text:
        return
    n = len(text)
    if DELAY > 0:
        log(f"收到 {n} 字，{DELAY:.2f} 秒后上屏（现在切目标窗口/放光标还来得及）")
        time.sleep(DELAY)
    else:
        log(f"收到 {n} 字，开始上屏")

    if not IS_LINUX:
        preview = text if n <= 60 else text[:60] + "…"
        log(f"[非 Linux 系统] 仅显示: {preview}")
        return
    _deliver_linux(text, n)


def _deliver_linux(text: str, n: int) -> None:
    if _ATSPI_OK:
        # 白名单模式必须完整识别(安全门)；平时只查监听缓存(毫秒级)用于日志，
        # 焦点识别在 commit 快路径上不值得花几百毫秒扫全树
        acc = _atspi.focused_any() if ALLOWLIST is not None else _atspi.focused_cheap()
        if acc is not None:
            app, title = _atspi.describe(acc)
            try:
                role_name = acc.get_role().value_nick or "?"
            except Exception:
                role_name = "?"
            log(f"目标窗口: {title[:48] or '(无标题)'} [{app or '?'}·{role_name}]")
            if ALLOWLIST is not None:
                hay = f"{app} {title}".lower()
                if not any(str(x).lower() in hay for x in ALLOWLIST):
                    log(f"前台 {app or '?'} 不在 ALLOWLIST 白名单内，已跳过上屏")
                    return
        elif ALLOWLIST is not None:
            log("识别不到当前焦点，ALLOWLIST 模式下保守跳过")
            return

    _commit_with_report(text, n)


def _commit_with_report(text: str, n: int) -> bool:
    """走 IBus 主通道上屏,失败时按原因给出精确提示(手机/本机识别共用)"""
    st = _ibus_commit(text)
    if st == "OK":
        log(f"已 commit {n} 字（IBus 输入法通道）")
        return True
    if st == "NOFOCUS":
        # 引擎进程在跑但没有聚焦实例。多实例状态不可靠(实例可能已销毁、
        # 进程可能重启过)，直接问 ibus 当前全局输入源——这是唯一可靠的依据
        cur = ""
        try:
            cur = subprocess.run(["ibus", "engine"], capture_output=True,
                                 text=True, timeout=2).stdout.strip()
        except Exception:
            pass
        if cur == _IBUS_ENGINE:
            log("上屏失败：当前没有聚焦的输入框")
            log("点一下要输入的位置再发送（输入源已是「语音听写」🎙）")
        else:
            log(f"上屏失败：当前输入源是 {cur or '?'}，不是「语音听写」")
            log("请切回「语音听写」🎙（Super+Space），或把 AUTO_SWITCH_IME 改为 True")
    elif st == "FAIL":
        log("上屏失败：引擎 commit 异常")
    else:
        log(f"上屏失败：IBus 引擎未激活（{st}）")
        log("请把输入源切到「语音听写」🎙（Super+Space），或把 AUTO_SWITCH_IME 改为 True")
    return False


# ════════════════════════ 本机语音识别(SenseVoice) ════════════════════════

class _LocalVoice:
    """快捷键触发 → pw-cat 采 16k 单声道 → 整段缓冲 → 再按一次后
    SenseVoice 一次性解码 → commit 上屏"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.stop_flag = False
        self.proc = None
        self.worker = None
        self._rec = None
        self._so = None
        self._np = None

    def _recognizer(self):
        if self._rec is None:
            import glob
            import sherpa_onnx as so
            import numpy as np
            self._so, self._np = so, np
            pick = lambda pat: glob.glob(os.path.join(LOCAL_SENSEVOICE_DIR, pat))[0]
            self._rec = so.OfflineRecognizer.from_sense_voice(
                model=pick("model*.onnx"),
                tokens=os.path.join(LOCAL_SENSEVOICE_DIR, "tokens.txt"),
                num_threads=2, use_itn=True, language="zh")
            log("识别引擎: SenseVoice(已加载,常驻)")
        return self._rec

    def toggle(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                self.stop_flag = True
                return False, "结束录音，识别中…"
            if not os.path.isdir(LOCAL_SENSEVOICE_DIR):
                return False, f"本机识别未启用(缺模型目录 {LOCAL_SENSEVOICE_DIR})"
            try:
                self._recognizer()
            except Exception as e:
                log(f"识别模型加载失败: {e}")
                return False, f"模型加载失败: {e}"
            self.stop_flag = False
            self.worker = threading.Thread(target=self._run, daemon=True,
                                           name="local-voice")
            self.worker.start()
            return True, "开始录音，再按一次结束"

    def _run(self):
        np = self._np
        audio = []   # 整段采集缓冲
        # 硬件增益自愈:WirePlumber 偶发把它顶回 100% 导致削波,每次录音前强制拉回
        if MIC_HW_VOLUME and shutil.which("amixer"):
            try:
                subprocess.run(["amixer", "-c", "0", "sset", "Capture", "0",
                                MIC_HW_VOLUME], capture_output=True, timeout=3)
            except Exception:
                pass
        try:
            self.proc = subprocess.Popen(
                ["pw-cat", "-r", "--rate", "16000", "--channels", "1",
                 "--format", "s16", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except Exception as e:
            log(f"启动采集失败: {e}")
            return
        log("🎙 本机录音中…(再触发一次结束)")
        try:
            while not self.stop_flag:
                data = self.proc.stdout.read(3200)   # 0.1s 的 s16 单声道
                if not data:
                    break
                audio.append(np.frombuffer(data, dtype=np.int16)
                             .astype(np.float32) / 32768.0)
        finally:
            try:
                self.proc.kill()
            except Exception:
                pass
        # 调试转储:留最近一次音频,排查"没听清"用
        try:
            import wave as _wave
            pcm = np.clip(np.concatenate(audio) * 32767, -32767, 32767) \
                .astype("<i2") if audio else b""
            if len(pcm):
                with _wave.open("/tmp/voicedict_last.wav", "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                    w.writeframes(pcm.tobytes())
        except Exception:
            pass
        # 非流式:整段音频一次性解码(实测约 0.15x 实时,10 秒话音约 1.5 秒)
        text = ""
        try:
            log("识别中…")
            st = self._rec.create_stream()
            st.accept_waveform(16000, np.concatenate(audio))
            self._rec.decode_stream(st)
            text = st.result.text
        except Exception as e:
            log(f"SenseVoice 解码失败: {e}")
        text = (text or "").replace(" ", "").strip()
        if text:
            log(f"识别完成: {text}")
            _commit_with_report(text, len(text))
        else:
            log("没听清(无有效语音)")


_local_voice = None

def _ensure_local_voice():
    global _local_voice
    if _local_voice is None:
        _local_voice = _LocalVoice()
    return _local_voice


def _preload_asr():
    """服务启动时后台加载模型,首次按键零等待"""
    try:
        _ensure_local_voice()._recognizer()
        log("识别模型预加载完成,快捷键即时可用")
    except Exception as e:
        log(f"识别模型预加载失败: {e}")


def _local_voice_toggle():
    """供 /local-voice 端点调用;返回 (是否在录音, 提示)"""
    return _ensure_local_voice().toggle()


# ════════════════════════ 投递线程(主线程) ════════════════════════

def deliver_loop() -> None:
    """从队列取文字→上屏;替代原"客户端经 localhost SSE 订阅自己"的回环"""
    log("投递线程就绪，等待文字……")
    while True:
        text = deliver_q.get()
        try:
            deliver(str(text))
        except Exception as e:
            log(f"投递异常: {e}")


# ════════════════════════ 入口 ════════════════════════

def lan_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(("8.8.8.8", 80))   # 不会真发包，只为拿到本机出口 IP
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips


def main() -> None:
    if IS_LINUX:
        log(f"上屏通道: {_linux_backend_summary()}")
        if os.path.isdir(LOCAL_SENSEVOICE_DIR):
            log("本机识别: 模型就绪(Alt+V 或 /local-voice 触发)")
            threading.Thread(target=_preload_asr, daemon=True,
                             name="asr-preload").start()
        else:
            log("本机识别: 未装模型,仅手机通道")
    else:
        log("警告：当前不是 Linux，无法上屏，仅接收显示")

    try:
        run_server(block=False)
    except Exception as e:
        log(f"服务端启动失败: {e}")
        raise SystemExit(1)
    log(f"服务端启动 http://{HOST}:{PORT}")
    for ip in lan_ips():
        log(f"手机浏览器访问 http://{ip}:{PORT}")
    deliver_loop()   # 主线程常驻:取队列→上屏


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        log("已退出")
