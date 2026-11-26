# 第三方代码与对应源码

`ui.html` 包含 npm 发布的 `h264-mp4-encoder@1.0.12` WebAssembly 编码器。构建脚本从锁定的 npm 包读取原始发行文件，保留其 MIT 许可文本。

| 组件 | 许可与对应源码 |
| --- | --- |
| h264-mp4-encoder 封装 | MIT，许可见 `ui.html`。 [1.0.12 源码与构建脚本](https://github.com/TrevorSundberg/h264-mp4-encoder/tree/6d177fd043157606224cef4702e134dd31f6adfa) |
| minih264 | [CC0 1.0 许可声明](LICENSE.minih264.txt)。 [对应源码](https://github.com/TrevorSundberg/minih264/tree/25f441086ac8f2eef1c883476c095f9397843ac8) |
| libmp4v2 | [Mozilla 公共许可证 1.1](LICENSE.libmp4v2.txt)。 [对应源码](https://github.com/TrevorSundberg/libmp4v2/tree/d49b4466ed76fc23b59e31a5f7f10f34b30ad7c4) |

内嵌的 libmp4v2 部分的源码可按 MPL 1.1 获取。可从上表对应提交下载源代码，也可递归检出封装仓库的固定提交以取得其构建脚本和固定的子模块。
