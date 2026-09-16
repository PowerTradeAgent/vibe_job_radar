"use strict";
const $ = (id) => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
const token = fragment.get("token") || sessionStorage.getItem("radar-session") || "";
if (fragment.has("token")) {
  sessionStorage.setItem("radar-session", token);
  history.replaceState(null, "", location.pathname);
}
let state = null;
function notice(value) { $("notice").textContent = value; }
async function request(path, data) {
  const options = {headers: {"X-Radar-Token": token}, cache: "no-store"};
  if (data !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(data);
  }
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}
async function operation(action) {
  const buttons = [...document.querySelectorAll("button")];
  buttons.forEach((button) => { button.disabled = true; });
  notice("正在处理本次操作；请勿重复提交。搜索发现可能需要等待外部服务响应。");
  try { await action(); }
  catch (error) { notice(error.message || "操作失败，请检查输入与本地环境。"); }
  finally { buttons.forEach((button) => { button.disabled = false; }); await publicState().catch(() => {}); }
}
function table(target, headers, rows) {
  const element = document.createElement("table");
  const head = document.createElement("thead");
  const heading = document.createElement("tr");
  headers.forEach((label) => { const th = document.createElement("th"); th.textContent = label; heading.append(th); });
  head.append(heading); element.append(head);
  const body = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    row.forEach((value, index) => { const td = document.createElement("td"); td.textContent = String(value ?? "");
      if (headers[index] === "原文") td.className = "quote";
      if (headers[index] === "来源链接" && typeof value === "string" && value.startsWith("https://")) {
        const link = document.createElement("a"); link.href = value; link.target = "_blank";
        link.rel = "noopener noreferrer"; link.textContent = "打开来源"; td.replaceChildren(link);
      }
      tr.append(td); });
    body.append(tr);
  });
  element.append(body); target.replaceChildren(element);
}
function choices(id, values, defaults) {
  if ($(id).childElementCount) return;
  Object.entries(values).forEach(([key, value]) => {
    const label = document.createElement("label"); const input = document.createElement("input");
    input.type = "checkbox"; input.value = key; input.checked = defaults.includes(key);
    label.append(input, typeof value === "string" ? value : value.label); $(id).append(label);
  });
}
function selected(id) { return [...$(id).querySelectorAll("input:checked")].map((input) => input.value); }
function filters() { return {roles: selected("roles"), platforms: selected("platforms")}; }
async function refresh() {
  state = await request("/api/status");
  $("workspace").textContent = `本机数据目录：${state.workspace}`;
  $("counts").textContent = `真实记录 ${state.counts.records} · 完整正文 ${state.counts.full_text} · 摘要线索 ${state.counts.snippet}`;
  $("key-status").textContent = state.brave_key_configured ? "已检测到环境变量中的 Brave Key，但未验证联网和额度。" : "未配置 Brave Key：不影响粘贴、导入、演示与离线分析。";
  choices("roles", state.roles, Object.keys(state.roles));
  choices("platforms", state.platforms, ["boss", "liepin", "51job"]);
  if (!$("job-platform").childElementCount) {
    Object.entries({...state.platforms, manual: {label: "其他 / 手工来源"}}).forEach(([key, value]) => {
      const option = document.createElement("option"); option.value = key; option.textContent = value.label;
      $("job-platform").append(option);
    });
  }
  table($("sources"), ["平台", "授权文本导入", "自动登录", "实站验收"], Object.values(state.platforms).map((p) => [p.label, "可用", "未实现", "未验证"]));
  table($("records"), ["职位", "平台", "证据", "匹配岗位", "来源链接"], state.records.map((j) => [j.title, state.platforms[j.platform]?.label || j.platform,
    j.evidence_level === "full_text" ? "完整正文" : "摘要线索", j.roles.map((r) => state.roles[r]).join("、") || "未匹配", j.url]));
  $("runs").replaceChildren();
  state.runs.forEach((run) => {
    const button = document.createElement("button"); button.type = "button"; button.className = "secondary";
    button.textContent = `${run.mode === "synthetic_demo" ? "合成演示" : "真实样本"} · ${run.created_at} · ${run.stats.requirement_rows} 行`;
    button.addEventListener("click", () => operation(async () => showReport(await request(`/api/report/${run.id}`))));
    $("runs").append(button);
  });
  if (!state.runs.length) $("runs").textContent = "还没有报告。可以先运行演示。";
}
async function download(runId, name) {
  const response = await fetch(`/api/download/${runId}/${encodeURIComponent(name)}`, {headers: {"X-Radar-Token": token}, cache: "no-store"});
  if (!response.ok) { const result = await response.json(); throw new Error(result.error || "下载失败"); }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a"); link.href = url; link.download = name;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
  notice(`已请求下载 ${name}；文件位于浏览器下载目录。`);
}
function showReport(report) {
  $("report").hidden = false;
  $("report-title").textContent = `${report.manifest.mode === "synthetic_demo" ? "合成演示（不是市场事实）" : "真实输入样本（不是全市场）"} · ${report.id.slice(0, 8)}`;
  $("report-message").textContent = report.message;
  const s = report.manifest.stats;
  $("report-stats").textContent = `正文岗位组 ${s.full_text_job_groups} · 要求行 ${s.requirement_rows} · 已接受正向要求 ${s.accepted_positive_requirement_rows} · 待复核 ${s.review_queue_rows}`;
  $("descriptions").textContent = report.descriptions;
  $("manifest").textContent = JSON.stringify(report.manifest, null, 2);
  table($("requirements"), ["岗位", "能力", "强度", "原文", "复核状态"], (report.requirements || []).map((r) => [r.title, r.capability, r.strength, r.quote, r.review_status]));
  $("files").replaceChildren();
  const preferred = ["requirements_zh.csv", "descriptions.md", "role_descriptions.md", "evidence_gaps.md", "dashboard.html"];
  [...report.files].sort((a, b) => (preferred.includes(a) ? preferred.indexOf(a) : 100) - (preferred.includes(b) ? preferred.indexOf(b) : 100)).forEach((name) => {
    const button = document.createElement("button"); button.type = "button"; button.className = "secondary"; button.textContent = name;
    button.addEventListener("click", () => operation(() => download(report.id, name))); $("files").append(button);
  });
  notice(report.message);
}
$("job-form").addEventListener("submit", (event) => {
  event.preventDefault(); const form = event.currentTarget;
  operation(async () => {
    const data = Object.fromEntries(new FormData(form)); data.full_text_confirmed = form.elements.full_text_confirmed.checked;
    const result = await request("/api/job", data); await refresh(); notice(result.message);
    form.elements.text.value = ""; form.elements.full_text_confirmed.checked = false;
  });
});
$("import-form").addEventListener("submit", (event) => {
  event.preventDefault(); const form = event.currentTarget;
  operation(async () => {
    const file = $("import-file").files[0];
    if (!file || file.size > 1000000) throw new Error("请选择不超过 1 MB 的 UTF-8 文件。");
    const decoder = new TextDecoder("utf-8", {fatal: true});
    const result = await request("/api/import", {filename: file.name, content: decoder.decode(await file.arrayBuffer()),
      rights_note: form.elements.rights_note.value, full_text_confirmed: form.elements.full_text_confirmed.checked});
    await refresh(); notice(`${result.message} 新快照 ${result.new_snapshots} / 观察 ${result.observations}`);
  });
});
$("search-form").addEventListener("submit", (event) => {
  event.preventDefault(); const form = event.currentTarget;
  operation(async () => {
    try {
      const result = await request("/api/discover", {...filters(), api_key: form.elements.api_key.value,
        max_requests: Number(form.elements.max_requests.value), consent_search: form.elements.consent_search.checked});
      await refresh(); notice(`${result.message}\n请求 ${result.requests_made} / 线索 ${result.leads_stored}\n状态 ${JSON.stringify(result.task_statuses)}\n${result.errors.join("\n")}`);
    } finally { form.elements.api_key.value = ""; }
  });
});
$("plan").addEventListener("click", () => operation(async () => {
  const plan = await request("/api/plan", filters()); $("plan-output").hidden = false;
  $("plan-output").textContent = plan.tasks.map((task) => task.query).join("\n"); notice(`已生成 ${plan.query_count} 条计划，未发出任何搜索请求。`);
}));
$("doctor").addEventListener("click", () => operation(async () => notice(JSON.stringify(await request("/api/doctor"), null, 2))));
$("refresh").addEventListener("click", () => operation(async () => { await refresh(); notice("本地数据已刷新。"); }));
$("demo").addEventListener("click", () => operation(async () => {
  const report = await request("/api/analyze", {dataset: "demo"}); await refresh(); showReport(report);
}));
$("analyze").addEventListener("click", () => operation(async () => {
  const report = await request("/api/analyze", {...filters(), dataset: "real"}); await refresh(); showReport(report);
}));
if (!token) notice("缺少本地会话令牌，请打开终端中带 #token 的完整地址。");
else refresh().catch((error) => notice(error.message));


