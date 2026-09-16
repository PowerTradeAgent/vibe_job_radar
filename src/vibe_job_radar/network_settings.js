/* Shared, same-origin opt-in control. Never accepts a resolver URL or token. */
(() => {
  'use strict';
  const panel = document.getElementById('network-preferences');
  if (!panel) return;
  const toggle = panel.querySelector('input');
  const button = panel.querySelector('button');
  const message = panel.querySelector('[role=status]');
  let revision = null;
  async function call(path, data) {
    const token = sessionStorage.getItem('radar-session') || '';
    const response = await fetch(path, {method: data ? 'POST' : 'GET', cache: 'no-store',
      headers: {'X-Radar-Token': token, ...(data ? {'Content-Type': 'application/json'} : {})},
      body: data ? JSON.stringify(data) : undefined});
    const value = await response.json();
    if (!response.ok) throw Error(value.error || '无法保存网络偏好');
    return value;
  }
  async function refresh() {
    const value = await call('/api/network/state');
    toggle.checked = value.mode === 'fake_ip_doh';
    revision = value.revision;
    message.textContent = toggle.checked ? '已同意仅在映射地址时采用加密解析。尚未进行网络测试。' : '未开启额外解析；普通公网DNS不受影响。';
    button.disabled = false;
  }
  button.addEventListener('click', async () => {
    if (revision === null) return;
    button.disabled = true;
    try {
      const value = await call('/api/network/preferences', {mode: toggle.checked ? 'fake_ip_doh' : 'system',
        consent: toggle.checked, revision});
      revision = value.revision;
      message.textContent = value.message;
    } catch (error) { message.textContent = error.message; }
    finally { button.disabled = false; }
  });
  refresh().catch(error => { message.textContent = error.message; });
})();
