"""假的 AI 助手命令行：从标准输入读任务说明，按任务类型输出结果。用来测试「工作台直连本机 AI 助手」。"""
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
task = sys.stdin.read()
if "连通测试" in task:
    print("好的")
elif "周复盘" in task:
    print("# 本周复盘\n\n结论：故事型开头留住了人，下周做成系列。")
elif "拆解一条对标爆款" in task:
    if "没有通过检查" in task:  # 第二次：按报错改好
        print("好的，改好了：\n---\n原型: 教程型\n开头钩: 反常识断言\n---\n## 开头怎么抓人\n一句反常识。")
    else:  # 第一次故意漏掉「原型」
        print("---\n开头钩: 反常识断言\n---\n## 开头怎么抓人\n一句反常识。")
elif "出稿" in task or "口播稿" in task or "0–3 秒" in task:
    print("好的，下面是稿子：\n\n" + open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else "")
else:
    sys.exit(3)