// Public tasks poll LOCAL state only. Loading this page never queries a source.
let publicPolling = false, publicReport = '';
async function publicState() {
  const result = await request('/api/public/state');
  const task = result.task;
  $('public-status').textContent = task.message || '';
  $('public-service-status').textContent = result.hybrid_service_configured
    ? '使用已配置公开服务；仅发送确认的查询字段。'
    : '混合服务核心已接入，但产品侧服务尚未配置上线；公开案例和本地采集仍可使用。';
  $('public-network').textContent = JSON.stringify(result.network_policy, null, 2);
  const busy = ['queued', 'running'].includes(task.status);
  $('public-example').disabled = busy;
  $('public-search-button').disabled = busy || !result.hybrid_service_configured;
  if (!$('public-source').options.length) for (const source of result.sources) {
    const option = document.createElement('option'); option.value = source.id; option.textContent = source.label;
    $('public-source').append(option);
  }
  return task;
}
async function watchPublic() {
  if (publicPolling) return;
  publicPolling = true;
  try {
    for (let i = 0; i < 120; i++) {
      const task = await publicState();
      if (!['queued', 'running'].includes(task.status)) {
        if (task.status === 'completed' && task.report_id && task.report_id !== publicReport) {
          publicReport = task.report_id;
          await refresh(); showReport(await request('/api/report/' + task.report_id));
        }
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    $('public-status').textContent = '任务仍在后台处理，已保存进度；刷新页面查看，不必重复提交。';
  } catch (error) { $('public-status').textContent = error.message; }
  finally { publicPolling = false; }
}
$('public-example').addEventListener('click', async () => {
  if (!confirm('仅请求官方公开岗位接口，按来源限制获取并在本机生成报告。不上传简历或登录态。是否继续？')) return;
  await operation(async () => { await request('/api/public/start', {consent: true}); });
  await watchPublic();
});
$('public-search').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget;
  await operation(async () => { await request('/api/public/search', {
    consent: form.elements.consent.checked,
    query: {query: form.elements.query.value, region: form.elements.region.value,
            source_scope: [form.elements.source.value], limit: 20}
  }); });
  await watchPublic();
});
if (token) watchPublic();
