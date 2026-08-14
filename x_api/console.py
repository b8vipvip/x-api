from __future__ import annotations

import html
import json
from typing import Callable

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from .store import ProviderStore


_PAGE = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>X-API 供应商管理</title><style>
:root{font-family:Inter,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;color:#172033;background:#f5f7fb}*{box-sizing:border-box}body{margin:0}.shell{max-width:1280px;margin:auto;padding:28px}.head{display:flex;align-items:center;gap:12px;margin-bottom:20px}.head div{flex:1}.head h1{margin:0 0 5px;font-size:24px}.muted{color:#667085;font-size:13px}.btn{border:1px solid #cfd7e5;background:#fff;border-radius:9px;padding:8px 12px;cursor:pointer}.btn.primary{background:#1677ff;color:#fff;border-color:#1677ff}.btn.danger{color:#c62839}.card{background:#fff;border:1px solid #e3e8ef;border-radius:14px;padding:18px;margin-bottom:18px;box-shadow:0 8px 24px rgba(15,23,42,.05)}table{width:100%;border-collapse:collapse;min-width:950px}th,td{text-align:left;padding:10px;border-bottom:1px solid #edf0f4;vertical-align:top;font-size:12px}th{color:#667085;background:#f8fafc}.scroll{overflow:auto}.pill{display:inline-block;padding:3px 7px;border-radius:99px;background:#edf3ff;color:#0b5cc4;margin:1px 3px 1px 0}.ok{background:#e9faf3;color:#08714c}.bad{background:#fff0f1;color:#b42332}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.full{grid-column:1/-1}label{display:flex;flex-direction:column;gap:6px;font-size:12px;font-weight:650}input,select{border:1px solid #ccd5e1;border-radius:8px;padding:9px;font:inherit}.checks{display:flex;gap:14px;flex-wrap:wrap}.checks label{display:flex;flex-direction:row;align-items:center;font-weight:400}.actions{display:flex;gap:6px;flex-wrap:wrap}.toast{display:none;padding:10px 12px;border-radius:9px;margin-bottom:12px;background:#eef5ff}.toast.show{display:block}@media(max-width:700px){.shell{padding:15px}.grid{grid-template-columns:1fr}.full{grid-column:auto}}
</style></head><body><main class="shell">
<div class="head"><div><h1>X-API 供应商管理</h1><div class="muted">多供应商 · 主备模型 · 加密密钥 · 协议探测</div></div><button class="btn primary" onclick="resetEditor()">新增供应商</button></div>
<div id="toast" class="toast"></div>
<section class="card"><div class="scroll"><table><thead><tr><th>优先级</th><th>供应商</th><th>主模型</th><th>备用模型</th><th>协议</th><th>状态</th><th>操作</th></tr></thead><tbody id="rows"></tbody></table></div></section>
<section class="card" id="editor"><h2 id="editorTitle">新增供应商</h2><div class="grid">
<label>名称<input id="name"></label><label>优先级<input id="priority" type="number" value="100"></label>
<label class="full">Base URL<input id="base_url" placeholder="https://example.com/v1"></label>
<label>API Key<input id="api_key" type="password" placeholder="留空保持当前密钥"></label><label>超时（秒）<input id="timeout_seconds" type="number" value="60"></label>
<label>主文本模型<input id="main_text_model"></label><label>备用文本模型<input id="backup_text_models" placeholder="model-a, model-b"></label>
<label>主视觉模型<input id="main_vision_model"></label><label>备用视觉模型<input id="backup_vision_models"></label>
<div class="full checks"><label><input id="enabled" type="checkbox" checked>启用</label><label><input id="p_chat" type="checkbox" checked>Chat</label><label><input id="p_responses" type="checkbox" checked>Responses</label><label><input id="p_legacy" type="checkbox" checked>Legacy</label><label><input id="auto_test_enabled" type="checkbox">自动深测</label></div>
<label>自动深测周期（小时）<input id="auto_test_interval_hours" type="number" value="12"></label><label><span>密钥操作</span><span class="checks"><label><input id="clear_api_key" type="checkbox">清除现有 Key</label></span></label>
</div><div style="margin-top:15px"><button class="btn primary" onclick="save()">保存</button></div></section>
</main><script>
const API=__API__;let editing=0;let providers=[];
const $=id=>document.getElementById(id);const split=v=>[...new Set((v||'').split(/[,，;；\n]+/).map(x=>x.trim()).filter(Boolean))];
function toast(msg){$('toast').textContent=msg;$('toast').classList.add('show');setTimeout(()=>$('toast').classList.remove('show'),5000)}
async function request(url,opt={}){const r=await fetch(url,{headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});const text=await r.text();let data={};try{data=text?JSON.parse(text):{}}catch{data={detail:text}}if(!r.ok)throw new Error(data.detail||text||`HTTP ${r.status}`);return data}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function load(){providers=await request(API);$('rows').innerHTML=providers.map(p=>`<tr><td>#${p.priority}<br><span class="pill ${p.enabled?'ok':''}">${p.enabled?'启用':'停用'}</span></td><td><b>${esc(p.name)}</b><br><span class="muted">${esc(p.base_url)}</span><br><code>${esc(p.api_key_masked||'未配置')}</code></td><td>${esc(p.main_text_model||'—')}</td><td>${(p.backup_text_models||[]).map(x=>`<span class="pill">${esc(x)}</span>`).join('')||'—'}</td><td>${(p.protocol_order||[]).map(x=>`<span class="pill">${esc(x)}</span>`).join('')}</td><td><span class="pill ${String(p.last_status).startsWith('可用')?'ok':String(p.last_status).startsWith('失败')?'bad':''}">${esc(p.last_status)}</span><br><span class="muted">${p.last_latency_ms||0} ms</span></td><td><div class="actions"><button class="btn" onclick="edit(${p.id})">编辑</button><button class="btn" onclick="probe(${p.id},'ordinary')">普通测试</button><button class="btn" onclick="probe(${p.id},'deep')">深测</button><button class="btn" onclick="discover(${p.id})">模型</button><button class="btn danger" onclick="removeProvider(${p.id})">删除</button></div></td></tr>`).join('')||'<tr><td colspan="7" class="muted">暂无供应商</td></tr>'}
function resetEditor(){editing=0;$('editorTitle').textContent='新增供应商';for(const id of ['name','base_url','api_key','main_text_model','backup_text_models','main_vision_model','backup_vision_models'])$(id).value='';$('priority').value=100;$('timeout_seconds').value=60;$('auto_test_interval_hours').value=12;$('enabled').checked=true;$('p_chat').checked=$('p_responses').checked=$('p_legacy').checked=true;$('auto_test_enabled').checked=$('clear_api_key').checked=false;$('editor').scrollIntoView({behavior:'smooth'})}
function edit(id){const p=providers.find(x=>x.id===id);if(!p)return;editing=id;$('editorTitle').textContent=`编辑：${p.name}`;for(const [id,key] of [['name','name'],['base_url','base_url'],['main_text_model','main_text_model'],['main_vision_model','main_vision_model']])$(id).value=p[key]||'';$('api_key').value='';$('backup_text_models').value=(p.backup_text_models||[]).join(', ');$('backup_vision_models').value=(p.backup_vision_models||[]).join(', ');$('priority').value=p.priority;$('timeout_seconds').value=p.timeout_seconds;$('auto_test_interval_hours').value=p.auto_test_interval_hours;$('enabled').checked=p.enabled;$('p_chat').checked=(p.protocol_order||[]).includes('chat');$('p_responses').checked=(p.protocol_order||[]).includes('responses');$('p_legacy').checked=(p.protocol_order||[]).includes('legacy');$('auto_test_enabled').checked=p.auto_test_enabled;$('clear_api_key').checked=false;$('editor').scrollIntoView({behavior:'smooth'})}
async function save(){const body={name:$('name').value,base_url:$('base_url').value,api_key:$('api_key').value,enabled:$('enabled').checked,priority:+$('priority').value||100,main_text_model:$('main_text_model').value,backup_text_models:split($('backup_text_models').value),main_vision_model:$('main_vision_model').value,backup_vision_models:split($('backup_vision_models').value),protocol_order:[['chat','p_chat'],['responses','p_responses'],['legacy','p_legacy']].filter(x=>$(x[1]).checked).map(x=>x[0]),timeout_seconds:+$('timeout_seconds').value||60,auto_test_enabled:$('auto_test_enabled').checked,auto_test_interval_hours:+$('auto_test_interval_hours').value||12,clear_api_key:$('clear_api_key').checked};try{await request(editing?`${API}/${editing}`:API,{method:editing?'PUT':'POST',body:JSON.stringify(body)});toast('已保存');resetEditor();await load()}catch(e){toast(e.message)}}
async function probe(id,mode){try{toast(`${mode==='deep'?'深度':'普通'}测试中…`);const r=await request(`${API}/${id}/probe`,{method:'POST',body:JSON.stringify({mode,auto_apply:true})});toast(r.ok?`测试通过：${(r.usable_models||[]).length} 个可用模型`:`测试失败：${r.error||'无可用模型'}`);await load()}catch(e){toast(e.message)}}
async function discover(id){try{const r=await request(`${API}/${id}/discover`,{method:'POST'});toast(r.ok?`发现 ${(r.models||[]).length} 个模型：${(r.models||[]).slice(0,6).join(', ')}`:'模型发现失败')}catch(e){toast(e.message)}}
async function removeProvider(id){if(!confirm('确定删除此供应商？'))return;try{await request(`${API}/${id}`,{method:'DELETE'});toast('已删除');if(editing===id)resetEditor();await load()}catch(e){toast(e.message)}}
load();
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
