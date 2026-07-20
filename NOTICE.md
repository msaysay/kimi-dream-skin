# 致敬与第三方声明 / Acknowledgements

## Codex Dream Skin

本项目（Kimi Dream Skin）的设计灵感与实现思路来自开源项目
**[Fei-Away/Codex-Dream-Skin](https://github.com/Fei-Away/Codex-Dream-Skin)**（MIT License）。
在此向原作者与贡献者致谢。

我们参考并借鉴了以下内容：

- **技术路线**：Electron 应用以 `--remote-debugging-port` 启动后，通过仅绑定
  `127.0.0.1` 的 CDP（Chrome DevTools Protocol）注入主题 CSS，不修改官方安装包与签名；
- **设计规范**：2560×1440 级背景图 + 左侧 x=0~52% 文字安全区 + 右侧 x=62~88% 主视觉焦点
  的构图法则，以及 `docs/background-generation-prompts.md` 中的生图提示词方法论；
- **主题方向**：青蓝歌姬、粉雾玫瑰、财神打工、舞台黑金、红白科幻五套主题分别临摹其
  `skin-07 / skin-01 / skin-02 / skin-08 / skin-03` 概念方向的视觉语言；
- **配色令牌**：`gothic.css`（哥特虚空）的配色数值取自其官方预设
  `macos/presets/preset-gothic-void-crusade/theme.json`。

## 素材权利

- 本仓库 `windows/themes/assets/` 中的背景图均为 **AI 生成的原创虚构角色/场景**，
  按照上述提示词方法论全新生成，不包含、不复制 Codex Dream Skin 仓库中的任何图片资产；
- 图中人物均为虚构形象，不与任何真实自然人、艺人或版权角色对应；
- 若你替换为自己的背景图，请自行确认素材的版权与肖像权。

## 免责声明

- 本项目与月之暗面（Moonshot AI）、OpenAI 均无任何关联，非双方官方产品；
- "Kimi"、"Codex" 及相关商标权利归其各自权利人所有；
- Codex Dream Skin 的 MIT License 全文见其仓库；本项目本身同样以 MIT License 发布（见 LICENSE）。
