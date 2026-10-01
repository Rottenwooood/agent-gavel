"""本地综合测试站点服务（tests/webapp/）+ 特殊端点。

- 静态文件：tests/webapp/
- /slow  ：2s 后响应（测超时/等待）
- /hang  ：挂起 30s（测导航超时）

被 conftest.py（pytest fixture）与 bench_runtime.py（独立基准）共用。
"""

import functools
import http.server
import os
import threading
import time

WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, directory=None, **k):
        super().__init__(*a, directory=directory, **k)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/slow":
            time.sleep(2.0)
            return self._text("slow done")
        if path == "/hang":
            time.sleep(30.0)
            return
        if path == "/redirect302":
            self.send_response(302)
            self.send_header("Location", "/basic.html")
            self.end_headers()
            return
        return super().do_GET()

    def _text(self, text):
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def start_server(directory=WEBAPP_DIR):
    """起服务，返回 (httpd, base_url)。调用方负责 httpd.shutdown()。"""
    handler = functools.partial(Handler, directory=directory)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}"
