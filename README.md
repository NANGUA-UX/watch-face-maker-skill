# 表盘制作助手

`watch-face-maker` 是在Codex中使用的Figma表盘制作Skill：参考设计评审、原生效果制作、切图母件与实例、数字和指针表盘、AOD、位置标注、资源命名及实际产物检查。用户可见说明使用中文。

## 安装

在Codex中直接发送：

```text
https://github.com/NANGUA-UX/watch-face-maker-skill 帮我安装这个skill
```

本仓库公开可读，下载Skill不需要仓库所有者另行授权。仓库只提供一个Skill：默认分支`main`下的`skills/watch-face-maker/`。Codex可通过内置`skill-installer`识别并安装该目录，无需安装整个仓库。

需要显式指定时：

```text
使用 $skill-installer 安装：
--repo NANGUA-UX/watch-face-maker-skill
--ref main
--path skills/watch-face-maker
```

安装后在下一轮调用`$watch-face-maker`，显示名称为“表盘制作助手”。同名技能已经存在时先核对版本与本地修改，再更新原副本；GitHub更新不会自动同步到已安装副本。

## 使用准备

- 安装并连接Codex的Figma插件，拥有目标文件的编辑权限及所用规范、案例和组件库的访问权限。
- 提供参考图、目标节点、命名与开发规范、组件库，以及语言、单位、动画等实际需求。所用字体需在Figma环境可用。
- 本地检查使用Python 3；PNG检查需要Pillow，可通过`uv run --with pillow`运行。安装Skill不会自动授予外部账号或文件权限。

```text
使用 $watch-face-maker，根据这张参考图在以下Figma节点制作表盘：……
规范：……；组件库：……；特殊要求：……。
```

## 默认约定与验证范围

默认英文、单主题、480×480圆屏、AOD及公英制资源。按需要采用Figma原生效果，复杂区域先局部试做；仍无法实现的局部质感再使用透明底效果素材。约95%相似是用户视觉目标，不是自动评分或保证。

附带脚本目前使用固定的480圆屏校验规则集，详见[开发能力与适用范围](skills/watch-face-maker/references/development-capabilities.md)。其他屏幕、状态枚举或导出规范需要适配验证逻辑，不能假设已经通用支持。语义、实际视觉、用户确认和开发真机验证分别记录；脚本通过不等于完整验收。

本仓库只包含通用Skill、校验脚本和说明，不附带项目素材、用户反馈图、项目记忆、内部规范或私有Figma案例。新项目使用自己的来源与记忆文件。
