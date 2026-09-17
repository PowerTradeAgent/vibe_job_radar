# 检查通过而采集仍报 non_public_address：浏览器回调策略绑定

## 现场与结论边界

PR42后，用户Windows原生证书验证已使BOSS的Cloudflare加密解析通过。
随后猎聘页面仍报non_public_address/ERR_BLOCKED_BY_CLIENT。BOSS诊断不能
当成猎聘或目标页面的连接成功证据；两个域名仍需分别验证。

本轮从main@5102485复现一个与域名无关的实际接线缺陷：GuidedService在工作线程
用ContextVar设置工作区策略，但PinnedTransport直到第一次fetch才读取它。
Playwright同步API的路由回调运行在独立greenlet/context中，不保证继承调用方ContextVar。
回调于是重新读取未启用工作区DoH的环境策略，Fake-IP被原有公网检查拒绝。
直接调用HTTP桥的旧测试没有跨过这个回调边界，故未发现此问题。

## 修改

PlaywrightBackend创建传输实例后、启动Playwright调度前，在所属上下文中调用
bind_policy(current_policy())。PinnedTransport验证并保存这一不可变快照；同一个
会话不能再绑定其他快照。所有robots、document、script、xhr/fetch和正文请求
共用同一工作区策略与resolver实例。离线/空白页测试传输无需实现此可选能力；
独立调用PinnedTransport的原先首次请求冻结行为保留。

这不是把198.18地址放行，不改变TLS、Windows信任、SNI、域名、robots、POST、
限频或失败处理。也没有更换DNS供应商、自动切换出口或改Playwright内部greenlet。
已有会话不热切换；撤销仍由原共享resolver的实时权限检查生效。

## 验证

新增单元测试在空Context中执行原_route，改前复现non_public_address，改后通过。
同时覆盖多类资源、不重新发现代理、工作区隔离、原会话不热切换、撤销/私网/
混合回答/TLS拒绝/robots和写请求限制。

新增真实Playwright集成脚本由原GuidedService驱动，不替换浏览器HTTP桥：
同域名工作区诊断→真实浏览器调度→原公网IP绑定TLS→受控DoH→robots/动态列表/
脚本/fetch→选取正文→原研究报告，再验证撤销和另一未同意工作区。
只有测试套接字映射和测试信任上下文使用既有人工夹具，不允许从生产API设置。
Linux Chromium与Windows Edge工作流都执行该脚本，原检查全部保留。
本地执行容器的浏览器导航被ERR_BLOCKED_BY_ADMINISTRATOR拒绝，未绕过；
实际浏览器是否通过以相同提交的CI结果为准。夹具结果不是猎聘实站认证。

## 合并后的本机验证

不再安装任何组件。停止旧工作台，更新至合并后main，保留原工作区、Edge和
加密解析同意，重新启动。在“猎聘”下检查当前网络策略，核对host确为
www.liepin.com。通过后发起原1页/最多5条的小批任务；新会话会绑定正确策略。
若出现新的TLS/robots/HTTP/页面结构错误，按新的具体错误处理，不反复安装。

参考：greenlet Context Variables（新greenlet的Context默认不继承）；
Playwright Python RouteHandler/_context_manager（同步调度）；
项目network_policy.py、guided/browser.py、guided/transport.py。
