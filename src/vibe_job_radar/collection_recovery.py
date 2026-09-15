"""Human-readable collection outcomes for the existing HTTP path."""
from __future__ import annotations

REASONS = {
    'local_proxy_configuration_conflict': ('同时配置了HTTP与SOCKS入口。请只保留一种，本程序不会猜测或在失败后自动改走另一条线路。', '查看本机代理配置；不要增加采集预算或反复新建任务。'),
    'local_socks_configuration_invalid': ('SOCKS设置无效：仅支持明确的本机socks5入口和实际端口；不支持认证、socks5h或宿主机LAN地址。', '查看本机代理配置；不要增加采集预算或反复新建任务。'),
    'local_socks_auth_unsupported': ('该SOCKS服务要求认证或没有接受匿名方式；本阶段不支持认证。请勿填招聘账号，也不会自动直连。', '查看本机代理配置；不要增加采集预算或反复新建任务。'),
    'local_socks_request_rejected': ('本机SOCKS服务拒绝连接目标。核对代理规则，程序不会换地址或直连继续请求。', '查看本机代理配置；不要增加采集预算或反复新建任务。'),
    'local_socks_protocol_error': ('SOCKS响应格式不兼容。检查是否误填HTTP端口或SOCKS4端口，本阶段要求SOCKS5。', '查看本机代理配置；不要增加采集预算或反复新建任务。'),
    'local_socks_truncated_reply': ('SOCKS握手尚未完成连接就断开。不是职位URL或网站登录问题。', '查看本机代理配置；不要增加采集预算或反复新建任务。'),
    'local_socks_timeout': ('连接本机SOCKS或握手超时。检查实际端口与服务状态，不增加职位预算。', '查看本机代理配置；不要增加采集预算或反复新建任务。'),
    'local_socks_connection_failed': ('无法连接所选本机SOCKS端口；服务可能未开启。程序不会自动直连。', '查看本机代理配置；不要增加采集预算或反复新建任务。'),
    'ok': ('已取得完整正文', '可以下载本批报告并核对原文。'),
    'fresh_reused': ('复用了已保存的新鲜正文', '没有为这条记录重复访问网站。'),
    'budget_skipped': ('未执行：本批正文尝试预算已用完', '不是抓取失败；先确认已尝试条目的问题，再为剩余链接新建小批次。'),
    'local_proxy_configuration_invalid': ('本机HTTP代理配置不符合要求', '只能填写明确的本机HTTP代理地址；此版本不支持代理账号、SOCKS或远程代理。不要在职位URL中填写代理地址。'),
    'local_proxy_connection_failed': ('所选本机代理无法建立连接或隧道', '核对代理实际HTTP监听端口。程序不会因代理失败自动直连；增加职位预算不能修复代理连接。'),
    'tls_verification_failed': ('TLS证书验证失败', '请检查系统时间和证书，不要关闭TLS验证。'),
    'tls_handshake_failed': ('TLS握手失败', '检查网络及服务端TLS支持；不自动重放已发送请求。'),
    'redirect_not_followed': ('旧版遇到跳转即停止，目标未被记录', '历史日志不能判断是否需要登录。更新后新建任务可看到跳转阶段，或转到浏览器向导。'),
    'redirect_login_required': ('网站跳转到了登录入口', 'HTTP路线没有浏览器登录会话。转到浏览器向导，在平台原生页人工登录后再采集。'),
    'redirect_verification_required': ('网站跳转到了验证入口', '自动请求已停止；在浏览器确认访问条件，不重复增加预算。'),
    'login_or_challenge': ('返回了登录或验证页面，不是职位正文', '使用浏览器向导人工处理；本次没有保存该页面为JD。'),
    'redirect_domain_not_permitted': ('跳转到了未获准的域名', '没有访问目标；核对原链接和来源权限，不自动扩大域名范围。'),
    'redirect_credentials_blocked': ('跳转地址含疑似凭据参数，未保存', '没有转发或保存凭据；应使用平台原生浏览器流程。'),
    'redirect_unsafe_target': ('跳转目标不满足HTTPS/标准端口/无凭据要求', '未跟随；核对目标地址，不能关闭安全检查。'),
    'redirect_missing_location': ('服务器返回跳转状态但没有目标地址', '保存本次诊断，人工核对来源；不能猜测目标。'),
    'redirect_loop': ('检测到跳转循环', '已停止，不进行无限重试。'),
    'redirect_limit': ('达到每条职位最多3次跳转限制', '请人工确认最终详情链接或使用浏览器向导。'),
    'robots_redirect_cross_origin': ('robots规则跳转到另一来源，未自动采用', '需核验发布方规则，不把另一个站点的规则当成本源许可。'),
    'robots_denied': ('来源robots规则拒绝此自动访问路径', '停止自动获取；使用允许的接口或有权处理的手工正文。'),
    'robots_unavailable': ('无法确认来源的robots规则', '未继续自动请求正文；不是缺少搜索Key。'),
    'permission_required': ('没有本次正文访问许可', '确认实际许可后再创建任务，不由软件自动勾选。'),
    'host_stopped': ('同一站点已因先前拒绝而停止', '先处理前面条目的原因，不换任务反复请求。'),
    'non_public_address': ('DNS返回非公网地址', '先做网络检查；这不是账号或浏览器安装错误。'),
    'parse_error': ('取得页面但不能确认独立职位正文', '可能是动态页面或结构变化，可转浏览器或手工录入。'),
    'interrupted_uncertain': ('上次请求中断，结果不确定', '预算已计入，不自动重试；确认后新建任务。'),
}


def explain(state: dict) -> dict:
    details = []
    for original in state['details']:
        code = original['status']
        message, action = REASONS.get(code, ('本条状态：'+code, '展开采集诊断查看原因，不要只增加预算。'))
        details.append({**original, 'status_message': message, 'next_action': action})
    saved = sum(d['status'] in {'ok', 'fresh_reused'} for d in details)
    skipped = sum(d['status'] == 'budget_skipped' for d in details)
    text = f'本批 {len(details)} 条链接，已尝试 {state["detail_attempts"]} 条，取得或复用 {saved} 条正文，预算未执行 {skipped} 条。'
    if state['mode'] == 'urls':
        text += ' 搜索和数据源预算不参与URL路线；报告阶段不代表取得了正文。'
    return {'details': details, 'user_summary': text,
            'route_label': {'urls': '公开HTTP（无浏览器登录会话）', 'search': '搜索API＋公开HTTP',
                            'feed': '用户配置的授权JSON源'}.get(state['mode'], state['mode']),
            'saved_detail_count': saved, 'budget_skipped_count': skipped}
