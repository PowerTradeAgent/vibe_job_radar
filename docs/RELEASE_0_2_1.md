# 0.2.1 发布保障维护候选

基于 main@a4f8e009f9cf03919d0a86b462e7047778e24de6。版本在本PR中，未合并、未创建正式Release/tag。它**不增加Fake-IP、自动系统代理、PAC、认证或网页网络设置，也不包含尚未合并的PR26**。

## 本轮真正改变的行为

过去可以直接执行build_candidate.py创建不附带当前源码验证的候选包。现在必须先运行verify_candidate.py，实际完成四项检查，且打包时源码指纹仍完全一致才允许构建。源码、文档或测试增加/删除/修改后，旧结果自动失效；缺报告、零测试、失败、缺少任一检查或执行期间源码变化都会拒绝。

本地一条命令复用现有测试、HTML指南同步、离线演示和源码启动doctor。明确显示当前解释器、检查结果、源码文件SHA-256与总指纹；浏览器、远端CI、真实网站验证仍是独立状态，不能因本地success就称全部通过。

原有7个OS/Python测试矩阵、7条浏览器脚本和3组浏览器就绪检查保留。单测避免功能分支push和pull_request双触发；同PR新提交取消旧提交进行中的检查，main和手动运行不互相覆盖；保留7天产物，增加明确超时。这不能修复账户/runner配置，应继续追踪#28。

## 用你已有的Python验证

下载本PR完整源码，解压并进入源码目录。先关闭运行中的旧工作台，不用重装Python。Git Bash执行：

```bash
/d/code_environment/anaconda_all_css/py312/python.exe scripts/verify_candidate.py
```

程序会逐步显示unit-tests、user-guide、offline-demo、source-doctor，结果保存在 `release-verification/result.json`。这不会登录招聘网站、安装浏览器、改变系统代理或修改账户。离线演示在源码 `runs/` 生成合成结果，临时doctor目录结束后删除；不会读取默认个人工作区。

测试含本机回环HTTP/TLS夹具，它们不是外部招聘网站访问。真实源码可能因你平台的本地约束出现失败，查看result.json中的step、returncode与脱敏输出，不要把失败修改成true。

四项成功后，打包：

```bash
/d/code_environment/anaconda_all_css/py312/python.exe scripts/build_candidate.py
```

产物位于 `release-candidate/vibe-job-radar-0.2.1-source.zip`，附manifest.json。ZIP里的CANDIDATE.json保留源码指纹和本次本地验证结果，并明确标记 `locally_verified_candidate_not_release`、远端另验、实站未认证。改代码后应重新验证，不能直接沿用旧JSON。

本地结果不是签名证书，不能抵抗拥有文件写权限的人主动伪造验收文件；其目的为防止错把旧结果/另一份源码当作当前版本，并提供可复现入口。正式发布仍需审查与远端/实站要求。

## 包含与排除

只打包约定源码目录及根文件，不包含根目录 `.env`、个人SQLite、runs、浏览器运行日志、临时目录和输出ZIP。源码目录里的文件被视为受审源码，**不要把个人凭据或用户数据放进src/tests/docs/examples**；本功能不是通用秘密扫描器。公开测试专用证书保留，不能用于生产。

符号链接来源拒绝；每个打包文件、打包后的源码再核对哈希。CANDIDATE里的本地日志可能出现机器路径，公开分享前应检查。源码的版本元数据、CLI和报告仍使用同一_version.py。

## 本轮未交付的采集修复

#27发现了robots允许访问但要求间隔大于15秒就被项目拒绝的问题，本地实验已经复现并测试；仓库写入被工具安全检查拒绝，**未进入这个候选包**。不要期待升级此维护候选后publisher_delay_exceeds_policy自动消失。

完整开放需求的分类、主跟踪、P0/P1/P2与卡点见[开放需求审计](OPEN_ISSUES_0_2_1.md)。网络主需求#23、站点路线#9、SOCKS子任务#25和CI问题#28继续开放。
