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

## 更新已安装版本

在Codex中发送：

```text
帮我更新已经安装的 watch-face-maker。
来源：https://github.com/NANGUA-UX/watch-face-maker-skill
分支：main
目录：skills/watch-face-maker
先找到原安装位置，检查并备份旧版本及本地修改。
下载完整Skill目录，更新原副本，并清理新版已移除的旧文件。
不要只替换SKILL.md，也不要另装一份同名Skill。
完成后运行校验，报告来源提交SHA和实际安装路径。
```

安装器会拒绝覆盖已有目录，更新应先在临时目录取得完整新版，核对、备份后再替换原安装副本；`scripts/`、`references/`和`agents/`需一并更新。更新后在新任务中调用`$watch-face-maker`。只对安装目录更新，保留用户项目文件和项目记忆。

## 本次更新（2026-10-12）

新增尺寸和屏形确认、七阶段工作流合同、代表性样稿门禁、视觉并排/叠图/局部图与当前产物绑定；同步资源对齐、AOD至少2px描边、亮屏/AOD字模配对尺寸、连写切图前缀及校验修复。

本次公开包通过273项工程回归、Skill结构校验和文档链接检查。工程回归与Skill结构校验不等于视觉或真机验收。三档模型的真实压力矩阵和隔离Figma完整制作回归尚未执行，当前版本不声称已达到跨模型视觉稳定性的实测通过标准。

## 使用准备

- 安装并连接Codex的Figma插件，拥有目标文件的编辑权限及所用规范、案例和组件库的访问权限。
- 提供参考图、目标节点、命名与开发规范、组件库，以及语言、单位、动画等实际需求。所用字体需在Figma环境可用。
- 本地检查使用Python 3；PNG检查需要Pillow，可通过`uv run --with pillow`运行。安装Skill不会自动授予外部账号或文件权限。

```text
使用 $watch-face-maker，根据这张参考图在以下Figma节点制作表盘：……
规范：……；组件库：……；特殊要求：……。
```

## 默认约定与验证范围

目标尺寸与屏形必须由用户确认，不默认480×480。默认英文、单主题、AOD及公英制资源，用户明确要求优先。简单效果优先Figma，复杂视觉内容可直接AI局部生成；按结构准确性、质量和总工时选择，保留AI素材清晰度门槛。约95%相似是用户视觉目标，不是自动评分或保证。

附带脚本支持480/466/390圆屏与390×450圆角方屏的独立校验、共享资源映射和批次导出规划，详见[开发能力与适用范围](skills/watch-face-maker/references/development-capabilities.md)。上述范围外的屏幕、状态枚举或导出规范需要适配验证逻辑，不能假设已经通用支持。语义、实际视觉、用户确认和开发真机验证分别记录；脚本通过不等于完整验收。

本仓库只包含通用Skill、校验脚本和说明，不附带项目素材、用户反馈图、项目记忆、内部规范或私有Figma案例。新项目使用自己的来源与记忆文件。
