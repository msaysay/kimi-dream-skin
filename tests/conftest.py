"""测试用的环境：数据写到临时目录，采集指向本机的假平台站点，等待时间缩短。"""
import os
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


FAKE_PORT = free_port()
os.environ["WORKBENCH_DATA"] = tempfile.mkdtemp(prefix="workbench-test-")
os.environ["WORKBENCH_WAIT"] = "0.15"
os.environ["WORKBENCH_HEADLESS_SEND"] = "1"
for key in ("XHS", "DOUYIN", "CHANNELS"):
    os.environ[f"WORKBENCH_URL_{key}"] = f"http://127.0.0.1:{FAKE_PORT}"
