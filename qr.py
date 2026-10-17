#!/usr/bin/env python3
"""打印当前局域网地址 + 终端二维码,手机扫码直达听写页面"""
import socket, qrcode

def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))   # 不真发包,只为取出口 IP
        return s.getsockname()[0]
    finally:
        s.close()

ip = lan_ip()
url = f"http://{ip}:8765"
print(f"\n  手机扫码或输入: {url}\n")
qr = qrcode.QRCode(border=1)
qr.add_data(url)
qr.print_ascii(invert=True)
