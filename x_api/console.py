from __future__ import annotations

import json
from typing import Callable

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from .store import ProviderStore


_PAGE = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>X-API 供应商管理</title><style>
:root{font-family:Inter,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;color:#172033;background:#f5f7fb}*{box-sizing:border-box}body{margin:0}.shell{max-width:1440px;margin:auto;padding:28px}.head{display:flex;align-items:center;gap:12px;margin-bottom:20px}.head div{flex:1}.head h1{margin:0 0 5px;font-size:24px}.muted{color:#667085;font-size:13px}.btn{border:1px solid #cfd7e5;background:#fff;border-radius:9px;padding:8px 12px;cursor:pointer}.btn.primary{background:#1677ff;color:#fff;border-color:#1677ff}.btn.danger{color:#c62839}.card{background:#fff;border:1px solid #e3e8ef;border-radius:14px;padding:18px;margin-bottom:18px;box-shadow:0 8px 24px rgba(15,23,42,.05)}table{width:100%;border-collapse:collapse;min-width:1200px}th,td{text-align:left;padding:10px;border-bottom:1px solid #edf0f4;vertical-align:top;font-size:12px}th{color:#667085;background:#f8fafc}.scroll{overflow:auto}.pill{display:inline-block;padding:3px 7px;border-radius:99px;background:#edf3ff;color:#0b5cc4;margin:1px 3px 1px 0}.pill.ok{background:#e9faf3;color:#08714c}.pill.image{background:#fff4e5;color:#9a5b00}.pill.audio{background:#fdf2fa;color:#a12472}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.grid.three{grid-template-columns:repeat(3,minmax(0,1fr))}.full{grid-column:1/-1}label{display:flex;flex-direction:column;gap:6px;font-size:12px;font-weight:650}input,select{border:1px solid #ccd5e1;border-radius:8px;padding:9px;font:inherit}.checks{display:flex;gap:14px;flex-wrap:wrap}.checks label{display:flex;flex-direction:row;align-items:center;font-weight:400}.actions{display:flex;gap:6px;flex-wrap:wrap}.toast{display:none;padding:10px 12px;border-radius:9px;margin-bottom:12px;background:#eef5ff}.toast.show{display:block}.group{border:1px solid #e3e8ef;border-radius:12px;padding:14px;margin-top:14px}.group h3{margin:0 0 5px}.group p{margin:0 0 12px}.cap{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.tiny{font-size:11px;color:#667085;margin-top:4px}@media(max-width:760px){.shell{padding:15px}.grid,.grid.three,.cap{grid-template-columns:1fr}.full{grid-column:auto}}
</style></head><body><main class="shell">
<div class="head"><div><h1>X-API 供应商管理</h1><div class="muted">文本/视觉共享 · 图片生成 · 语音模型 · 加密密钥 · 主备容错</div></div><button class="btn primary" onclick="resetEditor()">新增供应商</button></div>
<div id="toast" class="toast"></div>
<section class="card"><div class="scroll"><table><thead><tr><th>优先级</th><th>供应商</th><th>文本/视觉</th><th>图片生成</th><th>语音</th><th>状态</th><th>操作</th></tr></thead><tbody id="rows"></tbody></table></div></section>
<section class="card" id="editor"><h2 id="editorTitle">新增供应商</h2>
<div class="grid"><label>名称<input id="name"></label><label>优先级<input id="priority" type="number" value="100"></label><label class="full">Base URL<input id="base_url" placeholder="https://example.com/v1"></label><label>API Key<input id="api_key" type="password" placeholder="编辑时留空保持当前密钥"></label><label>超时（秒）<input id="timeout_seconds" type="number" value="60"></label></div>
<div class="group"><h3>文本 + 视觉理解</h3><p class="muted">视觉覆盖模型留空时直接共用文本模型池。</p><div class="grid"><label>主文本模型<input id="main_text_model"></label><label>备用文本模型<input id="backup_text_models" placeholder="model-a, model-b"></label><label>主视觉覆盖模型<input id="main_vision_model" placeholder="留空=共用文本"></label><label>视觉备用覆盖模型<input id="backup_vision_models"></label></div></div>
<div class="cap">
<div class="group"><h3>图片生成</h3><p class="muted">用于自动切换到图片生成能力。</p><label>主图片模型<input id="main_image_model"></label><label>图片备用模型<input id="backup_image_models"></label></div>
<div class="group"><h3>语音</h3><p class="muted">chat_audio=语音对话；speech=文本回答后 TTS。</p><label>主语音模型<input id="main_audio_model"></label><label>语音备用模型<input id="backup_audio_models"></label><div class="grid three"><label>方式<select id="audio_protocol"><option>auto</option><option>chat_audio</option><option>speech</option></select></label><label>Voice<input id="audio_voice" value="alloy"></label><label>格式<select id="audio_format"><option>wav</option><option>mp3</option><option>opus</option><option>aac</option><option>flac</option><option>pcm</option></select></label></div></div>
</div>
<div class="group"><div class="checks"><label><input id="enabled" type="checkbox" checked>启用</label><label><input id="p_chat" type="checkbox" checked>Chat</label><label><input id="p_responses" type="checkbox" checked>Responses</label><label><input id="p_legacy" type="checkbox" checked>Legacy</label><label><input id="auto_test_enabled" type="checkbox">自动文本深测</label><label><input id="clear_api_key" type="checkbox">清除现有 Key</label></div><div class="grid" style="margin-top:12px"><label>自动深测周期（小时）<input id="auto_test_interval_hours" type="number" value="12"></label></div><div class="tiny">自动深测只验证文本模型，避免周期性产生图片/音频费用。</div></div>
<div style="margin-top:15px"><button class="btn primary" onclick="save()">保存</button></div></section>
</main><script>
const API=__API__;let editing=0;let providers=[];const $=id=>document.getElementById(id);const split=v=>[...new Set((v||'').split(/[,，;；\n]+/).map(x=>x.trim()).filter(Boolean))];
function toast(msg){$('toast').textContent=msg;$('toast').classList.add('show');setTimeout(()=>$('toast').classList.remove('show'),5000)}
async function request(url,opt={}){const r=await fetch(url,{headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});const text=await r.text();let data={};try{data=text?JSON.parse(text):{}}catch{data={detail:text}}if(!r.ok)throw new Error(data.detail||text||`HTTP ${r.status}`);return data}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function list(v,klass=''){return (v||[]).map(x=>`<span class="pill ${klass}">${esc(x)}</span>`).join('')}
async function load(){providers=await request(API);$('rows').innerHTML=providers.map(p=>`<tr><td>#${p.priority}<br><span class="pill ${p.enabled?'ok':''}">${p.enabled?'启用':'停用'}</span></td><td><b>${esc(p.name)}</b><br><span class="muted">${esc(p.base_url)}</span><br><code>${esc(p.api_key_masked||'未配置')}</code></td><td><span class="pill">${esc(p.main_text_model||'未设置')}</span>${p.main_vision_model?`<br><span class="pill">视觉 ${esc(p.main_vision_model)}</span>`:'<br><span class="pill ok">视觉共用文本</span>'}<div class="tiny">${list(p.backup_text_models)}</div></td><td>${p.main_image_model?`<span class="pill image">${esc(p.main_image_model)}</span>`:'—'}<div class="tiny">${list(p.backup_image_models,'image')}</div></td><td>${p.main_audio_model?`<span class="pill audio">${esc(p.main_audio_model)}</span><div class="tiny">${esc(p.audio_protocol)} · ${esc(p.audio_voice)} · ${esc(p.audio_format)}</div>`:'—'}<div>${list(p.backup_audio_models,'audio')}</div></td><td><span class="pill ${String(p.last_status).startsWith('可用')?'ok':''}">${esc(p.last_status)}</span><br><span class="muted">${p.last_latency_ms||0} ms</span></td><td><div class="actions"><button class="btn" onclick="edit(${p.id})">编辑</button><button class="btn" onclick="probe(${p.id},'ordinary')">文本测试</button><button class="btn" onclick="probe(${p.id},'deep')">文本深测</button><button class="btn" onclick="discover(${p.id})">模型</button><button class="btn danger" onclick="removeProvider(${p.id})">删除</button></div></td></tr>`).join('')||'<tr><td colspan="7" class="muted">暂无供应商</td></tr>'}
const textFields=['name','base_url','api_key','main_text_model','backup_text_models','main_vision_model','backup_vision_models','main_image_model','backup_image_models','main_audio_model','backup_audio_models'];
function resetEditor(){editing=0;$('editorTitle').textContent='新增供应商';for(const id of textFields)$(id).value='';$('priority').value=100;$('timeout_seconds').value=60;$('auto_test_interval_hours').value=12;$('audio_protocol').value='auto';$('audio_voice').value='alloy';$('audio_format').value='wav';$('enabled').checked=true;$('p_chat').checked=$('p_responses').checked=$('p_legacy').checked=true;$('auto_test_enabled').checked=$('clear_api_key').checked=false;$('editor').scrollIntoView({behavior:'smooth'})}
function edit(id){const p=providers.find(x=>x.id===id);if(!p)return;editing=id;$('editorTitle').textContent=`编辑：${p.name}`;for(const id of ['name','base_url','main_text_model','main_vision_model','main_image_model','main_audio_model'])$(id).value=p[id]||'';$('api_key').value='';for(const id of ['backup_text_models','backup_vision_models','backup_image_models','backup_audio_models'])$(id).value=(p[id]||[]).join(', ');$('priority').value=p.priority;$('timeout_seconds').value=p.timeout_seconds;$('auto_test_interval_hours').value=p.auto_test_interval_hours;$('audio_protocol').value=p.audio_protocol||'auto';$('audio_voice').value=p.audio_voice||'alloy';$('audio_format').value=p.audio_format||'wav';$('enabled').checked=p.enabled;$('p_chat').checked=(p.protocol_order||[]).includes('chat');$('p_responses').checked=(p.protocol_order||[]).includes('responses');$('p_legacy').checked=(p.protocol_order||[]).includes('legacy');$('auto_test_enabled').checked=p.auto_test_enabled;$('clear_api_key').checked=false;$('editor').scrollIntoView({behavior:'smooth'})}
async function save(){const body={name:$('name').value,base_url:$('base_url').value,api_key:$('api_key').value,enabled:$('enabled').checked,priority:+$('priority').value||100,main_text_model:$('main_text_model').value,backup_text_models:split($('backup_text_models').value),main_vision_model:$('main_vision_model').value,backup_vision_models:split($('backup_vision_models').value),main_image_model:$('main_image_model').value,backup_image_models:split($('backup_image_models').value),main_audio_model:$('main_audio_model').value,backup_audio_models:split($('backup_audio_models').value),audio_protocol:$('audio_protocol').value,audio_voice:$('audio_voice').value,audio_format:$('audio_format').value,protocol_order:[['chat','p_chat'],['responses','p_responses'],['legacy','p_legacy']].filter(x=>$(x[1]).checked).map(x=>x[0]),timeout_seconds:+$('timeout_seconds').value||60,auto_test_enabled:$('auto_test_enabled').checked,auto_test_interval_hours:+$('auto_test_interval_hours').value||12,clear_api_key:$('clear_api_key').checked};try{await request(editing?`${API}/${editing}`:API,{method:editing?'PUT':'POST',body:JSON.stringify(body)});toast('已保存');resetEditor();await load()}catch(e){toast(e.message)}}
async function probe(id,mode){try{toast(`${mode==='deep'?'深度':'普通'}文本测试中…`);const r=await request(`${API}/${id}/probe`,{method:'POST',body:JSON.stringify({mode,auto_apply:true})});toast(r.ok?`文本测试通过：${(r.usable_models||[]).length} 个可用模型`:`测试失败：${r.error||'无可用模型'}`);await load()}catch(e){toast(e.message)}}
async function discover(id){try{const r=await request(`${API}/${id}/discover`,{method:'POST'});toast(r.ok?`发现 ${(r.models||[]).length} 个模型：${(r.models||[]).slice(0,6).join(', ')}`:'模型发现失败')}catch(e){toast(e.message)}}
async function removeProvider(id){if(!confirm('确定删除此供应商？'))return;try{await request(`${API}/${id}`,{method:'DELETE'});toast('已删除');if(editing===id)resetEditor();await load()}catch(e){toast(e.message)}}load();
</script></body></html>'''


def create_console_router(
    store: ProviderStore,
    *,
    require_admin: Callable[..., object] | None = None,
    api_prefix: str = "/api/admin/ai-providers",
    page_path: str = "/admin/ai-providers",
) -> APIRouter:
    dependencies = [Depends(require_admin)] if require_admin is not None else []
    router = APIRouter(dependencies=dependencies)

    @router.get(page_path, response_class=HTMLResponse, include_in_schema=False)
    def console() -> HTMLResponse:
        page = _PAGE.replace("__API__", json.dumps(api_prefix))
        return HTMLResponse(page)

    return router
