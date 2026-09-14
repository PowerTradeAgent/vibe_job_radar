/* Optional manual capture helper. Read the script before executing it.
 * Select ONLY the authorized JD text in your browser, then run this script.
 * No network calls, no cookies/localStorage/session access, no login automation.
 * It saves the selected text and explicitly entered metadata as a local JSON file.
 */
(() => {
  const text = String(window.getSelection() || "").trim();
  if (text.length < 20) {
    alert("请先选中你有权处理的完整岗位职责和任职要求，不要选推荐岗位或招聘者联系方式。");
    return;
  }
  const title = prompt("请确认真实职位名称（必填）", document.querySelector("h1")?.textContent?.trim() || "");
  if (!title?.trim()) return;
  const company = prompt("招聘公司（不确定则留空）", "");
  if (company === null) return;
  const rights = prompt("请记录你有权处理这段文本的依据/允许范围（必填）", "");
  if (!rights?.trim()) return;
  const hosts = {"zhipin.com":"boss","liepin.com":"liepin","51job.com":"51job","zhaopin.com":"zhaopin","lagou.com":"lagou","maimai.cn":"maimai","nowcoder.com":"nowcoder","shixiseng.com":"shixiseng"};
  const match = Object.entries(hosts).find(([d]) => location.hostname === d || location.hostname.endsWith("." + d));
  const url = new URL(location.href); url.hash = "";
  const item = {title:title.trim(), company:company.trim(), text, url:url.toString(), platform:match?.[1] || "manual",
    evidence_level:"full_text",source_mode:"manual",is_synthetic:false,collected_at:new Date().toISOString(),
    rights_note:rights.trim(), source_ref:"user-selected JD text; completeness confirmed by user"};
  const blob = new Blob([JSON.stringify(item, null, 2)], {type:"application/json;charset=utf-8"});
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a"); a.href = href; a.download = "job_" + Date.now() + ".json";
  a.click(); setTimeout(() => URL.revokeObjectURL(href), 1000);
})();
