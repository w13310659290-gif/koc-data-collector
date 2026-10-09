const form = document.querySelector('#record-form');
const metrics = [['likes','点赞'],['comments','评论'],['favorites','收藏'],['shares','转发']];
const platforms = {douyin:'抖音',xiaohongshu:'小红书'};
let records = [], editing = null;
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function notify(message) { document.querySelector('#status').textContent = message; }
async function request(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '操作失败，请重试');
  return data;
}
function render() {
  document.querySelector('#summary').innerHTML = [['作品记录',records.length],['抖音',records.filter(r=>r.platform==='douyin').length],['小红书',records.filter(r=>r.platform==='xiaohongshu').length]].map(([label,n])=>`<div class="stat"><span>${label}</span><strong>${n}</strong></div>`).join('');
  const search = document.querySelector('#search').value.trim().toLowerCase();
  const platform = document.querySelector('#platform-filter').value;
  const visible = records.filter(r=>(!platform || r.platform===platform) && [r.title,r.author,r.url,r.notes].join(' ').toLowerCase().includes(search));
  document.querySelector('#count').textContent = `${visible.length} 条`;
  document.querySelector('#records').innerHTML = visible.length ? visible.map(r=>`<article class="entry"><div class="entry-top"><span class="pill">${platforms[r.platform]}</span><h3>${escapeHTML(r.title || '未填写标题')}</h3></div><p class="meta">${escapeHTML(r.author || '未填写作者')} · ${escapeHTML(new Date(r.recorded_at).toLocaleString())}</p><a href="${escapeHTML(r.url)}" target="_blank" rel="noopener noreferrer">打开原作品 ↗</a><div class="numbers">${metrics.map(([key,label])=>`<div><span>${label}</span><strong>${r[key]===null?'—':r[key].toLocaleString()}</strong></div>`).join('')}</div>${r.notes?`<p class="note">${escapeHTML(r.notes)}</p>`:''}<div class="entry-actions"><button class="secondary" data-edit="${r.id}">编辑</button><button class="delete" data-delete="${r.id}">删除</button></div></article>`).join('') : '<div class="empty">暂无记录<br>在左侧添加第一条作品数据。</div>';
}
async function load() { records = await request('/api/records'); render(); }
function reset() { editing = null; form.reset(); document.querySelector('#form-title').textContent='添加作品'; document.querySelector('#cancel').hidden=true; }
form.addEventListener('submit',async event=>{
  event.preventDefault();
  const values = Object.fromEntries(new FormData(form));
  for (const [key] of metrics) {
    if (values[key]==='') values[key]=null;
    else { values[key]=Number(values[key]); if (!Number.isSafeInteger(values[key]) || values[key]<0) return notify('数量必须为非负整数'); }
  }
  const button = document.querySelector('#save'); button.disabled = true;
  try {
    await request(editing===null?'/api/records':`/api/records/${editing}`,{method:editing===null?'POST':'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});
    reset(); notify('记录已保存'); await load();
  } catch(error) { notify(error.message); } finally { button.disabled=false; }
});
document.querySelector('#cancel').addEventListener('click',reset);
document.querySelector('#search').addEventListener('input',render);
document.querySelector('#platform-filter').addEventListener('change',render);
document.querySelector('#records').addEventListener('click',async event=>{
  const edit = event.target.closest('[data-edit]');
  if (edit) {
    const record = records.find(r=>r.id===Number(edit.dataset.edit)); editing=record.id;
    for (const key of ['platform','url','title','author','notes',...metrics.map(([key])=>key)]) form.elements.namedItem(key).value=record[key] ?? '';
    document.querySelector('#form-title').textContent='编辑作品'; document.querySelector('#cancel').hidden=false;
    form.scrollIntoView({behavior:'smooth',block:'start'}); return;
  }
  const remove = event.target.closest('[data-delete]');
  if (remove && confirm('确定删除这条记录？此操作无法撤销。')) {
    try { await request(`/api/records/${remove.dataset.delete}`,{method:'DELETE'}); if (editing===Number(remove.dataset.delete)) reset(); await load(); notify('记录已删除'); }
    catch(error) { notify(error.message); }
  }
});
load().catch(error=>notify('无法读取记录：'+error.message));
