# 致谢与第三方组件

- **[JaelChen/video-data-dashboard](https://github.com/JaelChen/video-data-dashboard)**（MIT）：三个平台创作者后台的数据接口路径、字段对应关系、
  「和自己的中位数比」以及跨平台作品配对的做法，都改编自这个项目。对应代码在 `workbench/collect.py`、`workbench/ingest.py`、`workbench/analytics.py`。
- **[dreammis/social-auto-upload](https://github.com/dreammis/social-auto-upload)**（MIT）：登录和发布由它完成。本仓库不包含它的代码，
  安装时下载到 `vendor/social-auto-upload/`，工作台通过它的命令行调用。
- **[Apache ECharts](https://echarts.apache.org/)**（Apache-2.0）：图表库，随仓库附带在 `web/vendor/echarts.min.js`，许可证见 `web/vendor/echarts.LICENSE`。
- **[3441293738/creatorhub](https://github.com/3441293738/creatorhub)**：评论自动回复的设计思路参考了它，包括关键词规则、模板库、同义替换、先出草稿再发送、每日上限和间隔。该仓库没有声明开源许可证，所以本项目没有复制它的代码，`workbench/comments.py` 是重新写的。
- **《宁少AI进化论 · 三平台内容数据分析方法论》**：复盘的口径、字典、阈值和决策表来自这份文档，对应代码在 `workbench/method.py`、`analytics.py`、`planner.py`、`review.py`。
