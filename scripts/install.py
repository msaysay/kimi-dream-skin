"""一键安装：建虚拟环境、下载发布引擎 social-auto-upload、装依赖和浏览器。

只用标准库，任何 Python 3 都能运行它；真正跑工作台的 Python 需要 3.10-3.12（发布引擎的要求）。
可以重复运行。加 --latest 下载发布引擎的最新版，而不是工作台测试过的那个版本。
"""
import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
SAU = ROOT / "vendor" / "social-auto-upload"
# 工作台测试过的 social-auto-upload 版本
SAU_COMMIT = "3847fff01f5434a8a32829833163cd6723cabc59"
SAU_ZIP = "https://github.com/dreammis/social-auto-upload/archive/{ref}.zip"
SAU_GIT = "https://github.com/dreammis/social-auto-upload"
WINDOWS = os.name == "nt"
VENV_PY = VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def say(text):
    print("\n==> " + text, flush=True)


def version_of(cmd):
    try:
        out = subprocess.run([*cmd, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
                             capture_output=True, text=True, timeout=30).stdout.split()
        return int(out[0]), int(out[1])
    except Exception:
        return None


def find_python():
    """找一个 3.10-3.12 的 Python。"""
    candidates = [[sys.executable]]
    for minor in (12, 11, 10):
        candidates += [["py", f"-3.{minor}"]] if WINDOWS else [[f"python3.{minor}"]]
    for cmd in candidates:
        v = version_of(cmd)
        if v and v[0] == 3 and 10 <= v[1] <= 12:
            return cmd
    return None


def run(cmd, **kw):
    print("   $ " + " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def fetch_sau(ref, tmp):
    """先试压缩包；不行再用 git（装了的话）。返回解出来的源码目录。"""
    try:
        with urllib.request.urlopen(SAU_ZIP.format(ref=ref), timeout=120) as r:
            zipfile.ZipFile(io.BytesIO(r.read())).extractall(tmp)
        return next(p for p in tmp.iterdir() if p.is_dir())
    except Exception as e:
        if not shutil.which("git"):
            raise
        print(f"   压缩包下载失败（{e}），改用 git")
        src = tmp / "repo"
        run(["git", "clone", "--quiet", SAU_GIT, src])
        if ref != "HEAD":
            run(["git", "-C", src, "checkout", "--quiet", ref])
        shutil.rmtree(src / ".git", ignore_errors=True)
        return src


def download_sau(ref):
    say(f"下载发布引擎 social-auto-upload（{ref[:12]}）")
    tmp = SAU.parent / "_download"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    src = fetch_sau(ref, tmp)
    # 保留已有的登录状态和配置
    for keep in ("cookies", "conf.py"):
        if (SAU / keep).exists():
            shutil.rmtree(src / keep, ignore_errors=True)
            shutil.move(str(SAU / keep), str(src / keep))
    shutil.rmtree(SAU, ignore_errors=True)
    shutil.move(str(src), str(SAU))
    shutil.rmtree(tmp, ignore_errors=True)
    (SAU / ".workbench-version").write_text(ref, encoding="utf-8")
    if not (SAU / "conf.py").exists():
        shutil.copy(SAU / "conf.example.py", SAU / "conf.py")


def main():
    latest = "--latest" in sys.argv
    skip_browser = "--skip-browser" in sys.argv
    py = find_python()
    if py is None:
        print("没有找到 Python 3.10 - 3.12（发布引擎不支持更新或更旧的版本）。\n"
              "请到 https://www.python.org/downloads/release/python-3129/ 下载安装 Python 3.12，\n"
              "安装时勾选 Add python.exe to PATH，然后重新运行本安装程序。")
        return 1
    if not VENV_PY.exists():
        say("创建虚拟环境 .venv")
        run([*py, "-m", "venv", VENV])
    ref = "HEAD" if latest else SAU_COMMIT
    marker = SAU / ".workbench-version"
    if latest or not marker.exists() or marker.read_text(encoding="utf-8") != ref:
        SAU.parent.mkdir(parents=True, exist_ok=True)
        try:
            download_sau(ref)
        except Exception as e:
            print(f"下载失败：{e}\n访问 GitHub 不稳定时可以多试几次，或者开代理后再运行。")
            return 1
    env = dict(os.environ)
    # 国内网络默认走镜像；已经自己配过就不覆盖
    env.setdefault("PIP_INDEX_URL", "https://pypi.tuna.tsinghua.edu.cn/simple")
    env.setdefault("PLAYWRIGHT_DOWNLOAD_HOST", "https://npmmirror.com/mirrors/playwright")
    say("安装依赖（第一次需要几分钟）")
    pip = [VENV_PY, "-m", "pip", "install", "--disable-pip-version-check", "-e", SAU]
    try:
        run(pip, env=env)
    except subprocess.CalledProcessError:
        if "PIP_INDEX_URL" in os.environ:
            raise
        print("   镜像源安装失败，改用官方源重试")
        run(pip, env={k: v for k, v in env.items() if k != "PIP_INDEX_URL"})
    if not skip_browser:
        say("下载自动化用的浏览器 Chromium")
        run([VENV_PY, "-m", "patchright", "install", "chromium"], env=env)
    say("安装完成。双击「启动.cmd」打开工作台。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        print(f"\n安装中断：上面这一步失败了（退出码 {e.returncode}）。把这个窗口里的内容发给开发者排查。")
        sys.exit(1)
