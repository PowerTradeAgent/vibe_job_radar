# Windows verify_code 20：修复构链方式，而不是重装浏览器

## 已知与未知

现场报告为 Python/OpenSSL 到 cloudflare-dns.com 的 TLS 握手出现
SSLCertVerificationError / verify_code 20，未发送 DoH 正文。现有上下文有294个CA；
这并不表示其中包含本次验证所需的签发者。编号20表示无法找到构链所需的签发者，
不是“确定证书过期”、不是BOSS403，也没有确定某个VPN或安全产品有问题。

Python的默认SSLContext会加载Windows CA/ROOT库，但仍由OpenSSL构链。
加载一份CA快照不等于调用Windows原生链引擎；缺少中间证书或系统管理的
构链材料时，两条路径可能产生不同结果。未取得用户实际链，不能断言本机原因已根治。

## 本次实际连接改动

Windows且没有SSL_CERT_FILE/SSL_CERT_DIR显式配置时，安装受支持的truststore后，
统一PinnedHTTPSConnection在拨号前创建truststore.SSLContext(PROTOCOL_TLS_CLIENT)，
由Windows CryptoAPI完成可信链、有效期、用途与主机名验证。只使用系统信任策略，
不从失败连接导入证书、不改证书库、不注入全局ssl，也不调用CERT_NONE。
TLS最低1.2、CERT_REQUIRED、check_hostname保持；验证成功前不发送HTTP/DNS正文。

所有原PinnedHTTPSConnection用户（DoH、公开来源、浏览器Python请求桥）使用此工厂。
目标IP绑定、SNI、HTTP/SOCKS路线、站点访问规则和配额均未改变。证书拒绝不换IP、
路线、解析商或验证器重试。缺少可选组件时保持原有完整校验的OpenSSL路径；
组件已安装但损坏/不兼容时停止，不能默默退回。显式CA环境配置保持原OpenSSL语义，
不被系统路径忽略。非Windows行为不变。

**证书网络边界：** Windows链引擎可按系统策略取得缺少的中间证书及系统管理的
链材料。相关证书服务访问由操作系统管理，不经本应用的岗位代理和采集配额；
不能承诺所有证书查询与DoH采用同一路线。现有套接字超时在调用返回后仍检查，
系统构链调用本身也可能耗时，不能把它说成能由网页立即强制取消。
这是系统已有信任政策的链构建，不是自动信任收到的未知根证书。
未受信根、错误主机名、过期或用途错误仍应拒绝；默认没有新增强制CRL规则。

## 用户操作（审查合并后）

保留已可用的Edge。向导中展开“证书链验证失败？修复 Windows 系统证书验证组件”，
确认并点击“安装 Windows 证书验证组件”。只安装truststore>=0.10.4,<0.11，
不升级Playwright、不下载Chromium，不修改信任根或任意路径。已有采集会话先停止。
安装结束后退出并重启原工作台；同一网络检查应显示tls_environment.engine=
windows_cryptoapi。该字段表示所选引擎，**不是**TLS或网站已成功。
原网络检查的passed=true/effective_dns_ok才表示应用解析成功。

已有显式CA配置时按钮不覆盖它。安装过程或原生验证仍失败时保持错误，
由管理员核对真实可信根和中间链；不要使用trusted-host、关闭证书校验，
不要从聊天/未知站点下载安装所谓修复根证书。SSL拒绝继续保留原30秒保护等待。
不需要切换VPN、重装系统或反复新建任务。

## 验证

新增Windows专门CI生成独立临时根/中间/叶子证书。根仅加载到测试上下文，
不写入Windows证书库。服务端故意只发送叶子，OpenSSL先实际失败为20；
原生引擎通过本机AIA服务获取缺失中间后，原生产连接成功。随后验证不可信根、
错误主机名、过期及错误用途全部拒绝且不发送HTTP、不换路线或IP。
原Fake-IP/DoH请求链通过同一受控TLS服务验证。另有真实Windows系统信任的
固定公开来源检查，仍不能代表用户Windows10/TUN或BOSS现场成功。

依赖库为truststore官方SSLContext公开API（不使用pip内置私有副本）。
新诊断保留Windows32位验证码，并将不可枚举的原生证书计数表示为未知，
不误报0个证书。所有旧CI保留，增加原生组件已安装时的全量回归和实际UI测试。

参考：
- https://docs.python.org/3.12/library/ssl.html#ssl.SSLContext.load_default_certs
- https://docs.openssl.org/3.0/man3/X509_STORE_CTX_get_error/
- https://truststore.readthedocs.io/en/latest/
- https://learn.microsoft.com/en-us/windows/win32/api/wincrypt/nf-wincrypt-certgetcertificatechain
