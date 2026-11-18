"""直连本机的 AI 助手：工作台自己调用你电脑上已登录的 Claude Code / Codex / CodeBuddy（WorkBuddy 的命令行版），
用的是你在这些产品里的账号和额度，不用另外配模型、不用复制粘贴。

每个助手都用它自带的「无界面模式」跑一次：任务说明从标准输入送进去，结果从标准输出（或 Codex 的结果文件）读回来，
再交给 agent.submit 校验入库。格式不对会把报错发回去让它改一次。

也可以在设置里填一条自定义命令：任务说明从标准输入送进去，标准输出就是结果。
"""
import os
import shlex
import shutil
import subprocess
import tempfile

from . import config

# 只让它输出文字：不调用工具、不写文件（工作台自己负责保存）。避免引号、百分号等在 Windows 命令行里有特殊含义的字符。
INSTRUCTION = "标准输入里是一份完整的任务说明。请直接按说明完成任务，只输出结果正文，不要寒暄或解释，不要调用任何工具，不要写文件。"

RUNNERS = {
    "claude": {"name": "Claude Code", "bins": ["claude"], "login": "在命令行里运行一次 claude 并登录"},
    "codex": {"name": "Codex", "bins": ["codex"], "login": "在命令行里运行一次 codex 并登录"},
    "codebuddy": {"name": "CodeBuddy（WorkBuddy 的命令行版）", "bins": ["codebuddy", "cbc"], "login": "在命令行里运行一次 codebuddy 并登录"},
}
ORDER = ["claude", "codex", "codebuddy"]
TIMEOUT = 15 * 60


def which(key):
    for b in RUNNERS[key]["bins"]:
        path = shutil.which(b)
        if path:
            return path
    return None


def detect():
    """本机装了哪些助手：[{key, name, path}]"""
    return [{"key": k, "name": RUNNERS[k]["name"], "path": which(k)} for k in ORDER if which(k)]


def chosen():
    """按设置选出要用的助手：返回 (key, 显示名)；不用或没装返回 (None, None)。"""
    s = config.load_settings()
    mode = s.get("ai_runner") or "auto"
    if mode == "off":
        return None, None
    if mode == "custom":
        return ("custom", "自定义命令") if (s.get("ai_runner_cmd") or "").strip() else (None, None)
    if mode in RUNNERS:
        return (mode, RUNNERS[mode]["name"]) if which(mode) else (None, None)
    for k in ORDER:
        if which(k):
            return k, RUNNERS[k]["name"]
    return None, None


def command(key, out_file):
    if key == "custom":
        cmd = config.load_settings().get("ai_runner_cmd", "").strip()
        return shlex.split(cmd, posix=os.name != "nt")
    exe = which(key)
    if key == "claude":
        return [exe, "-p", "--output-format", "text", INSTRUCTION]
    if key == "codebuddy":
        return [exe, "-p", "--output-format", "text", INSTRUCTION]
    # Codex：- 表示从标准输入读任务；最后一条回复另外写进文件，进度信息不会混进结果
    return [exe, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "-o", str(out_file), "-"]


def run(prompt, key=None, timeout=TIMEOUT, on_proc=None):
    """把任务说明交给本机助手，返回它写的结果文本。失败抛 RuntimeError（带上助手自己的报错）。"""
    if key is None:
        key, _ = chosen()
    if not key:
        raise RuntimeError("没有找到可用的 AI 助手命令行")
    work = config.DATA / "agent" / "run"  # 在一个空目录里跑，助手不会去读工作台的代码和说明
    work.mkdir(parents=True, exist_ok=True)
    fd, out_file = tempfile.mkstemp(prefix="result-", suffix=".md", dir=work)
    os.close(fd)
    try:
        cmd = command(key, out_file)
        if key in ("codex", "custom"):
            stdin_text = INSTRUCTION + "\n\n" + prompt
        else:
            stdin_text = prompt
        env = dict(os.environ, PYTHONIOENCODING="utf-8", NO_COLOR="1")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen(cmd, cwd=str(work), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", creationflags=flags)
        if on_proc:
            on_proc(proc)
        try:
            out, err = proc.communicate(stdin_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise RuntimeError(f"等了 {timeout // 60} 分钟还没写完，已停止")
        if key == "codex":
            try:
                with open(out_file, encoding="utf-8") as f:
                    out = f.read() or out
            except OSError:
                pass
        out = (out or "").strip()
        if proc.returncode != 0 or not out:
            tail = (err or out or "").strip()[-800:]
            hint = RUNNERS.get(key, {}).get("login", "检查自定义命令")
            raise RuntimeError(f"助手没有返回结果（退出码 {proc.returncode}）。如果是没登录或额度用完，请{hint}。\n{tail}")
        return out
    finally:
        try:
            os.remove(out_file)
        except OSError:
            pass


def test(key=None):
    """用一句很短的话试一下能不能调通。"""
    out = run("这是连通测试。只回复两个字：好的", key=key, timeout=180)
    return out[:200]
