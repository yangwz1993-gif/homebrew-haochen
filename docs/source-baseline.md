# 新版本源码基线

- 新项目路径：`/Users/yangwz/Documents/workspace/haochen`
- 来源路径：`/Users/yangwz/Documents/workspace/haochen_new`
- 来源版本：0.1.10（以打包脚本和 Cask 配置为准）
- 新开发版本：0.2.0-dev.1

## 迁移原则

已复制当前应用、引擎源码、配置模板、测试、文档及打包源码；未复制以下内容：

- Python 虚拟环境、Node/Bun 依赖目录；
- build/dist、DMG、App、PyInstaller 产物；
- 编译后的 59MB 引擎二进制；
- 旧版参考代码 `previous-version/`；
- 签名私钥、证书、P12 与 keychain 口令；
- 运行日志、缓存和协作临时文件；
- 子目录原有 `.git` 元数据。

需要的依赖与二进制必须由可复现脚本重新生成，签名凭据只允许通过 macOS Keychain 或 CI Secret 注入。
