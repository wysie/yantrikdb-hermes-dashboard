const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const state = { health:null, stats:null, settings:null, defaultNamespace:'', selectedNamespace:'__all__', view:'overview', memoryOffset:0, selectedMemory:null, recentWrites:[], identityScope:null, actorIdentityFilter:'__all__' };
const VALID_VIEWS = new Set(['overview','visualiser','memories','recall','conflicts','graph','identity-scope','lifecycle','ops','settings']);
const ALL_NAMESPACES = '__all__';
const DEFAULT_VIZ_CAMERA = { rotation:.55, tilt:.78, zoom:1, panX:0, panY:0 };
const viz = { frame:0, nodes:[], edges:[], byId:{}, stars:[], data:null, mode:'constellation', paused:false, interaction:'rotate', drag:null, lastFrameTime:0, ...DEFAULT_VIZ_CAMERA };

function fmt(n){ if(n===undefined||n===null) return '—'; if(typeof n==='number') return n.toLocaleString(); return n; }
function esc(s){ return String(s ?? '').replace(/[&<>'"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function toast(msg){ const t=$('#toast'); t.textContent=msg; t.classList.add('show'); setTimeout(()=>t.classList.remove('show'),2600); }

function setScopeLoading(on, label='Loading…'){
  const scopeBar=$('#scopeBar');
  const scopeLoading=$('#scopeLoading');
  scopeBar?.classList.toggle('is-loading', !!on);
  scopeLoading?.classList.toggle('hidden', !on);
  if(scopeLoading){ scopeLoading.lastChild && (scopeLoading.lastChild.textContent=label); }
  [$('#scopeSelect'), $('#memoryNamespaceFilter')].filter(Boolean).forEach(el=>{ el.disabled=!!on; });
  const active=$('.view.active');
  if(active) active.setAttribute('aria-busy', on?'true':'false');
}
function setVisualiserLoading(on, label='Building visualiser'){
  const loading=$('#threeLoading');
  loading?.classList.toggle('hidden', !on);
  if(loading){
    const title=loading.querySelector('strong'); if(title) title.textContent=label;
    const detail=loading.querySelector('em'); if(detail) detail.textContent=ns()===ALL_NAMESPACES?'Preparing multiple namespace graphs…':'Preparing selected memory scope…';
  }
  $('#threeViewport')?.setAttribute('aria-busy', on?'true':'false');
  ['#threeRefresh','#threeReset','#threePanMode','#threePause','#threeFullscreen'].forEach(s=>{ const el=$(s); if(el) el.disabled=!!on; });
}
async function api(path, opts={}){
  const headers = {'Content-Type':'application/json', ...(opts.headers||{})};
  const res = await fetch(path, {...opts, headers});
  const txt = await res.text();
  let data; try{ data=txt?JSON.parse(txt):{} }catch{ data={raw:txt}; }
  if(res.status===401){ showLogin(); throw new Error(data.detail || 'Dashboard password required'); }
  if(!res.ok) throw new Error(data.detail || data.error || res.statusText);
  return data;
}
function ns(){ return state.selectedNamespace || ALL_NAMESPACES; }
function setNamespace(value){ const v=value || ALL_NAMESPACES; state.selectedNamespace=v; if($('#scopeSelect')) $('#scopeSelect').value=v; if($('#memoryNamespaceFilter')) $('#memoryNamespaceFilter').value=v; }
function syncMemoryFiltersFromUrl(params=new URLSearchParams(location.search)){
  if(params.has('q')) $('#memorySearch').value=params.get('q')||'';
  if(params.has('status')) $('#statusFilter').value=params.get('status')||'active';
  if(params.has('domain')) $('#domainFilter').value=params.get('domain')||'';
  if(params.has('source')) $('#sourceFilter').value=params.get('source')||'';
  if(params.has('sort')) $('#sortFilter').value=params.get('sort')||'created_at';
  if(params.has('namespace')) setNamespace(params.get('namespace') || ALL_NAMESPACES);
  updateMemoryAdvancedFilters();
}
function updateMemoryAdvancedFilters(){
  const details = $('#memoryAdvancedFilters');
  if(!details) return;
  const hasAdvanced = !!($('#domainFilter')?.value || $('#sourceFilter')?.value || (($('#sortFilter')?.value || 'created_at') !== 'created_at'));
  details.open = hasAdvanced;
}
function routeUrlFor(view){
  const url=new URL(location.href); url.searchParams.set('view',view); url.searchParams.delete('memory');
  ['q','status','domain','source','sort'].forEach(k=>url.searchParams.delete(k));
  const selectedNs=ns();
  if(selectedNs && selectedNs!==ALL_NAMESPACES) url.searchParams.set('namespace', selectedNs); else url.searchParams.delete('namespace');
  if(view==='memories'){
    const vals={q:$('#memorySearch')?.value||'',status:$('#statusFilter')?.value||'active',domain:$('#domainFilter')?.value||'',source:$('#sourceFilter')?.value||'',sort:$('#sortFilter')?.value||'created_at'};
    for(const [k,v] of Object.entries(vals)) if(v && !(['status','sort'].includes(k) && ['active','created_at'].includes(v))) url.searchParams.set(k,v);
  }
  return url;
}
function writeRoute(view,{replace=false}={}){
  const url=routeUrlFor(view); const obj={view,search:url.search};
  if(replace) history.replaceState(obj,'',url); else history.pushState(obj,'',url);
}

function setView(view, opts={}){
  view = VALID_VIEWS.has(view) ? view : 'overview';
  if(opts.query){
    if(opts.query.q!==undefined) $('#memorySearch').value=opts.query.q||'';
    if(opts.query.status!==undefined) $('#statusFilter').value=opts.query.status||'active';
    if(opts.query.domain!==undefined) $('#domainFilter').value=opts.query.domain||'';
    if(opts.query.source!==undefined) $('#sourceFilter').value=opts.query.source||'';
    if(opts.query.sort!==undefined) $('#sortFilter').value=opts.query.sort||'created_at';
    updateMemoryAdvancedFilters();
  }
  if(view==='memories' && opts.fromUrl) syncMemoryFiltersFromUrl();
  if(opts.updateUrl!==false) writeRoute(view,{replace:!!opts.replace});
  state.view=view; $$('.view').forEach(v=>v.classList.remove('active')); $(`#view-${view}`).classList.add('active');
  $$('.nav-item').forEach(b=>b.classList.toggle('active', b.dataset.view===view));
  document.body.classList.remove('menu-open');
  $('#menuToggle')?.setAttribute('aria-expanded','false');
  const names={overview:'Memory Operations',visualiser:'Visualiser',memories:'Memory Browser',recall:'Recall',conflicts:'Contradictions',graph:'Entity Graph','identity-scope':'Identity & Scope',lifecycle:'Lifecycle',ops:'Maintenance',settings:'Settings'};
  $('#pageTitle').textContent=names[view]||view;
  if(view!=='visualiser') stopVisualiser();
  if(view!=='memories') closeMemoryDetail({silent:true});
  if(view==='overview' && state.stats) requestAnimationFrame(()=>drawRecent(state.recentWrites||[]));
  if(view==='overview' && !state.stats) loadStats();
  if(view==='visualiser') loadVisualiser();
  if(view==='memories') loadMemories(); if(view==='recall' && !$('#recallQuery').value) $('#recallQuery').value='What should this agent remember about YantrikDB dashboards?';
  if(view==='conflicts') loadConflicts(); if(view==='graph') loadEntities(); if(view==='identity-scope') loadIdentityScope(); if(view==='lifecycle') loadLifecycle(); if(view==='ops') loadHealthPanel(); if(view==='settings') loadSettings();
}

async function init(){
  $$('.nav-item').forEach(b=>b.onclick=()=>setView(b.dataset.view));
  $('#brandHome')?.addEventListener('click',()=>setView('overview'));
  window.addEventListener('popstate',()=>{
    const params=new URLSearchParams(location.search);
    const view=params.get('view')||'overview';
    setView(view,{updateUrl:false,fromUrl:true});
    const rid=params.get('memory');
    if(view==='memories' && rid) selectMemory(rid,{updateUrl:false});
    else if(view==='memories') closeMemoryDetail({silent:true});
  });
  if(location.search.includes('menu=open')) document.body.classList.add('menu-open');
  $('#menuToggle')?.setAttribute('aria-expanded', String(document.body.classList.contains('menu-open')));
  $('#menuToggle')?.addEventListener('click',()=>{
    const open=!document.body.classList.contains('menu-open');
    document.body.classList.toggle('menu-open', open);
    $('#menuToggle')?.setAttribute('aria-expanded', String(open));
  });
  document.addEventListener('keydown',(e)=>{ if(e.key==='Escape'){ document.body.classList.remove('menu-open'); $('#menuToggle')?.setAttribute('aria-expanded','false'); closeMemoryDetail({silent:true}); } });
  $('#closeMemoryDrawer')?.addEventListener('click',()=>closeMemoryDetail());
  $('#memoryDrawer')?.addEventListener('click',(e)=>{ if(e.target.id==='memoryDrawer') closeMemoryDetail(); });
  $('#scopeSelect')?.addEventListener('change',()=>{ setNamespace($('#scopeSelect').value); state.memoryOffset=0; writeRoute(state.view,{replace:true}); refreshAll(); });
  $('#memoryNamespaceFilter')?.addEventListener('change',()=>{ setNamespace($('#memoryNamespaceFilter').value); state.memoryOffset=0; writeRoute('memories',{replace:true}); refreshAll(); });
  $('#memoryApply').onclick=()=>{state.memoryOffset=0; writeRoute('memories',{replace:true}); loadMemories();};
  $('#memoryReset')?.addEventListener('click',()=>{ $('#memorySearch').value=''; $('#statusFilter').value='active'; $('#domainFilter').value=''; $('#sourceFilter').value=''; $('#sortFilter').value='created_at'; updateMemoryAdvancedFilters(); state.memoryOffset=0; writeRoute('memories',{replace:true}); loadMemories(); });
  $('#runRecall').onclick=runRecall; $('#loadConflicts').onclick=loadConflicts;
  $('#threeRefresh')?.addEventListener('click',()=>loadVisualiser(true));
  $('#threeReset')?.addEventListener('click',()=>resetVisualiser());
  $('#threePanMode')?.addEventListener('click',()=>{ threeVis.panMode=!threeVis.panMode; updateThreeUI(); });
  $('#threePause')?.addEventListener('click',()=>{ threeVis.paused=!threeVis.paused; updateThreeUI(); });
  $('#threeFullscreen')?.addEventListener('click',()=>$('#threeViewport')?.requestFullscreen?.());
  $('#threeExitFullscreen')?.addEventListener('click',()=>document.exitFullscreen?.());
  document.addEventListener('fullscreenchange',()=>{ if(state.view==='visualiser') requestAnimationFrame(()=>resizeThree()); });
  $$('.visualiser-tabs button[data-three-mode]').forEach(b=>b.onclick=()=>setVisualiserMode(b.dataset.threeMode));
  $('#loadEntities').onclick=loadEntities; $('#loadGraph').onclick=()=>loadGraph($('#graphEntity').value||$('#entitySearch').value);
  $('#reloadIdentityScope')?.addEventListener('click',()=>loadIdentityScope()); $('#saveIdentityScope')?.addEventListener('click',()=>saveIdentityScope());
  $('#identityForm')?.addEventListener('submit', addIdentityFromForm); $('#actorForm')?.addEventListener('submit', addActorFromForm);
  $('#spaceForm')?.addEventListener('submit', addSpaceFromForm); $('#conversationForm')?.addEventListener('submit', addConversationFromForm);
  $('#runThink').onclick=runThink; $('#refreshHealth').onclick=loadHealthPanel; $('#adminModeToggle')?.addEventListener('change',()=>saveSettings());
  $('#saveMemoryScoping')?.addEventListener('click',()=>saveMemoryScopingSettings());
  ['#ownerScopingToggle','#includeBaseRecallToggle','#includeActorRecallToggle','#topKSetting'].forEach(sel=>$(sel)?.addEventListener('change',()=>saveMemoryScopingSettings({silent:true})));
  $('#savePassword')?.addEventListener('click',()=>saveSettings({password:true}));
  $('#disablePassword')?.addEventListener('click',()=>saveSettings({disablePassword:true}));
  $('#logoutBtn')?.addEventListener('click',logout);
  $('#loginBtn')?.addEventListener('click',login);
  $('#loginPassword')?.addEventListener('keydown',(e)=>{ if(e.key==='Enter') login(); });
  window.addEventListener('resize',()=>{ if(state.view==='overview' && state.recentWrites) requestAnimationFrame(()=>drawRecent(state.recentWrites)); });
  try{ await refreshAll(); }catch(e){ toast(e.message); return; }
  const initialView = new URLSearchParams(location.search).get('view') || 'overview';
  setView(initialView,{replace:true,fromUrl:true});
  const initialMemory = new URLSearchParams(location.search).get('memory');
  if(initialView==='memories' && initialMemory) selectMemory(initialMemory,{updateUrl:false});
}
function updateAdminBadge(){ const on=!!(state.health?.admin_enabled||state.settings?.admin_mode); $('#adminBadge').textContent=on?'Admin mode enabled':'Admin mode disabled'; $('#adminBadge').className=on?'pill warn':'pill neutral'; const ops=$('#opsAdminState'); if(ops){ops.textContent=on?'Admin mode enabled':'Admin mode disabled'; ops.className=on?'pill warn':'pill neutral';} }

function selectedNamespaceMeta(){
  const current=ns();
  const rows=state.health?.namespaces||[];
  const total=rows.reduce((a,n)=>a+Number(n.count||0),0);
  if(current===ALL_NAMESPACES) return {name:'All namespaces', count:total, all:true};
  const found=rows.find(n=>n.namespace===current);
  return {name:current || state.defaultNamespace, count:found?.count ?? null, all:false};
}
function updateScopeUi(){
  const meta=selectedNamespaceMeta();
  const count=meta.count===null?'':`${fmt(meta.count)} memor${Number(meta.count)===1?'y':'ies'}`;
  $('#scopeCount') && ($('#scopeCount').textContent=count);
  $('#scopeBar')?.classList.toggle('all-scope', !!meta.all);
  $('#opsScopeName') && ($('#opsScopeName').textContent=meta.name);
  $('#opsScopeCount') && ($('#opsScopeCount').textContent=count);
  $('#opsScopeName')?.closest('.maintenance-scope')?.classList.toggle('all-scope', !!meta.all);
  if($('#settingsExportLink')) $('#settingsExportLink').href=`/api/export/memories.jsonl?namespace=${encodeURIComponent(ns())}&status=active`;
}
async function refreshAll(){
  const loadingLabel = state.view==='visualiser' ? 'Building graph…' : 'Loading…';
  setScopeLoading(true, loadingLabel);
  if(state.view==='visualiser') setVisualiserLoading(true, 'Building visualiser');
  try{
    state.health=await api('/api/health');
    state.settings=await api('/api/settings');
    state.defaultNamespace=state.health.default_namespace;
    const nsRows=state.health.namespaces||[];
    const options=`<option value="${ALL_NAMESPACES}">All namespaces (${fmt(nsRows.reduce((a,n)=>a+Number(n.count||0),0))})</option>`+nsRows.map(n=>`<option value="${esc(n.namespace)}">${esc(n.namespace)} (${fmt(n.count)})</option>`).join('');
    const memSel=$('#memoryNamespaceFilter'); const scopeSel=$('#scopeSelect');
    const requested=new URLSearchParams(location.search).get('namespace');
    let prev=(requested || state.selectedNamespace || ALL_NAMESPACES);
    // HTTP-cluster mode has no local SQLite to enumerate namespaces from, so
    // health.namespaces is empty. Sending __all__ to /v1/stats or /v1/memories
    // would 400 because the server has no dashboard magic value — fall back to
    // the configured default_namespace on first load (no URL param, no prior
    // explicit choice) in that mode. Explicit __all__ picks still come through
    // via requested.
    if(state.health.mode==='http' && !requested && prev===ALL_NAMESPACES && state.defaultNamespace){
      prev=state.defaultNamespace;
    }
    state.selectedNamespace=prev;
    [memSel,scopeSel].filter(Boolean).forEach(s=>{
      s.innerHTML=options;
      if(![...s.options].some(o=>o.value===prev)) s.insertAdjacentHTML('afterbegin',`<option value="${esc(prev)}">${esc(prev)}</option>`);
      s.value=prev;
    });
    updateScopeUi();
    updateAdminBadge();
    await loadStats();
    if(state.view==='memories') await loadMemories();
    if(state.view==='lifecycle') await loadLifecycle();
    if(state.view==='visualiser') await loadVisualiser(true);
  } finally {
    setScopeLoading(false);
    if(state.view==='visualiser') setVisualiserLoading(false);
  }
}


function compactJson(value){ return JSON.stringify(value||{}, null, 2); }
function chip(label, value){ return `<div class="scope-chip"><span>${esc(label)}</span><strong>${esc(value || '—')}</strong></div>`; }
function scopeEmpty(title, body){ return `<div class="empty helpful-empty"><strong>${esc(title)}</strong><span>${esc(body)}</span></div>`; }
function sourceLabel(item){
  return item.source==='yantrikdb_identity_map'?'configured identity map':(item.source==='namespace_inventory'?'memory bucket discovery':'dashboard edit');
}
function identitySelectMarkup(selected, raw){
  const cfg=identityConfig();
  const ids=availableIdentityOptions(cfg).filter(Boolean);
  const opts=['', ...ids].filter((v,i,a)=>a.indexOf(v)===i);
  return `<select class="inline-identity-select" data-actor-select="${esc(raw)}" aria-label="Identity for ${esc(raw)}">${opts.map(id=>`<option value="${esc(id)}" ${id===(selected||'')?'selected':''}>${esc(id || 'Unassigned')}</option>`).join('')}</select>`;
}
function renderScopeRows(items, kind){
  if(!items?.length){
    const copy={
      identities:['No people yet.','Detected actors can be assigned once a person exists.'],
      actors:['No actors detected yet.','When WhatsApp, Telegram, or other platforms appear in identity config, they show here automatically.'],
      spaces:['No shared spaces yet.','Create one for household, team, or group-chat memory.'],
      conversations:['No chat routes yet.','Choose which shared space each group chat should use.'],
    }[kind] || ['Nothing configured.','Add rows below.'];
    return scopeEmpty(copy[0], copy[1]);
  }
  return items.map((item, idx)=>{
    const title=item.label || item.name || item.id || item.actor_id || item.conversation_id || item.scope || 'Unlabelled';
    const pieces=[]; let actions=''; let detail='';
    if(kind==='identities'){
      pieces.push(chip('Person ID', item.id), chip('Display name', item.label || item.id));
      detail = `<details class="scope-technical"><summary>Technical details</summary><div><span>Owner ID</span><code>${esc(item.private_scope || '')}</code></div>${item.resolved_scope?`<div><span>Storage namespace</span><code>${esc(item.resolved_scope)}</code></div>`:''}${item.source?`<div><span>Source</span><code>${esc(sourceLabel(item))}</code></div>`:''}</details>`;
      actions = `<button class="btn tiny secondary" type="button" data-edit-identity="${esc(item.id||'')}">Edit person</button>`;
    }
    if(kind==='actors'){
      const raw = `${item.platform||''}:${item.actor_id||''}`;
      pieces.push(chip('Platform', item.platform), chip('Actor ID', item.actor_id));
      if(item.alias) pieces.push(chip('Known alias', item.alias));
      detail = `<details class="scope-technical"><summary>Technical details</summary>${item.legacy_scope?`<div><span>Namespace</span><code>${esc(item.legacy_scope)}</code></div>`:''}${item.source?`<div><span>Source</span><code>${esc(sourceLabel(item))}</code></div>`:''}</details>`;
      actions = `<div class="inline-identity-control"><span>Identity</span><div class="inline-identity-actions">${identitySelectMarkup(item.identity, raw)}<button class="btn tiny primary" type="button" data-save-actor-identity="${esc(raw)}">Save</button></div></div>`;
      return `<div class="scope-card actor-row"><div class="actor-main"><strong>${esc(title)}</strong><div class="scope-chip-row">${pieces.join('')}</div>${detail}</div>${actions}</div>`;
    }
    if(kind==='spaces'){
      pieces.push(chip('Shared space', item.id), chip('Members', Array.isArray(item.members)?item.members.join(', '):(item.members||'')));
      detail = `<details class="scope-technical"><summary>Technical scope</summary><code>${esc(item.scope || '')}</code></details>`;
      actions = `<button class="btn tiny secondary" type="button" data-edit-space="${esc(item.id||'')}">Edit space</button>`;
    }
    if(kind==='conversations'){
      pieces.push(chip('Platform', item.platform), chip('Chat', item.conversation_id), chip('Shared space', item.scope));
      actions = `<button class="btn tiny secondary" type="button" data-edit-conversation="${esc((item.platform||'')+':'+(item.conversation_id||''))}">Edit route</button>`;
    }
    return `<div class="scope-card"><div class="scope-card-head"><strong>${esc(title)}</strong>${actions}</div><div class="scope-chip-row">${pieces.join('')}</div>${detail}</div>`;
  }).join('');
}
function bindIdentityScopeRowActions(){
  $$('[data-edit-identity]').forEach(btn=>btn.onclick=()=>fillIdentityForm(btn.dataset.editIdentity));
  $$('[data-save-actor-identity]').forEach(btn=>btn.onclick=()=>saveInlineActorIdentity(btn.dataset.saveActorIdentity));
  $$('[data-edit-space]').forEach(btn=>btn.onclick=()=>fillSpaceForm(btn.dataset.editSpace));
  $$('[data-edit-conversation]').forEach(btn=>btn.onclick=()=>fillConversationForm(btn.dataset.editConversation));
}

function identityConfig(){
  const cfg=state.identityScope?.identity_scope || {};
  return {
    identities:[...(cfg.identities||[])],
    actors:[...(cfg.actors||[])],
    spaces:[...(cfg.spaces||[])],
    conversations:[...(cfg.conversations||[])],
  };
}
function cleanId(value){ return String(value||'').trim(); }
function csvList(value){ return String(value||'').split(',').map(v=>v.trim()).filter(Boolean); }
function upsertBy(list, item, keyFn){
  const key=keyFn(item); const idx=list.findIndex(existing=>keyFn(existing)===key);
  if(idx>=0) list[idx]={...list[idx],...item}; else list.push(item);
}
function availableIdentityOptions(cfg){
  const ids=(cfg.identities||[]).map(i=>i.id).filter(Boolean);
  return ids.length ? ids : [''];
}
function identityLabel(id, cfg=identityConfig()){
  const item=(cfg.identities||[]).find(i=>i.id===id);
  return item?.label || id;
}
function availableScopeOptions(cfg){
  const scopes=[];
  (cfg.spaces||[]).forEach(s=>{ if(s.scope) scopes.push({value:s.scope, label:s.label || s.id || s.scope}); });
  return scopes.filter(s=>s.value);
}
function availablePlatformOptions(cfg){
  const vals=['whatsapp','telegram', ...(cfg.actors||[]).map(a=>a.platform), ...(cfg.conversations||[]).map(c=>c.platform)].filter(Boolean);
  return [...new Set(vals)];
}
function availableConversationOptions(cfg){
  return [...new Set((cfg.conversations||[]).map(c=>c.conversation_id).filter(Boolean))];
}
function selectedSpaceMembers(){
  return $$('[data-space-member]:checked').map(el=>el.value).filter(Boolean);
}
function renderSpaceMemberChecklist(cfg, selected=[]){
  const wrap=$('#spaceMembersChecklist'); if(!wrap) return;
  const ids=availableIdentityOptions(cfg).filter(Boolean);
  if(!ids.length){ wrap.innerHTML='<span class="muted">Create people first, then choose members here.</span>'; return; }
  const selectedSet=new Set(selected||[]);
  wrap.innerHTML=`<div class="checkbox-list-title">Members</div><div class="checkbox-pill-row">`+ids.map(id=>{ const label=identityLabel(id,cfg); return `<label class="checkbox-pill"><input type="checkbox" data-space-member value="${esc(id)}" aria-label="Shared space member ${esc(label)}" ${selectedSet.has(id)?'checked':''} /><span>${esc(label)}</span></label>`; }).join('')+`</div>`;
}
function actorIdentityFilterOptions(cfg){
  const ids=availableIdentityOptions(cfg).filter(Boolean);
  const hasUnassigned=(cfg.actors||[]).some(a=>!a.identity);
  const opts=[{value:'__all__', label:'All'}];
  ids.forEach(id=>opts.push({value:id, label:id}));
  if(hasUnassigned) opts.push({value:'__unassigned__', label:'Unassigned'});
  return opts;
}
function renderActorIdentityFilter(cfg){
  const sel=$('#actorIdentityFilter'); if(!sel) return;
  const opts=actorIdentityFilterOptions(cfg);
  if(!opts.some(o=>o.value===state.actorIdentityFilter)) state.actorIdentityFilter='__all__';
  sel.innerHTML=opts.map(o=>`<option value="${esc(o.value)}" ${o.value===state.actorIdentityFilter?'selected':''}>${esc(o.label)}</option>`).join('');
  sel.onchange=()=>{ state.actorIdentityFilter=sel.value || '__all__'; renderIdentityScope(state.identityScope); };
}
function filteredActors(cfg){
  const actors=cfg.actors||[];
  if(state.actorIdentityFilter==='__unassigned__') return actors.filter(a=>!a.identity);
  if(state.actorIdentityFilter && state.actorIdentityFilter!=='__all__') return actors.filter(a=>a.identity===state.actorIdentityFilter);
  return actors;
}

function renderIdentityScopeSelectors(cfg){
  const actorSel=$('#actorIdentity');
  if(actorSel){
    const current=actorSel.value; const options=availableIdentityOptions(cfg);
    actorSel.innerHTML=options.map(id=>`<option value="${esc(id)}">${esc(id || 'Choose identity')}</option>`).join('');
    if(options.includes(current)) actorSel.value=current;
  }
  renderSpaceMemberChecklist(cfg, selectedSpaceMembers());
  const platformSel=$('#conversationPlatform');
  if(platformSel){
    const current=platformSel.value; const options=availablePlatformOptions(cfg);
    platformSel.innerHTML=(options.length?options:['']).map(platform=>`<option value="${esc(platform)}">${esc(platform || 'Choose platform')}</option>`).join('');
    if(options.includes(current)) platformSel.value=current;
  }
  const chatOptions=$('#conversationIdOptions');
  if(chatOptions){ chatOptions.innerHTML=availableConversationOptions(cfg).map(id=>`<option value="${esc(id)}"></option>`).join(''); }
  const scopeSel=$('#conversationScope');
  if(scopeSel){
    const current=scopeSel.value; const options=availableScopeOptions(cfg);
    scopeSel.innerHTML=(options.length?options:[{value:'',label:'Create a shared space first'}]).map(scope=>`<option value="${esc(scope.value)}">${esc(scope.label)}</option>`).join('');
    if(options.some(o=>o.value===current)) scopeSel.value=current;
  }
}
async function persistIdentityConfig(cfg, message='Identity & Scope registry saved'){
  $('#identityScopeJson').value=compactJson(cfg);
  const data=await api('/api/identity-scope',{method:'POST',body:JSON.stringify({identity_scope:cfg})});
  renderIdentityScope(data); toast(message);
}

async function saveInlineActorIdentity(raw){
  const cfg=identityConfig();
  const actor=(cfg.actors||[]).find(a=>`${a.platform}:${a.actor_id}`===raw);
  const select=document.querySelector(`[data-actor-select="${CSS.escape(raw)}"]`);
  if(!actor || !select){ toast('Actor row not found'); return; }
  actor.identity=select.value || '';
  try{ await persistIdentityConfig(cfg, actor.identity ? 'Actor identity updated' : 'Actor marked unassigned'); }catch(err){ toast(err.message || 'Could not update actor identity'); }
}

function fillIdentityForm(id){
  const item=(identityConfig().identities||[]).find(i=>i.id===id); if(!item) return;
  $('#identityId').value=item.id||''; $('#identityLabel').value=item.label||''; $('#identityPrivateScope').value=item.private_scope||'';
  $('#identitySubmit').textContent='Update person'; $('#identityId').focus();
}
function fillActorForm(raw){
  const cfg=identityConfig(); const item=(cfg.actors||[]).find(a=>`${a.platform}:${a.actor_id}`===raw); if(!item) return;
  $('#actorPlatform').value=item.platform||''; $('#actorId').value=item.actor_id||''; renderIdentityScopeSelectors(cfg); $('#actorIdentity').value=item.identity||''; $('#actorIdentity').focus();
}
function fillSpaceForm(id){
  const item=(identityConfig().spaces||[]).find(s=>s.id===id); if(!item) return;
  $('#spaceId').value=item.id||''; $('#spaceLabel').value=item.label||''; $('#spaceScope').value=item.scope||''; renderSpaceMemberChecklist(identityConfig(), Array.isArray(item.members)?item.members:csvList(item.members||'')); $('#spaceId').focus();
}
function fillConversationForm(raw){
  const cfg=identityConfig(); const item=(cfg.conversations||[]).find(c=>`${c.platform}:${c.conversation_id}`===raw); if(!item) return;
  $('#conversationPlatform').value=item.platform||''; $('#conversationId').value=item.conversation_id||''; renderIdentityScopeSelectors(cfg); $('#conversationScope').value=item.scope||''; $('#conversationScope').focus();
}

async function addIdentityFromForm(e){
  e.preventDefault();
  const id=cleanId($('#identityId')?.value);
  if(!id){ toast('Enter an identity ID first'); return; }
  const cfg=identityConfig();
  const privateScope=cleanId($('#identityPrivateScope')?.value) || `owner:${id}`;
  upsertBy(cfg.identities,{id,label:cleanId($('#identityLabel')?.value)||id,private_scope:privateScope}, item=>item.id);
  try{ await persistIdentityConfig(cfg, 'Person saved'); e.target.reset(); $('#identitySubmit').textContent='Save person'; }catch(err){ toast(err.message || 'Could not save person'); }
}
async function addActorFromForm(e){
  e.preventDefault();
  const platform=cleanId($('#actorPlatform')?.value); const actor_id=cleanId($('#actorId')?.value); const identity=cleanId($('#actorIdentity')?.value);
  if(!platform || !actor_id || !identity){ toast('Enter platform, actor ID, and identity'); return; }
  const cfg=identityConfig();
  upsertBy(cfg.actors,{platform,actor_id,identity}, item=>`${item.platform}:${item.actor_id}`);
  try{ await persistIdentityConfig(cfg, 'Actor mapped'); e.target.reset(); renderIdentityScopeSelectors(cfg); }catch(err){ toast(err.message || 'Could not map actor'); }
}
async function addSpaceFromForm(e){
  e.preventDefault();
  const id=cleanId($('#spaceId')?.value);
  if(!id){ toast('Enter a shared space ID first'); return; }
  const cfg=identityConfig();
  const scope=cleanId($('#spaceScope')?.value) || `space:${id}`;
  upsertBy(cfg.spaces,{id,label:cleanId($('#spaceLabel')?.value)||id,scope,members:selectedSpaceMembers()}, item=>item.id);
  try{ await persistIdentityConfig(cfg, 'Shared space added'); e.target.reset(); }catch(err){ toast(err.message || 'Could not add shared space'); }
}
async function addConversationFromForm(e){
  e.preventDefault();
  const platform=cleanId($('#conversationPlatform')?.value); const conversation_id=cleanId($('#conversationId')?.value); const scope=cleanId($('#conversationScope')?.value);
  if(!platform || !conversation_id || !scope){ toast('Choose platform, chat ID, and shared space'); return; }
  const cfg=identityConfig();
  upsertBy(cfg.conversations,{platform,conversation_id,scope}, item=>`${item.platform}:${item.conversation_id}`);
  try{ await persistIdentityConfig(cfg, 'Chat route saved'); e.target.reset(); renderIdentityScopeSelectors(cfg); }catch(err){ toast(err.message || 'Could not route chat'); }
}

function renderIdentityScope(data){
  state.identityScope=data;
  const cfg=data.identity_scope||{}; const summary=data.summary||{}; const runtime=data.runtime_scope||{};
  $('#identityScopeSummary').innerHTML=[['People',summary.identities],['Actors',summary.actors],['Shared spaces',summary.spaces],['Chat routes',summary.conversations],['Needs review',summary.unmapped_namespaces]].map(([k,v])=>`<div class="metric"><div class="metric-label">${esc(k)}</div><div class="metric-value">${esc(fmt(v||0))}</div></div>`).join('');
  $('#identityScopingStatus').innerHTML=[
    ['Person scoping', runtime.owner_scoping, 'owner_scoping'],
    ['Shared fallback', runtime.include_base_namespace_recall, 'include_base_namespace_recall'],
    ['Old actor recall', runtime.include_legacy_actor_namespace_recall, 'include_legacy_actor_namespace_recall'],
  ].map(([label,on,key])=>`<span class="scope-status ${on?'on':'off'}"><strong>${esc(label)}: ${on?'On':'Off'}</strong><code title="${esc(key)}">${esc(key)}</code></span>`).join('');
  $('#identityList').innerHTML=renderScopeRows(cfg.identities,'identities');
  renderActorIdentityFilter(cfg);
  $('#actorList').innerHTML=renderScopeRows(filteredActors(cfg),'actors');
  $('#spaceList').innerHTML=renderScopeRows(cfg.spaces,'spaces');
  $('#conversationList').innerHTML=renderScopeRows(cfg.conversations,'conversations');
  $('#identityNamespaceTable').innerHTML=(data.namespace_inventory||[]).length ? `<div class="namespace-coverage-list">${(data.namespace_inventory||[]).map(n=>{
    const type=(n.mapping_type || '').replaceAll('_',' ');
    const source=n.mapping_source ? ` · ${n.mapping_source}` : '';
    const belongs=n.mapped?`<div class="mapped-owner"><strong>${esc(n.mapped_to || 'Mapped')}</strong><span>${esc(type + source)}</span></div>`:'<span class="muted">No person, shared space, or enabled fallback yet</span>';
    const status=n.mapped?(n.derived_by_config?'Covered by settings':'Mapped'):'Needs review';
    const pillClass=n.mapped?(n.derived_by_config?'good':'neutral'):'warn';
    return `<article class="namespace-coverage-card"><div class="namespace-coverage-head"><div><span class="coverage-label">Namespace</span><code title="${esc(n.namespace)}">${esc(n.namespace)}</code></div><span class="pill coverage-status ${pillClass}">${esc(status)}</span></div><div class="namespace-coverage-body"><div><span class="coverage-label">Rows</span><strong>${fmt(n.count)}</strong></div><div><span class="coverage-label">Belongs to</span>${belongs}</div></div></article>`;
  }).join('')}</div>` : scopeEmpty('No namespaces found.','Memory buckets will appear here once YantrikDB has rows.');
  $('#identityScopeJson').value=compactJson(cfg);
  renderIdentityScopeSelectors(cfg);
  bindIdentityScopeRowActions();
}

async function loadIdentityScope(){
  const data=await api('/api/identity-scope');
  renderIdentityScope(data);
}
async function saveIdentityScope(){
  try{
    const parsed=JSON.parse($('#identityScopeJson').value||'{}');
    const data=await api('/api/identity-scope',{method:'POST',body:JSON.stringify({identity_scope:parsed})});
    renderIdentityScope(data); toast('Identity & Scope registry saved');
  }catch(e){ toast(e.message || 'Invalid Identity & Scope JSON'); }
}

function settingsRows(s){
  const y=s.yantrikdb||{};
  const rows=[
    ['Admin mode', s.admin_mode ? 'Enabled' : 'Read-only'],
    ['Password', s.password_enabled ? 'Enabled' : 'Disabled'],
    ['Backend mode', y.mode || 'embedded'],
    ['Base namespace', y.namespace || 'hermes'],
    ['Default scope', y.default_namespace || s.default_namespace],
    ['Database', s.db_path],
    ['Provider config', y.config_path || ''],
    ['Provider map', y.identity_map_path || 'not set'],
    ['Embedder', `${s.embedder} (${s.embedding_dim}d)`],
  ];
  return rows.map(([k,v])=>`<div class="diag-row"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join('');
}
async function loadSettings(){
  state.settings=await api('/api/settings');
  const toggle=$('#adminModeToggle'); if(toggle) toggle.checked=!!state.settings.admin_mode;
  const y=state.settings.yantrikdb||{};
  const ownerToggle=$('#ownerScopingToggle'); if(ownerToggle) ownerToggle.checked=!!y.owner_scoping;
  const baseToggle=$('#includeBaseRecallToggle'); if(baseToggle) baseToggle.checked=!!y.include_base_namespace_recall;
  const actorToggle=$('#includeActorRecallToggle'); if(actorToggle) actorToggle.checked=!!y.include_legacy_actor_namespace_recall;
  const topK=$('#topKSetting'); if(topK) topK.value=y.top_k||10;
  const passwordState=$('#passwordState'); if(passwordState) passwordState.textContent=state.settings.password_enabled?'Password enabled. Changes clear saved browser sessions.':'Password disabled.';
  updateScopeUi();
  $('#settingsRuntime').innerHTML=settingsRows(state.settings);
  updateAdminBadge();
}
async function saveMemoryScopingSettings(opts={}){
  const payload={
    admin_mode:!!$('#adminModeToggle')?.checked,
    owner_scoping:!!$('#ownerScopingToggle')?.checked,
    include_base_namespace_recall:!!$('#includeBaseRecallToggle')?.checked,
    include_legacy_actor_namespace_recall:!!$('#includeActorRecallToggle')?.checked,
    top_k:Number($('#topKSetting')?.value || 10),
  };
  try{
    state.settings=await api('/api/settings',{method:'POST',body:JSON.stringify(payload)});
    await loadSettings();
    if(!opts.silent) toast('Memory settings saved');
  }catch(e){ toast(e.message || 'Could not save memory settings'); await loadSettings(); }
}

async function saveSettings(opts={}){
  const enabled=!!$('#adminModeToggle')?.checked;
  const payload={admin_mode:enabled};
  if(opts.password){
    const pw=$('#dashboardPassword')?.value||'';
    if(!pw.trim()){ toast('Enter a new password first'); return; }
    payload.new_password=pw;
  }
  if(opts.disablePassword) payload.disable_password=true;
  try{
    state.settings=await api('/api/settings',{method:'POST',body:JSON.stringify(payload)});
    if($('#dashboardPassword')) $('#dashboardPassword').value='';
    if(opts.password) { showLogin(); toast('Password changed — log in again'); return; }
    state.health=await api('/api/health');
    await loadSettings();
    if(opts.disablePassword) toast('Password disabled');
    else toast(enabled?'Admin Mode enabled':'Admin Mode disabled');
  }catch(e){toast(e.message); await loadSettings();}
}

function showLogin(){ $('#loginModal')?.classList.remove('hidden'); setTimeout(()=>$('#loginPassword')?.focus(),50); }
function hideLogin(){ $('#loginModal')?.classList.add('hidden'); if($('#loginPassword')) $('#loginPassword').value=''; }
async function login(){
  try{
    await api('/api/auth/login',{method:'POST',body:JSON.stringify({password:$('#loginPassword')?.value||''})});
    hideLogin(); toast('Unlocked'); await refreshAll();
    const initialView = new URLSearchParams(location.search).get('view') || state.view || 'overview';
    setView(initialView,{replace:true,fromUrl:true});
  }catch(e){ toast(e.message); }
}
async function logout(){
  try{ await api('/api/auth/logout',{method:'POST',body:'{}'}); showLogin(); toast('Logged out'); }catch(e){ toast(e.message); }
}

async function loadStats(){
  state.stats=await api(`/api/stats?namespace=${encodeURIComponent(ns())}`);
  const engine=state.stats.engine||{};
  const active = engine.active_memories ?? (state.stats.memory_status||[]).find(x=>x.status==='active')?.count ?? 0;
  const consolidated = engine.consolidated_memories ?? (state.stats.memory_status||[]).find(x=>x.status==='consolidated')?.count ?? 0;
  const tomb = engine.tombstoned_memories ?? (state.stats.memory_status||[]).find(x=>x.status==='tombstoned')?.count ?? 0;
  const metrics=[['Active',active],['Consolidated',consolidated],['Forgotten',tomb],['Open conflicts',state.stats.open_conflicts],['Entities',state.stats.entities],['Edges',state.stats.edges],['DB size',humanBytes(state.health.db_size_bytes)],['Embedder',state.health.embedder]];
  if($('#heroMetrics')) $('#heroMetrics').innerHTML=metrics.slice(0,4).map(m=>metric(m[0],m[1])).join('');
  $('#statsGrid').innerHTML=metrics.map(m=>`<div class="stat-card"><div class="stat-label">${esc(m[0])}</div><div class="stat-value">${esc(fmt(m[1]))}</div></div>`).join('');
  $('#composition').innerHTML=compositionBlock('Domains',state.stats.by_domain)+compositionBlock('Sources',state.stats.by_source)+compositionBlock('Types',state.stats.by_type);
  state.recentWrites=state.stats.recent_by_day||[];
  drawRecent(state.recentWrites);
  $('#namespaceTable').innerHTML=`<table><thead><tr><th>Memory scope</th><th>Rows</th></tr></thead><tbody>${(state.health.namespaces||[]).map(n=>`<tr><td><code>${esc(n.namespace)}</code></td><td>${fmt(n.count)}</td></tr>`).join('')}</tbody></table>`;
  updateScopeUi();
}

function humanBytes(b){ if(!b) return '0 B'; const u=['B','KB','MB','GB']; let i=0; while(b>1024&&i<u.length-1){b/=1024;i++;} return `${b.toFixed(i?1:0)} ${u[i]}`; }
function metric(label,value){return `<div class="metric"><div class="metric-label">${esc(label)}</div><div class="metric-value">${esc(fmt(value))}</div></div>`}
function compositionBlock(title, arr){ const max=Math.max(1,...(arr||[]).map(x=>x.count)); return `<div class="detail-section"><div class="label">${title}</div>${(arr||[]).slice(0,8).map(x=>`<div class="bar-row"><span>${esc(x.domain||x.source||x.type||'—')}</span><div class="bar"><span style="width:${(x.count/max)*100}%"></span></div><b>${fmt(x.count)}</b></div>`).join('')}</div>`; }
function formatShortDate(day){
  const date=new Date(`${day}T00:00:00`);
  if(Number.isNaN(date.getTime())) return String(day||'').slice(5);
  return date.toLocaleDateString(undefined,{month:'short',day:'numeric'});
}
function recentSummaryBlock(chart){
  const total=chart.reduce((a,d)=>a+(Number(d.count)||0),0);
  const activeDays=chart.filter(d=>(Number(d.count)||0)>0).length;
  const peak=chart.reduce((best,d)=>(Number(d.count)||0)>(Number(best?.count)||0)?d:best, chart[0]||null);
  const latest=[...chart].reverse().find(d=>(Number(d.count)||0)>0);
  const avg=activeDays?Math.round(total/activeDays):0;
  const cells=[
    ['Total', fmt(total)],
    ['Active days', `${fmt(activeDays)}/${fmt(chart.length||0)}`],
    ['Peak day', peak?`${fmt(peak.count)} · ${formatShortDate(peak.day)}`:'—'],
    ['Avg / active day', fmt(avg)],
  ];
  return cells.map(([k,v])=>`<div class="recent-mini"><span>${esc(k)}</span><strong title="${esc(v)}">${esc(v)}</strong></div>`).join('');
}
function drawRecent(data){
  const c=$('#recentChart'); if(!c) return;
  const chart=(data||[]).filter(d=>d && d.day);
  const summary=$('#recentSummary'); if(summary) summary.innerHTML=recentSummaryBlock(chart);
  const rect=c.getBoundingClientRect();
  const parentW=c.parentElement?.clientWidth || 0;
  const cssW=Math.max(320, Math.floor(rect.width || parentW || 320));
  const cssH=190;
  const dpr=Math.min(devicePixelRatio||1,2);
  c.style.width='100%'; c.style.height=`${cssH}px`;
  c.width=Math.floor(cssW*dpr); c.height=Math.floor(cssH*dpr);
  const ctx=c.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,cssW,cssH);
  ctx.font='12px system-ui, -apple-system, BlinkMacSystemFont, sans-serif';
  if(!chart.length){
    ctx.fillStyle='rgba(255,247,249,.52)';
    ctx.textAlign='center';
    ctx.fillText('No recent writes in this scope', cssW/2, cssH/2);
    return;
  }
  const max=Math.max(1,...chart.map(d=>Number(d.count)||0));
  const left=44, right=14, top=14, bottom=34;
  const w=cssW-left-right, h=cssH-top-bottom;
  ctx.strokeStyle='rgba(255,255,255,.12)'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(left,top); ctx.lineTo(left,top+h); ctx.lineTo(left+w,top+h); ctx.stroke();
  ctx.fillStyle='rgba(255,247,249,.52)'; ctx.textAlign='right'; ctx.textBaseline='middle';
  [0,.5,1].forEach(t=>{ const y=top+h-(h*t); const v=Math.round(max*t); ctx.fillText(String(v), left-9, y); ctx.strokeStyle='rgba(255,255,255,.055)'; ctx.beginPath(); ctx.moveTo(left,y); ctx.lineTo(left+w,y); ctx.stroke(); });
  const gap=Math.max(2, Math.min(7, w/90));
  const bw=w/Math.max(1,chart.length);
  chart.forEach((d,i)=>{
    const value=Number(d.count)||0;
    const x=left+i*bw+gap/2;
    const bh=Math.max(value?3:0, h*(value/max));
    const y=top+h-bh;
    const g=ctx.createLinearGradient(0,y,0,top+h); g.addColorStop(0,'#e94560'); g.addColorStop(1,'#ffa500');
    ctx.fillStyle=g; ctx.beginPath(); ctx.roundRect(x,y,Math.max(2,bw-gap),bh,4); ctx.fill();
  });
  const labelEvery=cssW<520?Math.ceil(chart.length/4):Math.ceil(chart.length/6);
  ctx.fillStyle='rgba(255,247,249,.58)'; ctx.textAlign='center'; ctx.textBaseline='top';
  chart.forEach((d,i)=>{ if(i===0 || i===chart.length-1 || i%labelEvery===0){ const x=left+i*bw+bw/2; ctx.fillText(formatShortDate(d.day), x, top+h+10); } });
}

function visualiserColors(){ return viz.mode==='neural' ? {bg:'#050915',core:'rgba(24,130,112,.30)',mid:'rgba(9,14,28,.96)',star:'#66e8c6',memory:'#ff9b6a',text:'#f7f8ff',edge:'rgba(102,232,198,.36)',memoryEdge:'rgba(255,155,106,.44)'} : {bg:'#050711',core:'rgba(233,69,96,.18)',mid:'rgba(9,10,18,.96)',star:'#ffd6dd',memory:'#ffa500',text:'#f7f8ff',edge:'rgba(255,214,221,.30)',memoryEdge:'rgba(255,165,0,.42)'}; }
function setVisualiserMode(mode){ viz.mode=mode||'constellation'; $$('.visualiser-tabs button').forEach(b=>b.classList.toggle('active',b.dataset.vizMode===viz.mode)); document.querySelector('.constellation-wrap')?.setAttribute('data-visualiser',viz.mode); if(viz.data) buildVisualiserScene(viz.data); }
function resetVisualiser(){ Object.assign(viz, DEFAULT_VIZ_CAMERA, {paused:false, interaction:'rotate', drag:null}); $('#vizPause').textContent='Pause rotation'; $('#vizPanMode').textContent='Pan mode'; if(viz.data) buildVisualiserScene(viz.data); }
function stopVisualiser(){ if(viz.frame) cancelAnimationFrame(viz.frame); viz.frame=0; viz.drag=null; }
async function loadVisualiser(force=false){ if(viz.data && !force){ startVisualiser(); return; } const data=await api(`/api/constellation?namespace=${encodeURIComponent(ns())}&limit=260`); buildVisualiserScene(data); }
function buildVisualiserScene(data){
  const nodes=(data.nodes||[]).slice(0,180).map(n=>({...n}));
  const cats=[...new Set(nodes.map(n=>n.category||'Other'))]; const catIndex=Object.fromEntries(cats.map((c,i)=>[c,i]));
  const degree=new Map(); (data.edges||[]).forEach(e=>{degree.set(e.source,(degree.get(e.source)||0)+1);degree.set(e.target,(degree.get(e.target)||0)+1);});
  nodes.forEach((n,i)=>{ const ci=catIndex[n.category||'Other']||0; const angle=(i/Math.max(nodes.length,1))*Math.PI*2+ci*.62; const band=n.kind==='memory'?1.25:.70+(ci%4)*.16; const r=250*band+(i%7)*16; if(viz.mode==='neural'){ const region=(ci-cats.length/2)*84; n.x=Math.cos(angle)*(.38*r)+region; n.y=Math.sin(angle*1.7)*(80+(ci%4)*18); n.z=Math.sin(angle)*(.55*r)+(((i*97)%181)-90)*1.2; } else { n.x=Math.cos(angle)*r; n.y=Math.sin(angle*1.23)*(100+(ci%5)*24)+(((i*53)%131)-65)*.82; n.z=Math.sin(angle)*r*.82+(((i*97)%181)-90)*1.55+((ci%5)-2)*42; } n.size=Math.min(26,5+Math.sqrt(Number(n.weight||n.count||1)+(degree.get(n.id)||0))*3.2)*(n.kind==='memory'?1.08:1); n.twinkle=(i%17)/17; });
  viz.nodes=nodes; viz.byId=Object.fromEntries(nodes.map(n=>[n.id,n])); viz.edges=(data.edges||[]).filter(e=>viz.byId[e.source]&&viz.byId[e.target]).slice(0,360); viz.data=data; viz.stars=Array.from({length:120},(_,i)=>({x:((i*73)%1000)/1000,y:((i*191)%680)/680,r:.35+((i*37)%100)/90,a:.12+((i*29)%100)/260,phase:(i*47)%628/100,freq:.0006+((i*41)%90)/100000}));
  $('#constellationClusters').innerHTML=(data.clusters||[]).map(c=>`<button class="cluster-pill">${esc(c.label)} <strong>${fmt(c.count)}</strong></button>`).join('');
  visualiserInspectorDefault(); startVisualiser();
}
function visualiserInspectorDefault(){ $('#constellationInspector').innerHTML=`<div class="inspector-kicker">${viz.mode==='neural'?'Neural inspector':'Constellation inspector'}</div><h3>Nothing selected</h3><p class="muted">Pick a ${viz.mode==='neural'?'neuron hub, memory soma, or synapse':'star, memory, or link'} to inspect the underlying YantrikDB source.</p>`; }
function inspectVisualNode(node){ $('#constellationInspector').innerHTML=`<div class="inspector-kicker">${esc(node.kind||'entity')}</div><h3>${esc(node.label)}</h3><p class="muted">${esc(node.category||'Other')} · ${fmt(node.count||0)} signal(s) · weight ${Number(node.weight||0).toFixed(2)}</p>${node.preview?`<p>${esc(node.preview)}</p>`:''}<div class="inspector-actions">${node.memory_id?'<button id="vizOpenMemory" class="btn primary tiny">Open memory</button>':''}<button id="vizSearch" class="btn secondary tiny">Search this</button></div>`; if(node.memory_id) $('#vizOpenMemory').onclick=()=>selectMemory(node.memory_id); $('#vizSearch').onclick=()=>{ setView('memories'); $('#memorySearch').value=node.label.replace(/^memory:/,''); state.memoryOffset=0; loadMemories(); }; }
function projectVisualNode(n,w,h){ const cos=Math.cos(viz.rotation), sin=Math.sin(viz.rotation); const xr=n.x*cos-n.z*sin, z0=n.x*sin+n.z*cos; const y=n.y*Math.cos(viz.tilt)-z0*Math.sin(viz.tilt); const z=n.y*Math.sin(viz.tilt)+z0*Math.cos(viz.tilt); const depth=viz.mode==='neural'?920:760; const scale=depth/(depth+z+260); const fit=w<620?Math.min(.78,Math.max(.56,(w-36)/680)):Math.min(1.16,Math.max(.68,(w-72)/760)); return {x:w/2+viz.panX+xr*scale*fit*viz.zoom,y:h/2+viz.panY+y*scale*fit*viz.zoom,z,scale:scale*viz.zoom,visible:scale>.32}; }
function drawVisualFrame(t=0){ const canvas=$('#constellationCanvas'); if(!canvas||state.view!=='visualiser') return; const wrap=canvas.parentElement; const w=Math.max(320,wrap.clientWidth||1000), h=Math.max(430,wrap.clientHeight||680); const dpr=Math.min(devicePixelRatio||1,w<620?2:1.5); if(canvas.width!==Math.floor(w*dpr)||canvas.height!==Math.floor(h*dpr)){canvas.width=Math.floor(w*dpr);canvas.height=Math.floor(h*dpr);} const ctx=canvas.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0); const c=visualiserColors(); if(!viz.paused&&!viz.drag&&!matchMedia('(prefers-reduced-motion: reduce)').matches){ const delta=viz.lastFrameTime?Math.min(48,t-viz.lastFrameTime):16; viz.rotation+=delta*(viz.mode==='neural'?0.000032:0.000045); } viz.lastFrameTime=t; ctx.clearRect(0,0,w,h); const bg=ctx.createRadialGradient(w*.48,h*.42,18,w*.48,h*.42,Math.max(w,h)*.78); bg.addColorStop(0,c.core); bg.addColorStop(.48,c.mid); bg.addColorStop(1,c.bg); ctx.fillStyle=bg; ctx.fillRect(0,0,w,h); viz.stars.forEach(s=>{ctx.globalAlpha=s.a*(.5+Math.sin(t*s.freq+s.phase)*.3);ctx.fillStyle=c.text;ctx.beginPath();ctx.arc(s.x*w,s.y*h,s.r*.7,0,Math.PI*2);ctx.fill();}); const projected=new Map(); viz.nodes.forEach(n=>projected.set(n.id,projectVisualNode(n,w,h))); viz.edges.forEach(e=>{const a=projected.get(e.source),b=projected.get(e.target); if(!a||!b)return; ctx.globalAlpha=e.kind==='memory'?.26:.20; ctx.strokeStyle=e.kind==='memory'?c.memoryEdge:c.edge; ctx.lineWidth=.8; ctx.beginPath(); ctx.moveTo(a.x,a.y); const mx=(a.x+b.x)/2,my=(a.y+b.y)/2,dx=b.x-a.x,dy=b.y-a.y,len=Math.max(1,Math.hypot(dx,dy)),curve=Math.min(42,len*.15)*(((e.id||'').length%2)?1:-1); ctx.quadraticCurveTo(mx-dy/len*curve,my+dx/len*curve,b.x,b.y); ctx.stroke(); }); const hits=[]; [...viz.nodes].sort((a,b)=>(projected.get(a.id)?.z||0)-(projected.get(b.id)?.z||0)).forEach(n=>{const p=projected.get(n.id); if(!p?.visible)return; const base=n.kind==='memory'?c.memory:c.star; const r=Math.min(18,Math.max(4,n.size*p.scale)); const pulse=1+Math.sin(t*.0015+n.twinkle*6.28)*.07; const halo=r*(n.kind==='memory'?2.1:2.5); const g=ctx.createRadialGradient(p.x,p.y,0,p.x,p.y,halo); g.addColorStop(0,'rgba(255,255,255,.86)'); g.addColorStop(.22,base); g.addColorStop(1,'rgba(0,0,0,0)'); ctx.globalAlpha=.28; ctx.fillStyle=g; ctx.beginPath(); ctx.arc(p.x,p.y,halo,0,Math.PI*2); ctx.fill(); ctx.globalAlpha=.95; ctx.fillStyle=base; ctx.beginPath(); ctx.arc(p.x,p.y,r*pulse,0,Math.PI*2); ctx.fill(); ctx.globalAlpha=.9; ctx.fillStyle='rgba(255,255,255,.88)'; ctx.beginPath(); ctx.arc(p.x-r*.18,p.y-r*.22,Math.max(1,r*.23),0,Math.PI*2); ctx.fill(); if(w>520||n.kind!=='memory'){ ctx.globalAlpha=.78; ctx.fillStyle=c.text; ctx.font=`${n.kind==='memory'?10:12}px Inter, sans-serif`; ctx.textAlign='center'; ctx.fillText(String(n.label||'').slice(0,22),p.x,p.y+r+14); } hits.push({node:n,x:p.x,y:p.y,r:Math.max(r+8,14)}); }); viz.hits=hits; ctx.globalAlpha=1; viz.frame=requestAnimationFrame(drawVisualFrame); }
function startVisualiser(){ stopVisualiser(); viz.frame=requestAnimationFrame(drawVisualFrame); }
function bindVisualiserEvents(){ const canvas=$('#constellationCanvas'); if(!canvas||canvas.dataset.bound)return; canvas.dataset.bound='1'; const point=e=>{const r=canvas.getBoundingClientRect(); const t=e.touches?.[0]||e; return {x:t.clientX-r.left,y:t.clientY-r.top};}; const down=e=>{ if(e.button===2 && e.cancelable) e.preventDefault(); const p=point(e); const rightPan=e.button===2; if(!rightPan){ const hit=[...(viz.hits||[])].reverse().find(h=>Math.hypot(h.x-p.x,h.y-p.y)<=h.r); if(hit){inspectVisualNode(hit.node); return;} } viz.drag={x:p.x,y:p.y,rot:viz.rotation,tilt:viz.tilt,panX:viz.panX,panY:viz.panY,pan:rightPan||e.shiftKey||viz.interaction==='pan'}; canvas.setPointerCapture?.(e.pointerId);}; const move=e=>{ if(!viz.drag)return; const p=point(e), dx=p.x-viz.drag.x, dy=p.y-viz.drag.y; if(viz.drag.pan){viz.panX=viz.drag.panX+dx;viz.panY=viz.drag.panY+dy;} else {viz.rotation=viz.drag.rot+dx*.006;viz.tilt=Math.max(.15,Math.min(1.35,viz.drag.tilt+dy*.004));} }; const up=()=>{viz.drag=null;}; canvas.addEventListener('contextmenu',e=>e.preventDefault()); canvas.addEventListener('pointerdown',down); canvas.addEventListener('pointermove',move); canvas.addEventListener('pointerup',up); canvas.addEventListener('pointercancel',up); canvas.addEventListener('wheel',e=>{e.preventDefault(); viz.zoom=Math.max(.45,Math.min(4,viz.zoom*(e.deltaY>0?.92:1.09)));},{passive:false}); }

async function loadMemories(){
  const qs=new URLSearchParams({namespace:ns(),status:$('#statusFilter').value,q:$('#memorySearch').value,domain:$('#domainFilter').value,source:$('#sourceFilter').value,sort:$('#sortFilter').value,limit:50,offset:state.memoryOffset});
  const data=await api('/api/memories?'+qs);
  $('#memoryList').innerHTML=data.items.map(memoryItem).join('') || '<div class="empty helpful-empty"><strong>No memories found.</strong><span>Try clearing filters or switching memory scope.</span></div>';
  $$('#memoryList .memory-item').forEach(el=>el.onclick=()=>selectMemory(el.dataset.rid));
  $('#memoryPager').innerHTML=`<button class="btn secondary" ${state.memoryOffset<=0?'disabled':''} id="prevPage">Prev</button><span>${fmt(state.memoryOffset+1)}–${fmt(Math.min(state.memoryOffset+50,data.total))} of ${fmt(data.total)}</span><button class="btn secondary" ${state.memoryOffset+50>=data.total?'disabled':''} id="nextPage">Next</button>`;
  $('#prevPage')?.addEventListener('click',()=>{state.memoryOffset=Math.max(0,state.memoryOffset-50);loadMemories();}); $('#nextPage')?.addEventListener('click',()=>{state.memoryOffset+=50;loadMemories();});
}
function statusLabel(s){ return s==='tombstoned'?'forgotten':(s||'active'); }
function statusPillClass(s){ const v=String(s||'active'); return v==='active' ? 'good' : (v==='tombstoned' ? 'hot' : 'neutral'); }
function shortNamespace(value){
  const raw=String(value||'');
  if(!raw) return '';
  const owner=raw.match(/owner:([^:]+)$/);
  if(owner) return owner[1].replace(/^owner-/,'');
  const parts=raw.split(':');
  return parts.slice(-2).join(':') || raw;
}
function memoryItem(m){
  const showNs=ns()===ALL_NAMESPACES;
  return `<div class="memory-item" data-rid="${esc(m.rid)}">
    <div class="memory-headline"><div class="memory-title">${esc(m.domain||'general')} <span class="muted">· ${esc(m.type)}</span></div>${showNs?`<span class="namespace-chip" title="${esc(m.namespace||'')}">${esc(shortNamespace(m.namespace))}</span>`:''}</div>
    <div class="memory-text">${esc(m.text)}</div>
    <div class="meta-row"><span class="pill ${statusPillClass(m.consolidation_status)}">${esc(statusLabel(m.consolidation_status))}</span><span class="pill">importance ${Number(m.importance||0).toFixed(2)}</span><span class="pill">${esc(m.source||'source')}</span><span class="pill">${esc(m.created_at_iso||'')}</span></div>
  </div>`;
}
async function selectMemory(rid, opts={}){
  state.selectedMemory=rid;
  $$('#memoryList .memory-item').forEach(x=>x.classList.toggle('active',x.dataset.rid===rid));
  const m=await api('/api/memory/'+encodeURIComponent(rid)+'?namespace='+encodeURIComponent(ns()));
  $('.drawer-title').textContent='YantrikDB memory';
  $('#memoryDrawerBody').innerHTML=detailHtml(m);
  $('#memoryDrawer').classList.remove('hidden');
  if(opts.updateUrl!==false){
    if(state.view!=='memories') setView('memories',{updateUrl:false});
    const url=routeUrlFor('memories'); url.searchParams.set('memory',rid);
    history.pushState({view:'memories',memory:rid},'',url);
  }
}
function closeMemoryDetail(opts={}){
  $('#memoryDrawer')?.classList.add('hidden');
  state.selectedMemory=null;
  if(!opts.silent){
    $$('#memoryList .memory-item').forEach(x=>x.classList.remove('active'));
    if(state.view==='memories' && new URLSearchParams(location.search).has('memory')) writeRoute('memories',{replace:true});
  }
}
function detailHtml(m){
  const title=esc(m.domain||m.type||'Memory');
  const status=esc(m.consolidation_status||'active');
  const rid=esc(m.rid||'');
  return `<section class="memory-detail-shell">
    <div class="memory-detail-kicker">${esc(m.type||'semantic')} · ${status}</div>
    <h2 class="memory-detail-title">${title}</h2>
    <div class="detail-content">${esc(m.text)}</div>
    <div class="drawer-actions">
      <button class="drawer-action" onclick="showSelectableCopy('RID','${rid}')">Copy RID</button>
      <button class="drawer-action danger" onclick="forgetSelected('${rid}')">Forget memory</button>
    </div>
    <div class="diag-grid">
      ${diag('RID', rid, true)}${diag('Namespace', m.namespace, true)}${diag('Source', m.source)}${diag('Type', m.type)}${diag('Importance', Number(m.importance||0).toFixed(3))}${diag('Certainty', Number(m.certainty||0).toFixed(3))}${diag('Access count', m.access_count)}${diag('Created', m.created_at_iso)}${diag('Updated', m.updated_at_iso)}${diag('Embedding bytes', m.embedding_bytes)}
    </div>
    ${arrSection('Metadata', m.metadata_json||{})}${arrSection('Entities',m.entities)}${arrSection('Claims',m.claims)}${arrSection('Consolidation',m.consolidation_sources)}
  </section>`;
}
function diag(k,v,copy=false){ const val=fmt(v); return `<div class="diag-row"><span>${esc(k)}</span><strong>${copy?`<button class="diag-link" onclick="showSelectableCopy('${esc(k)}','${esc(val)}')">${esc(val)}</button>`:esc(val)}</strong></div>`; }
function kv(k,v){return `<div>${esc(k)}</div><div>${esc(fmt(v))}</div>`}
function arrSection(title, arr){ if(!arr||(Array.isArray(arr)&&!arr.length)||(typeof arr==='object'&&!Array.isArray(arr)&&!Object.keys(arr).length))return ''; return `<div class="detail-section"><div class="label">${title}</div><pre>${esc(JSON.stringify(arr,null,2))}</pre></div>`; }
function showSelectableCopy(label,value){ const text=String(value||''); if(navigator.clipboard?.writeText){ navigator.clipboard.writeText(text).then(()=>toast(`${label} copied`)).catch(()=>toast(text)); } else { toast(text); } }
function confirmAction(title,message){ return new Promise(resolve=>{ const modal=$('#confirmModal'); $('#confirmTitle').textContent=title; $('#confirmMessage').textContent=message; modal.classList.remove('hidden'); const done=v=>{modal.classList.add('hidden'); $('#confirmOk').onclick=null; $('#confirmCancel').onclick=null; resolve(v);}; $('#confirmOk').onclick=()=>done(true); $('#confirmCancel').onclick=()=>done(false); modal.onclick=e=>{ if(e.target===modal) done(false); }; }); }
async function forgetSelected(rid){ if(!await confirmAction('Forget this memory?','This removes the memory from active recall and keeps an audit record. Admin Mode must be enabled.'))return; try{ const out=await api(`/api/memory/${encodeURIComponent(rid)}/forget`,{method:'POST',body:'{}'}); toast('Memory forgotten'); await loadMemories(); $('#memoryDrawerBody').innerHTML=`<pre>${esc(JSON.stringify(out,null,2))}</pre>`;}catch(e){toast(e.message)} }

async function runRecall(){ const body={query:$('#recallQuery').value,top_k:Number($('#recallTopK').value||10),namespace:ns(),domain:$('#recallDomain').value||null,source:$('#recallSource').value||null,include_consolidated:$('#recallConsolidated').checked,expand_entities:$('#recallGraph').checked}; const data=await api('/api/recall',{method:'POST',body:JSON.stringify(body)}); const results=data.results||data.items||[]; $('#recallResults').innerHTML=`<div class="panel"><div class="panel-head"><h2>Results</h2><span class="muted">${fmt(results.length)} hits</span></div></div>`+results.map((r,i)=>`<div class="recall-card"><div class="panel-head"><h2>#${i+1} ${esc(r.domain||r.type||'memory')}</h2><div class="recall-score">${Number(r.score||r.similarity||0).toFixed(3)}</div></div><p>${esc(r.text||r.content||JSON.stringify(r).slice(0,500))}</p><div class="meta-row">${(r.why_retrieved||r.reasons||[]).map(x=>`<span class="pill good">${esc(x)}</span>`).join('')}<span class="pill">${esc(r.rid||'')}</span></div><pre>${esc(JSON.stringify(r,null,2))}</pre></div>`).join('') || `<div class="empty">No recall results.</div>`; }

async function loadConflicts(){ const data=await api(`/api/conflicts?namespace=${encodeURIComponent(ns())}&status=${encodeURIComponent($('#conflictStatus').value)}`); $('#conflictList').innerHTML=(data.items||[]).map(c=>`<div class="memory-item" data-id="${esc(c.conflict_id||c.id)}"><div class="memory-title">${esc(c.conflict_type||c.type||'conflict')} <span class="muted">${esc(c.status||'open')}</span></div><div class="memory-text">${esc(c.description||c.summary||JSON.stringify(c).slice(0,220))}</div><div class="meta-row"><span class="pill warn">priority ${esc(c.priority||'—')}</span><span class="pill">${esc(c.entity||'')}</span></div></div>`).join('') || '<div class="empty">No conflicts.</div>'; $$('#conflictList .memory-item').forEach(el=>el.onclick=()=>selectConflict(el.dataset.id)); }
async function selectConflict(id){ const c=await api('/api/conflicts/'+encodeURIComponent(id)); $('#conflictDetail').innerHTML=`<div class="detail"><h2>Conflict ${esc(id)}</h2><pre>${esc(JSON.stringify(c,null,2))}</pre><div class="detail-section"><div class="label">Resolve</div><div class="toolbar compact"><select id="resolveStrategy"><option value="dismiss">dismiss</option><option value="keep_winner">keep_winner</option><option value="merge">merge</option><option value="keep_both">keep_both</option></select><input id="winnerRid" placeholder="winner rid"><input id="newText" placeholder="merged text"><button class="btn primary" onclick="resolveConflict('${esc(id)}')">Resolve</button></div></div></div>`; }
async function resolveConflict(id){ try{ const body={strategy:$('#resolveStrategy').value,winner_rid:$('#winnerRid').value||null,new_text:$('#newText').value||null,resolution_note:'Resolved via YantrikDB for Hermes'}; const out=await api(`/api/conflicts/${encodeURIComponent(id)}/resolve`,{method:'POST',body:JSON.stringify(body)}); $('#conflictDetail').innerHTML=`<pre>${esc(JSON.stringify(out,null,2))}</pre>`; await loadConflicts(); }catch(e){toast(e.message)} }

async function loadEntities(){ const data=await api(`/api/entities?q=${encodeURIComponent($('#entitySearch').value)}&limit=80`); $('#entityList').innerHTML=(data.items||[]).map(e=>`<div class="memory-item" data-name="${esc(e.name||e.entity||'')}"><div class="memory-title">${esc(e.name||e.entity)}</div><div class="meta-row"><span class="pill">${esc(e.entity_type||e.type||'entity')}</span><span class="pill">mentions ${fmt(e.mention_count||e.count||0)}</span></div></div>`).join('') || '<div class="empty helpful-empty"><strong>No entity index yet.</strong><span>Your current YantrikDB has memories, but no populated entity/edge rows. Use Recall for text search until relationships are written.</span></div>'; $$('#entityList .memory-item').forEach(el=>el.onclick=()=>{ $('#graphEntity').value=el.dataset.name; loadGraph(el.dataset.name); }); }
async function loadGraph(entity){ if(!entity){toast('Enter/select entity');return;} const data=await api('/api/graph/'+encodeURIComponent(entity)+`?namespace=${encodeURIComponent(ns())}`); const empty=!(data.nodes?.length||data.edges?.length||data.memories?.length); $('#graphMeta').textContent=empty?`No graph data for ${entity}`:`${data.nodes.length} nodes · ${data.edges.length} edges`; drawGraph(data); $('#graphMemories').innerHTML=empty?'<div class="empty helpful-empty"><strong>No related memories for this entity.</strong><span>This graph only uses explicit YantrikDB entity links/relationship edges. Try Recall if you want normal semantic search.</span></div>':(data.memories||[]).map(memoryItem).join(''); }
function drawGraph(g){ const svg=$('#graphSvg'); svg.innerHTML=''; const w=760,h=420,cx=w/2,cy=h/2; if(!(g.nodes?.length||g.edges?.length||g.memories?.length)){ svg.insertAdjacentHTML('beforeend',`<text class="graph-label graph-empty-label" x="${cx}" y="${cy}" text-anchor="middle">No entity graph data yet</text><text class="graph-label graph-empty-sub" x="${cx}" y="${cy+28}" text-anchor="middle">Use Recall for normal memory search</text>`); return; } const nodes=g.nodes.length?g.nodes:[{id:g.entity,label:g.entity}]; const pos={}; nodes.forEach((n,i)=>{ if(n.id===g.entity||i===0) pos[n.id]=[cx,cy]; else {const a=(i-1)/Math.max(1,nodes.length-1)*Math.PI*2; pos[n.id]=[cx+Math.cos(a)*260, cy+Math.sin(a)*150];}}); g.edges.forEach(e=>{const a=pos[e.source]||[cx,cy], b=pos[e.target]||[cx,cy]; svg.insertAdjacentHTML('beforeend',`<line class="graph-edge" x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}"/>`);}); nodes.forEach(n=>{const p=pos[n.id]; const root=n.id===g.entity||n.label===g.entity; svg.insertAdjacentHTML('beforeend',`<g><circle class="graph-node ${root?'graph-root':''}" cx="${p[0]}" cy="${p[1]}" r="${root?34:24}"></circle><text class="graph-label" x="${p[0]}" y="${p[1]+44}" text-anchor="middle">${esc(String(n.label||n.id).slice(0,22))}</text></g>`);}); }

async function loadLifecycle(){ const [stale,upcoming,patterns,triggers]=await Promise.all([api(`/api/stale?namespace=${encodeURIComponent(ns())}`),api(`/api/upcoming?namespace=${encodeURIComponent(ns())}`),api('/api/patterns'),api('/api/triggers')]); $('#lifecycleCards').innerHTML=[['Stale candidates',stale.items?.length||0],['Upcoming',upcoming.items?.length||0],['Patterns',patterns.items?.length||0],['Triggers',triggers.items?.length||0]].map(m=>`<div class="stat-card"><div class="stat-label">${m[0]}</div><div class="stat-value">${fmt(m[1])}</div></div>`).join(''); $('#staleList').innerHTML=(stale.items||[]).map(memoryItem).join('')||'<div class="empty">No stale memories.</div>'; $('#upcomingList').innerHTML=(upcoming.items||[]).map(memoryItem).join('')||'<div class="empty">No upcoming memories.</div>'; $('#patternList').innerHTML=(patterns.items||[]).map(x=>`<div class="memory-item"><div class="memory-title">${esc(x.pattern_type||x.type||'pattern')}</div><pre>${esc(JSON.stringify(x,null,2))}</pre></div>`).join('')||'<div class="empty">No patterns.</div>'; $('#triggerList').innerHTML=(triggers.items||[]).map(x=>`<div class="memory-item"><div class="memory-title">${esc(x.trigger_type||x.type||'trigger')}</div><pre>${esc(JSON.stringify(x,null,2))}</pre></div>`).join('')||'<div class="empty">No triggers.</div>'; }
async function runThink(){ if(!await confirmAction('Run maintenance pass?','This will run housekeeping on the selected memory scope. Admin Mode must be enabled.'))return; const btn=$('#runThink'); const oldText=btn?.textContent; try{ if(btn){btn.disabled=true;btn.textContent='Running…';} $('#opsDetails')?.setAttribute('open',''); $('#opsOutput').textContent='Running maintenance…'; const out=await api('/api/think',{method:'POST',body:JSON.stringify({run_consolidation:true,run_conflict_scan:true,run_pattern_mining:false,run_personality:false})}); $('#opsOutput').textContent=JSON.stringify(out,null,2); toast('Maintenance complete'); await loadStats(); }catch(e){$('#opsDetails')?.setAttribute('open',''); $('#opsOutput').textContent=e.message; toast(e.message);} finally{ if(btn){btn.disabled=false;btn.textContent=oldText||'Run pass';} } }
async function loadHealthPanel(){ const h=await api('/api/health'); $('#healthPayload').textContent=JSON.stringify(h,null,2); }


// Direct Mnemosyne Three.js visualiser port. Keep this close to mnemosyne-dashboard/static/app.js.
function visualiserResponsiveFill(width, height){
  const w = Math.max(0, Number(width) || 0);
  const h = Math.max(0, Number(height) || 0);
  if(w < 760 || h < 520) return 1;
  const widthFill = Math.max(0, Math.min(1, (w - 760) / 760));
  const heightFill = Math.max(0, Math.min(1, (h - 520) / 360));
  return 1 + Math.min(.22, (widthFill * .16) + (heightFill * .06));
}
function constellationColors(){
  return { light:false, bg:'#050711', star:'#ffd6dd', memory:'#ffa500', text:'rgba(244,248,255,.92)' };
}
function neuralColors(){
  return { light:false, bg:'#050915', star:'#66e8c6', memory:'#ff9b6a', text:'rgba(244,248,255,.92)' };
}
let threeModulePromise = null;
let threeVis = {
  mode: 'constellation', data: null, renderer: null, scene: null, camera: null, group: null,
  nodes: [], edgePairs: [], labels: [], pulses: [], frame: 0, paused: false, panMode: false,
  drag: null, pointer: new Map(), yaw: 0, pitch: 0.32, cameraZ: 780, panX: 0, panY: 0, lastT: 0,
  selectedNode: null
};
function loadThreeModule(){
  if(!threeModulePromise) threeModulePromise = import('/static/vendor/three.module.min.js');
  return threeModulePromise;
}
function threeInspectorDefault(){
  const neural = threeVis.mode === 'neural';
  threeVis.selectedNode = null;
  const insp=$('#threeInspector'); if(insp) insp.innerHTML = neural
    ? `<div class="inspector-kicker">Neural inspector</div><h3>Nothing selected</h3><p class="muted">Pick a neuron hub, memory soma, or synapse to inspect the underlying read-only source.</p>`
    : `<div class="inspector-kicker">Constellation inspector</div><h3>Nothing selected</h3><p class="muted">Pick a star, memory, or link to inspect the underlying read-only source.</p>`;
  hideThreeFullscreenInspector();
}
function hideThreeFullscreenInspector(){
  const overlay = $('#threeFullscreenInspector');
  if(!overlay) return;
  overlay.classList.remove('active');
  overlay.innerHTML = '';
}
function exitFullscreenThen(fn){
  const run = () => { try { fn(); } catch(e) { toast(e.message || String(e)); } };
  if(document.fullscreenElement) document.exitFullscreen().then(run).catch(run);
  else run();
}
function threeInspectorMarkup(node, target='panel'){
  const mode = threeVis.mode === 'neural' ? 'Neural Map 3D' : 'Constellation 3D';
  const overlay = target === 'overlay';
  const memoryId = overlay ? 'threeOverlayMemory' : 'threeMemory';
  const searchId = overlay ? 'threeOverlaySearch' : 'threeSearch';
  const close = overlay ? '<button id="threeOverlayClose" class="btn ghost tiny" aria-label="Close fullscreen inspector">Close</button>' : '';
  const preview = node.preview ? `<p>${esc(node.preview)}</p>` : '';
  return `<div class="inspector-kicker">${mode} · ${esc(node.kind || 'entity')}</div><h3>${esc(node.label)}</h3><p class="muted">${esc(node.category || 'Other')} · ${Number(node.count || 0).toLocaleString()} signal(s) · weight ${Number(node.weight || 0).toFixed(2)}</p>${preview}<div class="inspector-actions">${node.memory_id ? `<button id="${memoryId}" class="btn primary tiny">Open memory</button>` : ''}<button id="${searchId}" class="btn secondary tiny">Search this</button>${close}</div>`;
}
function bindThreeInspectorActions(node, target='panel'){
  const overlay = target === 'overlay';
  const memory = $(overlay ? '#threeOverlayMemory' : '#threeMemory');
  const search = $(overlay ? '#threeOverlaySearch' : '#threeSearch');
  const close = $('#threeOverlayClose');
  if(memory) memory.onclick = () => exitFullscreenThen(() => selectMemory(node.memory_id));
  if(search) search.onclick = () => exitFullscreenThen(() => visualiserSearchNode(node));
  if(close) close.onclick = hideThreeFullscreenInspector;
}
function visualiserSearchNode(node){
  const rid = String(node.memory_id || '').trim();
  const label = String(node.label || '').replace(/^memory:/,'').trim();
  const preview = String(node.preview || '').replace(/\s+/g,' ').trim();
  const q = rid || preview.slice(0,80) || label;
  state.memoryOffset=0;
  setView('memories',{query:{q,status:'all',domain:'',source:'',sort:'created_at'}});
  loadMemories().then(()=>{ if(rid) selectMemory(rid); }).catch(e=>toast(e.message));
}
function inspectThreeNode(node){
  threeVis.selectedNode = node;
  $('#threeInspector').innerHTML = threeInspectorMarkup(node, 'panel');
  bindThreeInspectorActions(node, 'panel');
  const overlay = $('#threeFullscreenInspector');
  if(overlay){
    overlay.innerHTML = threeInspectorMarkup(node, 'overlay');
    overlay.classList.add('active');
    bindThreeInspectorActions(node, 'overlay');
  }
}
function updateThreeUI(){
  $$('.visualiser-tabs button[data-three-mode]').forEach(b => b.classList.toggle('active', b.dataset.threeMode === threeVis.mode));
  const viewport = $('#threeViewport'); if(viewport) viewport.dataset.threeMode = threeVis.mode;
  const legend = $('#threeLegend');
  if(legend) legend.innerHTML = threeVis.mode === 'neural'
    ? '<span><i class="legend-dot entity"></i>Neuron hub</span><span><i class="legend-dot memory"></i>Memory soma</span><span><i class="legend-line"></i>Synapse</span>'
    : '<span><i class="legend-dot entity"></i>Entity/topic</span><span><i class="legend-dot memory"></i>Memory</span><span><i class="legend-line"></i>Link</span>';
  const help = $('#threeHelp'); if(help) help.textContent = 'Drag to rotate · Right-click/Shift+drag to pan · Wheel/pinch to zoom.';
  const compact = window.matchMedia('(max-width: 760px)').matches;
  const pause = $('#threePause'); if(pause) pause.textContent = threeVis.paused ? (compact ? 'Resume' : (threeVis.mode === 'neural' ? 'Resume drift' : 'Resume rotation')) : (compact ? 'Pause' : (threeVis.mode === 'neural' ? 'Pause drift' : 'Pause rotation'));
  const pan = $('#threePanMode'); if(pan) pan.textContent = threeVis.panMode ? (compact ? 'Orbit' : 'Orbit mode') : (compact ? 'Pan' : 'Pan mode');
}
function resetThreeCamera(){ Object.assign(threeVis, { yaw: threeVis.mode === 'neural' ? .12 : .70, pitch: threeVis.mode === 'neural' ? .10 : .96, cameraZ: threeVis.mode === 'neural' ? 600 : 760, panX:0, panY: threeVis.mode === 'neural' ? -10 : -84, lastT:0 }); }
function clearThreeScene(){
  if(threeVis.frame) cancelAnimationFrame(threeVis.frame);
  threeVis.frame = 0;
  if(threeVis.renderer){ threeVis.renderer.dispose(); threeVis.renderer.domElement.remove(); }
  $('#threeLabels').innerHTML = '';
  Object.assign(threeVis, { renderer:null, scene:null, camera:null, group:null, nodes:[], edgePairs:[], labels:[], pulses:[] });
}
function cssHexToInt(hex){
  const m = String(hex || '').match(/^#([0-9a-f]{6})$/i);
  return m ? parseInt(m[1], 16) : 0xffffff;
}
function colorForTheme(){
  const c = threeVis.mode === 'neural' ? neuralColors() : constellationColors();
  return {
    bg: cssHexToInt(c.bg),
    entity: cssHexToInt(c.star),
    memory: cssHexToInt(c.memory),
    link: cssHexToInt(threeVis.mode === 'neural' ? (c.light ? '#127464' : '#52d6b5') : (c.light ? '#19416c' : '#c6e0ff')),
    pulse: cssHexToInt(threeVis.mode === 'neural' ? (c.light ? '#6f6048' : '#fffaf0') : c.memory),
    text: c.text,
    light: c.light
  };
}
function threeRenderPixelRatio(viewport){
  const rect = viewport?.getBoundingClientRect?.() || {width:650,height:650};
  const base = Math.max(1, window.devicePixelRatio || 1);
  const fullscreen = document.fullscreenElement === viewport || viewport?.matches?.(':fullscreen');
  const mobile = rect.width < 520;
  const qualityBoost = fullscreen ? 1.45 : 1.35;
  const cap = fullscreen ? 3.5 : (mobile ? 2.75 : 3.25);
  const maxPixels = fullscreen ? 12000000 : (mobile ? 3000000 : 6500000);
  const areaCap = Math.sqrt(maxPixels / Math.max(1, rect.width * rect.height));
  return Math.max(1, Math.min(base * qualityBoost, cap, areaCap));
}
function makePointTexture(THREE, kind){
  const canvas = document.createElement('canvas');
  canvas.width = 256; canvas.height = 256;
  const ctx = canvas.getContext('2d');
  ctx.scale(2,2);
  const cx=64, cy=64;
  if(kind === 'star'){
    const g=ctx.createRadialGradient(cx,cy,0,cx,cy,60);
    g.addColorStop(0,'rgba(255,255,255,1)');
    g.addColorStop(.28,'rgba(255,255,255,.92)');
    g.addColorStop(.58,'rgba(255,255,255,.38)');
    g.addColorStop(1,'rgba(255,255,255,0)');
    ctx.fillStyle=g; ctx.beginPath(); ctx.arc(cx,cy,60,0,Math.PI*2); ctx.fill();
    ctx.strokeStyle='rgba(255,255,255,.72)'; ctx.lineWidth=1.3;
    ctx.beginPath(); ctx.moveTo(cx,14); ctx.lineTo(cx,114); ctx.moveTo(14,cy); ctx.lineTo(114,cy); ctx.stroke();
  } else if(kind === 'neuron') {
    const g=ctx.createRadialGradient(cx,cy,0,cx,cy,62);
    g.addColorStop(0,'rgba(255,255,255,1)');
    g.addColorStop(.13,'rgba(255,255,255,.94)');
    g.addColorStop(.42,'rgba(255,255,255,.28)');
    g.addColorStop(1,'rgba(255,255,255,0)');
    ctx.fillStyle=g; ctx.beginPath(); ctx.arc(cx,cy,61,0,Math.PI*2); ctx.fill();
    ctx.strokeStyle='rgba(255,255,255,.70)'; ctx.lineCap='round'; ctx.lineJoin='round';
    for(let i=0;i<9;i++){
      const a=(i/9)*Math.PI*2 + .13;
      const len=22 + (i%4)*4;
      const fork=len*.62;
      const sx=cx+Math.cos(a)*13, sy=cy+Math.sin(a)*13;
      const mx=cx+Math.cos(a+.10*Math.sin(i))*fork, my=cy+Math.sin(a+.10*Math.sin(i))*fork;
      const ex=cx+Math.cos(a)*len, ey=cy+Math.sin(a)*len;
      ctx.lineWidth=i%3===0?2.25:1.45;
      ctx.beginPath(); ctx.moveTo(sx,sy); ctx.quadraticCurveTo(mx,my,ex,ey); ctx.stroke();
      ctx.lineWidth=.9;
      ctx.globalAlpha=.72;
      ctx.beginPath(); ctx.moveTo(mx,my); ctx.lineTo(cx+Math.cos(a+.38)*len*.66,cy+Math.sin(a+.38)*len*.66); ctx.stroke();
      if(i%3===0){ ctx.beginPath(); ctx.moveTo(mx,my); ctx.lineTo(cx+Math.cos(a-.34)*len*.60,cy+Math.sin(a-.34)*len*.60); ctx.stroke(); }
      ctx.globalAlpha=1;
    }
    ctx.fillStyle='rgba(255,255,255,.98)'; ctx.beginPath(); ctx.arc(cx,cy,34,0,Math.PI*2); ctx.fill();
    ctx.fillStyle='rgba(255,255,255,.54)'; ctx.beginPath(); ctx.arc(cx-8,cy-9,8,0,Math.PI*2); ctx.fill();
  } else if(kind === 'soma') {
    const g=ctx.createRadialGradient(cx,cy,0,cx,cy,62);
    g.addColorStop(0,'rgba(255,255,255,1)');
    g.addColorStop(.18,'rgba(255,255,255,.96)');
    g.addColorStop(.42,'rgba(255,255,255,.34)');
    g.addColorStop(1,'rgba(255,255,255,0)');
    ctx.fillStyle=g; ctx.beginPath(); ctx.arc(cx,cy,62,0,Math.PI*2); ctx.fill();
    ctx.strokeStyle='rgba(255,255,255,.68)'; ctx.lineCap='round'; ctx.lineWidth=1.55;
    for(let i=0;i<5;i++){
      const a=(i/5)*Math.PI*2+.22, len=21+(i%2)*4;
      ctx.beginPath(); ctx.moveTo(cx+Math.cos(a)*18,cy+Math.sin(a)*18); ctx.lineTo(cx+Math.cos(a)*len,cy+Math.sin(a)*len); ctx.stroke();
    }
    ctx.lineWidth=3.4; ctx.beginPath(); ctx.arc(cx,cy,40,0,Math.PI*2); ctx.stroke();
    ctx.fillStyle='rgba(255,255,255,1)'; ctx.beginPath(); ctx.arc(cx,cy,35,0,Math.PI*2); ctx.fill();
  } else {
    const g=ctx.createRadialGradient(cx,cy,0,cx,cy,60);
    g.addColorStop(0,'rgba(255,255,255,1)');
    g.addColorStop(.44,'rgba(255,255,255,.82)');
    g.addColorStop(.78,'rgba(255,255,255,.22)');
    g.addColorStop(1,'rgba(255,255,255,0)');
    ctx.fillStyle=g; ctx.beginPath(); ctx.arc(cx,cy,60,0,Math.PI*2); ctx.fill();
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.needsUpdate = true;
  return tex;
}
function buildThreePositions(data){
  if(threeVis.mode === 'neural') return buildThreeNeuralPositions(data);
  const nodes = (data.nodes || []).slice(0,160).map(n => ({...n}));
  const categories = [...new Set(nodes.map(n => n.category || 'Other'))];
  const catIndex = Object.fromEntries(categories.map((c,i)=>[c,i]));
  nodes.forEach((n,i) => {
    const cat = n.category || 'Other';
    const ci = catIndex[cat] || 0;
    const weight = Math.max(1, Number(n.weight || n.count || 1));
    const shell = n.kind === 'memory' ? 1.12 : .74 + (ci % 3) * .10;
    const radius = 285 * shell + (i % 7) * 18 + Math.min(46, Math.sqrt(weight) * 5.5);
    const longitude = ((i * 137.508 + ci * 23) % 360) * Math.PI / 180;
    const latitudeSeed = (((i * 53 + ci * 29) % 101) + .5) / 101;
    const latitude = Math.acos(1 - 2 * latitudeSeed) - Math.PI / 2;
    const radial = Math.cos(latitude);
    const orbitBias = Math.sin((i / Math.max(nodes.length,1)) * Math.PI * 2 + ci * .62) * 22;
    n.x = Math.cos(longitude) * radial * radius;
    n.y = Math.sin(latitude) * radius * .92 + orbitBias;
    n.z = Math.sin(longitude) * radial * radius * 1.12 + Math.cos(longitude * 1.7 + ci) * 54;
    const sizeJitter = 1 + (((i * 37) % 11) - 5) * .035;
    n.size = Math.min(42, 9 + Math.sqrt(weight)*6.2 + (n.kind === 'memory' ? 3.5 : 4.5)) * sizeJitter;
    n.twinkle = (i % 23) / 23;
    const twinkleTier = i % 17 === 0 ? 2 : (i % 5 === 0 ? 1 : 0);
    n.twinkleFreq = twinkleTier === 2 ? .0048 + ((i * 41) % 130) / 100000 : (twinkleTier === 1 ? .0024 + ((i * 47) % 120) / 100000 : .00125 + ((i * 53) % 110) / 100000);
    n.twinkleAmp = twinkleTier === 2 ? .34 : (twinkleTier === 1 ? .24 : .15 + ((i * 29) % 70) / 1000);
    n._degree = 0; n._weight = weight;
  });
  return nodes;
}
function buildThreeNeuralPositions(data){
  const nodes = (data.nodes || []).slice(0,170).map(n => ({...n}));
  const nodeIds = new Set(nodes.map(n => n.id));
  const edges = (data.edges || []).filter(e => nodeIds.has(e.source) && nodeIds.has(e.target)).slice(0,340);
  const categories = [...new Set(nodes.map(n => n.category || 'Other'))];
  const catIndex = Object.fromEntries(categories.map((c,i)=>[c,i]));
  const regionCount = Math.max(1, categories.length);
  const regions = Object.fromEntries(categories.map((cat, i) => {
    const angle = -Math.PI / 2 + (i / regionCount) * Math.PI * 2;
    const radius = regionCount <= 2 ? 86 : (i === regionCount - 1 && regionCount > 5 ? 70 : 142 + (i % 2) * 18);
    const lap = Math.floor(i / Math.max(1, regionCount));
    return [cat, {
      label:cat,
      angle,
      cx:Math.cos(angle) * radius + lap * 18,
      cy:Math.sin(angle) * radius * .96,
      cz:((i * 41) % 89 - 44) * .72,
      spread:94 + (i % 4) * 10
    }];
  }));
  const degree = new Map();
  edges.forEach(e => { degree.set(e.source, (degree.get(e.source) || 0) + 1); degree.set(e.target, (degree.get(e.target) || 0) + 1); });
  const hubsByCategory = {};
  nodes.filter(n => n.kind !== 'memory').sort((a,b)=>(Number(b.weight || b.count || 0)+ (degree.get(b.id)||0)) - (Number(a.weight || a.count || 0)+(degree.get(a.id)||0))).forEach(n => {
    const cat = n.category || 'Other'; if(!hubsByCategory[cat]) hubsByCategory[cat] = []; hubsByCategory[cat].push(n);
  });
  const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
  nodes.forEach((n,i) => {
    const cat = n.category || 'Other';
    const region = regions[cat] || regions.Other || { cx:0, cy:0, cz:0, angle:0, spread:80 };
    const ci = catIndex[cat] || 0;
    const weight = Math.max(1, Number(n.weight || n.count || 1));
    const d = degree.get(n.id) || 0;
    if(n.kind === 'memory'){
      const linked = edges.find(e => e.source === n.id || e.target === n.id);
      const parent = linked ? byId[linked.source === n.id ? linked.target : linked.source] : null;
      const parentX = parent && parent.kind !== 'memory' && Number.isFinite(parent.x) ? parent.x : region.cx;
      const parentY = parent && parent.kind !== 'memory' && Number.isFinite(parent.y) ? parent.y : region.cy;
      const parentZ = parent && parent.kind !== 'memory' && Number.isFinite(parent.z) ? parent.z : region.cz;
      const branch = ((i * 137.508 + ci * 19) % 360) * Math.PI / 180;
      const yUnit = ((((i * 43 + ci * 17) % 97) + .5) / 97) * 2 - 1;
      const radial = Math.sqrt(Math.max(0, 1 - yUnit * yUnit));
      const dist = 46 + (i % 6) * 13 + Math.min(48, Math.sqrt(weight) * 10);
      n.x = parentX + Math.cos(branch) * radial * dist;
      n.y = parentY + yUnit * dist * .82;
      n.z = parentZ + Math.sin(branch) * radial * dist * .86;
    } else {
      const rank = Math.max(0, (hubsByCategory[cat] || []).indexOf(n));
      const orbit = rank === 0 ? 0 : 30 + Math.sqrt(rank) * 20;
      const angle = region.angle + rank * 2.399963 + (ci % 3) * .24;
      const yUnit = rank === 0 ? 0 : ((((rank * 37 + ci * 11) % 89) + .5) / 89) * 2 - 1;
      const radial = Math.sqrt(Math.max(0, 1 - yUnit * yUnit));
      n.x = region.cx + Math.cos(angle) * radial * orbit;
      n.y = region.cy + yUnit * orbit * .86;
      n.z = region.cz + Math.sin(angle) * radial * orbit * .80;
    }
    n.size = Math.min(30, 8 + Math.sqrt(weight + d) * (n.kind === 'memory' ? 3.2 : 4.1));
    n._degree = d; n._weight = weight; n.neuralRegion = cat;
  });
  threeVis.neuralRegions = Object.values(regions);
  return nodes;
}
function limitedThreeEdges(data, byId, mobile=false){
  const degree = new Map(); const out=[];
  const limit = threeVis.mode === 'neural' ? 132 : (mobile ? 92 : 140);
  const degreeLimit = threeVis.mode === 'neural' ? 5 : (mobile ? 3 : 4);
  for(const e of (data.edges || [])){
    const a=byId.get(e.source), b=byId.get(e.target); if(!a || !b) continue;
    const da=degree.get(e.source)||0, db=degree.get(e.target)||0; if(da>=degreeLimit || db>=degreeLimit) continue;
    degree.set(e.source, da+1); degree.set(e.target, db+1); a._degree++; b._degree++; out.push({ ...e, a, b });
    if(out.length >= limit) break;
  }
  return out;
}
function neuralAuraOverlay(regions){
  if(threeVis.mode !== 'neural') return '';
  const regionList = (regions || []).slice(0,9);
  return `<div class="three-aura-layer">${regionList.map((r,i)=>`<span class="three-aura-oval" data-region="${esc(r.label || '')}" style="opacity:0;transform:translate(-50%,-50%) rotate(${(Number(r.angle || 0) * 28).toFixed(1)}deg)"></span>`).join('')}</div>`;
}
function makeAuraOvalTexture(THREE){
  const canvas = document.createElement('canvas'); canvas.width = 1024; canvas.height = 640;
  const ctx = canvas.getContext('2d'); ctx.scale(2,2); const cx=256, cy=160;
  const g = ctx.createRadialGradient(cx, cy, 12, cx, cy, 230);
  g.addColorStop(0, 'rgba(102,232,198,.24)');
  g.addColorStop(.52, 'rgba(102,232,198,.13)');
  g.addColorStop(1, 'rgba(102,232,198,0)');
  ctx.fillStyle = g;
  ctx.beginPath(); ctx.ellipse(cx, cy, 238, 142, 0, 0, Math.PI*2); ctx.fill();
  ctx.strokeStyle='rgba(102,232,198,.16)'; ctx.lineWidth=2;
  for(let i=0;i<3;i++){
    ctx.beginPath(); ctx.ellipse(cx, cy, 88+i*48, 46+i*28, 0, 0, Math.PI*2); ctx.stroke();
  }
  const tex = new THREE.CanvasTexture(canvas); tex.needsUpdate = true; return tex;
}
function addNeuralAuraOvals(THREE, group, regions, colors){
  const texture = makeAuraOvalTexture(THREE);
  (regions || []).slice(0,10).forEach((region, i) => {
    const material = new THREE.SpriteMaterial({ map:texture, color:colors.entity, transparent:true, opacity:colors.light ? .13 : .18, depthWrite:false, depthTest:false, blending:THREE.AdditiveBlending, rotation:(region.angle || 0) * .42 });
    const sprite = new THREE.Sprite(material);
    sprite.position.set(region.cx || 0, region.cy || 0, (region.cz || 0) - 18 - i*.8);
    const spread = region.spread || 86;
    sprite.scale.set(spread * (3.9 + (i%3)*.35), spread * (2.35 + (i%2)*.22), 1);
    sprite.renderOrder = -10 + i;
    group.add(sprite);
  });
}
function addHaloPoints(THREE, scene, nodes, kind, color, size){
  let selected = nodes.filter(n => (n.kind === 'memory') === (kind === 'memory'));
  if(threeVis.mode !== 'neural'){
    selected = selected
      .filter(n => {
        const weight = Math.max(1, Number(n.weight || n.count || 1));
        return weight > (kind === 'memory' ? 3.6 : 4.4) || Number(n._degree || 0) > 3;
      })
      .sort((a,b)=>(Math.max(1, Number(b.weight || b.count || 1)) + Number(b._degree || 0)) - (Math.max(1, Number(a.weight || a.count || 1)) + Number(a._degree || 0)))
      .slice(0, kind === 'memory' ? 30 : 44);
  }
  const positions = new Float32Array(selected.length * 3);
  selected.forEach((n,i)=>{ positions[i*3]=n.x; positions[i*3+1]=n.y; positions[i*3+2]=n.z; });
  const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.BufferAttribute(positions,3));
  const themeColors = colorForTheme();
  const isNeural = threeVis.mode === 'neural';
  const opacity = isNeural
    ? (kind === 'memory' ? (themeColors.light ? .16 : .28) : (themeColors.light ? .18 : .34))
    : (kind === 'memory' ? (themeColors.light ? .12 : .24) : (themeColors.light ? .13 : .26));
  const material = new THREE.PointsMaterial({ color, map:makePointTexture(THREE, 'orb'), alphaTest:.015, size, sizeAttenuation:true, transparent:true, opacity, depthWrite:false, blending:themeColors.light ? THREE.NormalBlending : THREE.AdditiveBlending });
  const points = new THREE.Points(geometry, material); scene.add(points); return points;
}
function addNeuralDendrites(THREE, group, nodes, colors){
  const trunks=[]; const twigs=[]; const tips=[];
  nodes.slice(0,150).forEach((n,i)=>{
    const arms = n.kind === 'memory' ? 3 : 6;
    const base = n.kind === 'memory' ? 10 : 17;
    for(let a=0;a<arms;a++){
      const theta=(a/arms)*Math.PI*2 + (i%11)*.19;
      const phi=Math.sin(i*.37+a)*.58;
      const len=base + ((i+a*13)%9);
      const mid=[n.x+Math.cos(theta+.16)*Math.cos(phi)*len*.50, n.y+Math.sin(phi)*len*.36, n.z+Math.sin(theta+.16)*Math.cos(phi)*len*.50];
      const end=[n.x+Math.cos(theta)*Math.cos(phi)*len*.78, n.y+Math.sin(phi)*len*.54, n.z+Math.sin(theta)*Math.cos(phi)*len*.78];
      trunks.push(n.x,n.y,n.z, mid[0],mid[1],mid[2], mid[0],mid[1],mid[2], end[0],end[1],end[2]);
      if(n.kind !== 'memory' && a%2===0){
        const side=theta+(a%2?.44:-.40);
        const fork=[mid[0]+Math.cos(side)*len*.18, mid[1]+Math.sin(phi+.25)*len*.12, mid[2]+Math.sin(side)*len*.18];
        twigs.push(mid[0],mid[1],mid[2], fork[0],fork[1],fork[2]);
      }
      if(i%3===0 && a%2===0) tips.push(end[0],end[1],end[2]);
    }
  });
  const trunkGeom = new THREE.BufferGeometry(); trunkGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(trunks),3));
  group.add(new THREE.LineSegments(trunkGeom, new THREE.LineBasicMaterial({ color:colors.entity, transparent:true, opacity:colors.light ? .34 : .36, blending:colors.light ? THREE.NormalBlending : THREE.AdditiveBlending, depthWrite:false })));
  const twigGeom = new THREE.BufferGeometry(); twigGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(twigs),3));
  group.add(new THREE.LineSegments(twigGeom, new THREE.LineBasicMaterial({ color:colors.link, transparent:true, opacity:colors.light ? .28 : .24, blending:colors.light ? THREE.NormalBlending : THREE.AdditiveBlending, depthWrite:false })));
  const tipGeom = new THREE.BufferGeometry(); tipGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(tips),3));
  group.add(new THREE.Points(tipGeom, new THREE.PointsMaterial({ color:colors.entity, map:makePointTexture(THREE, 'orb'), alphaTest:.03, size:3.8, transparent:true, opacity:colors.light ? .54 : .72, depthWrite:false, blending:colors.light ? THREE.NormalBlending : THREE.AdditiveBlending })));
}
function addPoints(THREE, scene, nodes, kind, color, size){
  const selected = nodes.filter(n => (n.kind === 'memory') === (kind === 'memory'));
  const positions = new Float32Array(selected.length * 3);
  const sizes = new Float32Array(selected.length);
  const phases = new Float32Array(selected.length);
  const freqs = new Float32Array(selected.length);
  const amps = new Float32Array(selected.length);
  const majors = new Float32Array(selected.length);
  selected.forEach((n,i)=>{
    const weight = Math.max(1, Number(n.weight || n.count || 1));
    positions[i*3]=n.x; positions[i*3+1]=n.y; positions[i*3+2]=n.z;
    const degreeBoost = Math.min(10, Number(n._degree || 0) * 1.9);
    const variedSize = (n.size || size) + degreeBoost;
    sizes[i]=Math.max(size * 1.14, Math.min(size * 2.65, variedSize * 1.62));
    phases[i]=(n.twinkle || 0) * Math.PI * 2;
    freqs[i]=n.twinkleFreq || .0012;
    amps[i]=n.twinkleAmp || .12;
    majors[i]=weight > 6.2 || (kind === 'memory' && weight > 4.8) ? 1 : 0;
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions,3));
  geometry.setAttribute('aSize', new THREE.BufferAttribute(sizes,1));
  geometry.setAttribute('aPhase', new THREE.BufferAttribute(phases,1));
  geometry.setAttribute('aFreq', new THREE.BufferAttribute(freqs,1));
  geometry.setAttribute('aAmp', new THREE.BufferAttribute(amps,1));
  geometry.setAttribute('aMajor', new THREE.BufferAttribute(majors,1));
  const themeColors = colorForTheme();
  let material;
  if(threeVis.mode === 'neural'){
    material = new THREE.PointsMaterial({ color, map:makePointTexture(THREE, kind === 'memory' ? 'soma' : 'neuron'), alphaTest:.04, size, sizeAttenuation:true, transparent:true, opacity:kind === 'memory' ? (themeColors.light ? .88 : .98) : (themeColors.light ? .76 : .86), depthWrite:false, blending:themeColors.light ? THREE.NormalBlending : THREE.AdditiveBlending });
  } else {
    material = new THREE.ShaderMaterial({
      uniforms:{
        uTime:{value:0},
        uScale:{value:420},
        uColor:{value:new THREE.Color(color)},
        uIsStar:{value:kind === 'memory' ? 0 : 1},
        uOpacity:{value:kind === 'memory' ? .98 : .96}
      },
      vertexShader:`
        attribute float aSize;
        attribute float aPhase;
        attribute float aFreq;
        attribute float aAmp;
        attribute float aMajor;
        uniform float uTime;
        uniform float uScale;
        varying float vPulse;
        varying float vMajor;
        void main(){
          vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
          float wave = sin(uTime * aFreq + aPhase) + sin(uTime * aFreq * 0.43 + aPhase * 1.71) * 0.45;
          vPulse = 1.0 + wave * aAmp;
          vMajor = aMajor;
          gl_PointSize = aSize * (0.98 + (vPulse - 1.0) * 0.32) * (uScale / max(72.0, -mvPosition.z));
          gl_Position = projectionMatrix * mvPosition;
        }
      `,
      fragmentShader:`
        uniform vec3 uColor;
        uniform float uIsStar;
        uniform float uOpacity;
        varying float vPulse;
        varying float vMajor;
        void main(){
          vec2 p = gl_PointCoord - vec2(0.5);
          float d = length(p);
          if(d > 0.5) discard;
          float core = 1.0 - smoothstep(0.026, 0.060, d);
          float body = 1.0 - smoothstep(0.060, 0.135, d);
          float halo = (1.0 - smoothstep(0.13, 0.48, d)) * (0.15 + clamp(vPulse - 1.0, -0.30, 0.46) * 0.82);
          float rayH = max(0.0, 1.0 - abs(p.y) / 0.010) * (1.0 - smoothstep(0.07, 0.44, abs(p.x)));
          float rayV = max(0.0, 1.0 - abs(p.x) / 0.010) * (1.0 - smoothstep(0.07, 0.44, abs(p.y)));
          float diag1 = max(0.0, 1.0 - abs(p.x - p.y) / 0.013) * (1.0 - smoothstep(0.06, 0.26, d));
          float diag2 = max(0.0, 1.0 - abs(p.x + p.y) / 0.013) * (1.0 - smoothstep(0.06, 0.26, d));
          float rays = vMajor * (max(rayH, rayV) * 0.50 + max(diag1, diag2) * 0.16);
          float alpha = (body * 0.46 + core * 1.02 + halo + rays) * uOpacity * clamp(0.72 + (vPulse - 1.0) * 0.92, 0.46, 1.35);
          if(alpha < 0.022) discard;
          vec3 starCore = mix(uColor, vec3(1.0), core * 0.88 + rays * 0.38);
          vec3 memoryCore = mix(uColor, vec3(1.0), core * 0.34);
          vec3 crisp = mix(memoryCore, starCore, uIsStar);
          gl_FragColor = vec4(crisp * (0.92 + (vPulse - 1.0) * 0.22), min(alpha, 1.0));
        }
      `,
      transparent:true,
      depthWrite:false,
      blending:THREE.NormalBlending
    });
  }
  const points = new THREE.Points(geometry, material); points.userData.nodes = selected; scene.add(points); return points;
}
function buildThreeLinkSegments(THREE, edges){
  const positions = [];
  edges.forEach((e,i)=>{
    if(threeVis.mode === 'neural'){
      const ax=e.a.x, ay=e.a.y, az=e.a.z, bx=e.b.x, by=e.b.y, bz=e.b.z;
      const dx=bx-ax, dy=by-ay, dz=bz-az;
      const len=Math.max(1, Math.hypot(dx,dy,dz));
      const bend=(i%2?1:-1) * Math.min(58, 18 + len*.12);
      const cx=(ax+bx)/2 + (-dy/len)*bend;
      const cy=(ay+by)/2 + (dx/len)*bend*.55 + Math.sin(i*.71)*18;
      const cz=(az+bz)/2 + Math.cos(i*.53)*bend*.72;
      e._curve={cx,cy,cz};
      let px=ax, py=ay, pz=az;
      for(let step=1; step<=7; step++){
        const t=step/7, inv=1-t;
        const x=inv*inv*ax+2*inv*t*cx+t*t*bx;
        const y=inv*inv*ay+2*inv*t*cy+t*t*by;
        const z=inv*inv*az+2*inv*t*cz+t*t*bz;
        positions.push(px,py,pz,x,y,z); px=x; py=y; pz=z;
      }
    } else {
      positions.push(e.a.x,e.a.y,e.a.z,e.b.x,e.b.y,e.b.z);
    }
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(positions),3));
  return geometry;
}
async function renderThreeVisualiser(data){
  const THREE = await loadThreeModule();
  clearThreeScene(); threeVis.data = data; updateThreeUI(); threeInspectorDefault();
  const viewport = $('#threeViewport'); if(!viewport) return;
  const colors = colorForTheme();
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias:true, alpha:true, powerPreference:'high-performance', precision:'highp', stencil:false });
  } catch(err) {
    $('#threeViewport').classList.add('three-fallback');
    $('#threeLabels').innerHTML = `<div class="three-fallback-card"><h3>3D visualiser unavailable</h3><p>The original Visualiser remains available for this browser.</p></div>`;
    $('#threeInspector').innerHTML = `<div class="inspector-kicker">Constellation inspector</div><h3>3D visualiser unavailable</h3><p class="muted">Try the original Visualiser, or reopen this page in a browser that supports the 3D view.</p>`;
    return;
  }
  $('#threeViewport').classList.remove('three-fallback');
  renderer.setPixelRatio(threeRenderPixelRatio(viewport));
  if('outputColorSpace' in renderer && THREE.SRGBColorSpace) renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(colors.bg, 0);
  viewport.prepend(renderer.domElement);
  const scene = new THREE.Scene(); scene.fog = new THREE.FogExp2(colors.bg, threeVis.mode === 'neural' ? .0011 : .0009);
  const mobileThree = (viewport.getBoundingClientRect?.().width || 650) < 520;
  const camera = new THREE.PerspectiveCamera(48, 1, 1, 5000);
  const group = new THREE.Group(); scene.add(group);
  const ambient = new THREE.AmbientLight(0xffffff, .55); scene.add(ambient);
  const light = new THREE.PointLight(colors.entity, 1.2, 1200); light.position.set(180,220,260); scene.add(light);
  const nodes = buildThreePositions(data); const byId = new Map(nodes.map(n=>[n.id,n])); const edges = limitedThreeEdges(data, byId, mobileThree);
  const linkGeom = buildThreeLinkSegments(THREE, edges);
  const linkMaterial = threeVis.mode === 'neural'
    ? new THREE.LineBasicMaterial({ color:colors.link, transparent:true, opacity:colors.light ? .30 : .40, blending:colors.light ? THREE.NormalBlending : THREE.AdditiveBlending, depthWrite:false })
    : new THREE.LineDashedMaterial({ color:colors.link, transparent:true, opacity:colors.light ? (mobileThree ? .14 : .16) : (mobileThree ? .13 : .12), dashSize:9, gapSize:8, blending:THREE.NormalBlending, depthWrite:false });
  const linkLines = new THREE.LineSegments(linkGeom, linkMaterial);
  if(threeVis.mode !== 'neural') linkLines.computeLineDistances();
  group.add(linkLines);
  if(threeVis.mode === 'neural'){
    addHaloPoints(THREE, group, nodes, 'entity', colors.entity, 50);
    addHaloPoints(THREE, group, nodes, 'memory', colors.memory, 48);
    addNeuralDendrites(THREE, group, nodes, colors);
  } else {
    // Constellation already has per-star shader halos. A separate halo layer made
    // the mobile view read like blurry particles instead of the original star map.
  }
  group.add(addPoints(THREE, group, nodes, 'entity', colors.entity, threeVis.mode === 'neural' ? 30 : 52));
  group.add(addPoints(THREE, group, nodes, 'memory', colors.memory, threeVis.mode === 'neural' ? 26 : 50));
  const starCount = threeVis.mode === 'neural' ? 360 : 420;
  const starPositions = new Float32Array(starCount*3);
  for(let i=0;i<starCount;i++){ const r=600+((i*37)%480), a=i*2.17, b=((i*53)%180-90)*Math.PI/180; starPositions.set([Math.cos(a)*Math.cos(b)*r, Math.sin(b)*r, Math.sin(a)*Math.cos(b)*r], i*3); }
  const starGeom = new THREE.BufferGeometry(); starGeom.setAttribute('position', new THREE.BufferAttribute(starPositions,3));
  scene.add(new THREE.Points(starGeom, new THREE.PointsMaterial({ color:0xffffff, map:makePointTexture(THREE, 'orb'), alphaTest:.04, size:1.25, transparent:true, opacity:threeVis.mode === 'neural' ? .38 : .24, depthWrite:false })));
  const pulseEdges = threeVis.mode === 'neural' ? edges.slice(0, 90) : [];
  const pulseGeom = new THREE.BufferGeometry(); const pulsePositions = new Float32Array(pulseEdges.length*3); pulseGeom.setAttribute('position', new THREE.BufferAttribute(pulsePositions,3));
  const pulsePoints = new THREE.Points(pulseGeom, new THREE.PointsMaterial({ color:colors.pulse, map:makePointTexture(THREE, 'star'), alphaTest:.03, size:threeVis.mode === 'neural' ? 10.5 : 5.2, transparent:true, opacity:threeVis.mode === 'neural' ? (colors.light ? .54 : .98) : .85, depthWrite:false, depthTest:false, blending:colors.light ? THREE.NormalBlending : THREE.AdditiveBlending })); group.add(pulsePoints);
  const labelNodes = nodes.filter(n => !/^[a-f0-9]{10,}$/i.test(String(n.label||''))).sort((a,b)=>(b._degree+b._weight)-(a._degree+a._weight)).slice(0, threeVis.mode === 'neural' ? 72 : 56);
  $('#threeLabels').innerHTML = neuralAuraOverlay(threeVis.neuralRegions) + labelNodes.map((n,i)=>`<span class="three-label ${n.kind === 'memory' ? 'memory' : ''}" data-i="${i}">${esc(String(n.label||'').replace(/^memory:/,'mem ').slice(0,24))}</span>`).join('');
  Object.assign(threeVis, { THREE, renderer, scene, camera, group, nodes, edgePairs:edges, labels:labelNodes, pulses:pulseEdges, pulsePoints });
  $('#threeClusters').innerHTML = (data.clusters || []).map(c => `<span class="cluster-pill">${esc(c.label)} <strong>${Number(c.count).toLocaleString()}</strong></span>`).join('');
  resetThreeCamera(); bindThreeControls(); resizeThree(); animateThree(0);
}
function resizeThree(){
  if(!threeVis.renderer) return;
  const viewport = $('#threeViewport'); const rect = viewport.getBoundingClientRect();
  const w = Math.max(320, rect.width), h = Math.max(320, rect.height);
  threeVis.renderer.setPixelRatio(threeRenderPixelRatio(viewport));
  threeVis.renderer.setSize(w,h,false); threeVis.camera.aspect = w/h; threeVis.camera.updateProjectionMatrix();
}
function threeEffectiveCameraZ(rect){
  const box = rect || $('#threeViewport')?.getBoundingClientRect?.() || {width:650,height:650};
  const fill = visualiserResponsiveFill(box.width, box.height);
  const mobile = box.width < 760 || box.height < 520;
  return threeVis.cameraZ / (mobile ? 1 : fill);
}
function updateThreeAuras(rect, projectVector){
  if(threeVis.mode !== 'neural') return;
  const mobile = rect.width < 520;
  $$('#threeLabels .three-aura-oval').forEach(el => {
    const region = el.dataset.region || '';
    const pts = threeVis.nodes.filter(n => n.neuralRegion === region);
    const screens = [];
    pts.forEach(n => {
      projectVector.set(n.x,n.y,n.z).applyMatrix4(threeVis.group.matrixWorld).project(threeVis.camera);
      if(projectVector.z < 1 && projectVector.z > -1) screens.push({ x:(projectVector.x*.5+.5)*rect.width, y:(-projectVector.y*.5+.5)*rect.height });
    });
    if(screens.length < 2){ el.style.opacity = '0'; return; }
    const xs = screens.map(p=>p.x), ys = screens.map(p=>p.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    const maxW = mobile ? rect.width * .62 : Math.min(340, rect.width * .34);
    const maxH = mobile ? rect.height * .30 : Math.min(230, rect.height * .26);
    const w = Math.max(mobile ? 92 : 128, Math.min(maxW, (maxX - minX) + (mobile ? 46 : 74)));
    const h = Math.max(mobile ? 58 : 78, Math.min(maxH, (maxY - minY) + (mobile ? 34 : 56)));
    el.style.left = `${cx}px`; el.style.top = `${cy}px`; el.style.width = `${w}px`; el.style.height = `${h}px`; el.style.opacity = screens.length > 4 ? '.42' : '.28';
  });
}
function updateThreeLabels(){
  if(!threeVis.camera || !threeVis.group) return;
  const viewport = $('#threeViewport'); const rect = viewport.getBoundingClientRect(); const v = new threeVis.THREE.Vector3();
  updateThreeAuras(rect, v);
  const labelBoxes = [];
  const effectiveCameraZ = threeEffectiveCameraZ(rect);
  const zoomReveal = threeVis.mode === 'neural' ? Math.max(0, Math.min(1, (900 - effectiveCameraZ) / 420)) : Math.max(0, Math.min(1, (760 - effectiveCameraZ) / 520));
  const maxLabels = threeVis.mode === 'neural' ? ((rect.width < 520 ? 14 : 24) + Math.round(zoomReveal * (rect.width < 520 ? 14 : 18))) : (rect.width < 520 ? (12 + Math.round(zoomReveal * 12)) : (20 + Math.round(zoomReveal * 18)));
  let shown = 0;
  $$('#threeLabels .three-label').forEach((el,i)=>{
    const n = threeVis.labels[i]; if(!n) return;
    v.set(n.x,n.y,n.z).applyMatrix4(threeVis.group.matrixWorld).project(threeVis.camera);
    const sx = (v.x*.5+.5)*rect.width, sy = (-v.y*.5+.5)*rect.height;
    const edgePad = rect.width < 520 ? 68 : 8;
    const visible = v.z < 1 && v.z > -1 && sx > edgePad && sx < rect.width - edgePad && sy > 8 && sy < rect.height - 8;
    const pulse = threeVis.mode === 'neural' && i > 3 ? Math.sin((threeVis.lastT || 0) * .00032 + i * 1.73) : 1;
    const box = {x:sx-54,y:sy-13,w:108,h:24};
    const collides = labelBoxes.some(b => !(box.x+box.w<b.x || b.x+b.w<box.x || box.y+box.h<b.y || b.y+b.h<box.y));
    const show = visible && shown < maxLabels && !collides && (threeVis.mode !== 'neural' || i <= 3 || pulse > .08);
    el.style.display = show ? '' : 'none';
    if(show){
      shown++; labelBoxes.push(box);
      el.style.left = `${sx}px`; el.style.top = `${sy}px`;
      const depthAlpha = Math.max(.32, Math.min(.86, 1 - Math.abs(v.z)*.35));
      const pulseAlpha = threeVis.mode === 'neural' && i > 3 ? Math.min(.78, .38 + pulse * .36) : depthAlpha;
      el.style.opacity = String(Math.min(depthAlpha, pulseAlpha));
    }
  });
}
function animateThree(t=0){
  if(!threeVis.renderer) return;
  resizeThree();
  const delta = threeVis.lastT ? Math.min(48, t - threeVis.lastT) : 16; threeVis.lastT = t;
  if(!threeVis.paused && !threeVis.drag) threeVis.yaw += delta * (threeVis.mode === 'neural' ? .00009 : .000055);
  clampThreeCamera();
  threeVis.group.rotation.y = threeVis.yaw; threeVis.group.rotation.x = threeVis.pitch;
  const viewport = $('#threeViewport'); const rect = viewport?.getBoundingClientRect?.() || {width:650,height:650};
  const effectiveCameraZ = threeEffectiveCameraZ(rect);
  threeVis.camera.position.set(threeVis.panX, threeVis.panY, effectiveCameraZ); threeVis.camera.lookAt(threeVis.panX, threeVis.panY, 0);
  if(threeVis.pulsePoints){
    const attr = threeVis.pulsePoints.geometry.attributes.position; const arr = attr.array;
    threeVis.pulses.forEach((e,i)=>{ const phase=(t*.00030 + (i%17)/17)%1; const inv=1-phase; if(e._curve){ arr[i*3]=inv*inv*e.a.x+2*inv*phase*e._curve.cx+phase*phase*e.b.x; arr[i*3+1]=inv*inv*e.a.y+2*inv*phase*e._curve.cy+phase*phase*e.b.y; arr[i*3+2]=inv*inv*e.a.z+2*inv*phase*e._curve.cz+phase*phase*e.b.z; } else { arr[i*3]=e.a.x+(e.b.x-e.a.x)*phase; arr[i*3+1]=e.a.y+(e.b.y-e.a.y)*phase; arr[i*3+2]=e.a.z+(e.b.z-e.a.z)*phase; } });
    attr.needsUpdate = true;
  }
  threeVis.scene.traverse(obj => {
    if(obj.isPoints && obj.material?.uniforms?.uTime){
      obj.material.uniforms.uTime.value = t;
      obj.material.uniforms.uScale.value = Math.max(360, Math.min(820, threeVis.renderer.domElement.clientHeight || 420));
    }
  });
  threeVis.renderer.render(threeVis.scene, threeVis.camera); updateThreeLabels();
  threeVis.frame = requestAnimationFrame(animateThree);
}
async function loadThreeVisualiser(){
  setVisualiserLoading(true, 'Building visualiser');
  try{
    await renderThreeVisualiser(await api(`/api/constellation?namespace=${encodeURIComponent(ns())}&limit=320`));
  } finally {
    setVisualiserLoading(false);
  }
}
function switchThreeMode(mode){ threeVis.mode = mode === 'neural' ? 'neural' : 'constellation'; if(threeVis.data) renderThreeVisualiser(threeVis.data); else loadThreeVisualiser(); }
function clampThreeCamera(){
  const viewport = $('#threeViewport'); const rect = viewport?.getBoundingClientRect?.() || {width:650,height:650};
  const fallbackZ = threeVis.mode === 'neural' ? 600 : 760;
  const minCameraZ = fallbackZ / 10;
  threeVis.cameraZ = Math.max(minCameraZ, Math.min(1800, Number.isFinite(threeVis.cameraZ) ? threeVis.cameraZ : fallbackZ));
  threeVis.yaw = Number.isFinite(threeVis.yaw) ? threeVis.yaw : 0;
  threeVis.pitch = Math.max(-1.15, Math.min(1.15, Number.isFinite(threeVis.pitch) ? threeVis.pitch : .32));
  const zoomFactor = 900 / Math.max(80, threeVis.cameraZ);
  const panLimitX = Math.max(120, rect.width * (.45 + zoomFactor * .18));
  const panLimitY = Math.max(120, rect.height * (.34 + zoomFactor * .12));
  threeVis.panX = Math.max(-panLimitX, Math.min(panLimitX, Number.isFinite(threeVis.panX) ? threeVis.panX : 0));
  threeVis.panY = Math.max(-panLimitY, Math.min(panLimitY, Number.isFinite(threeVis.panY) ? threeVis.panY : 0));
}
function bindThreeControls(){
  const viewport = $('#threeViewport'); if(!viewport || viewport.dataset.controlsBound === 'true') return; viewport.dataset.controlsBound = 'true';
  const pointers = threeVis.pointer || new Map(); threeVis.pointer = pointers;
  const dist = () => { const ps=[...pointers.values()]; return ps.length < 2 ? 1 : Math.max(1, Math.hypot(ps[0].x-ps[1].x, ps[0].y-ps[1].y)); };
  const center = () => { const ps=[...pointers.values()]; return ps.length < 2 ? {x:0,y:0} : {x:(ps[0].x+ps[1].x)/2, y:(ps[0].y+ps[1].y)/2}; };
  viewport.addEventListener('contextmenu', e=>e.preventDefault());
  viewport.addEventListener('wheel', e=>{ if(e.cancelable) e.preventDefault(); threeVis.cameraZ *= Math.exp(e.deltaY*.001); clampThreeCamera(); }, {passive:false});
  viewport.addEventListener('pointerdown', e=>{
    if(e.cancelable) e.preventDefault();
    try { viewport.setPointerCapture?.(e.pointerId); } catch(_err) {}
    pointers.set(e.pointerId, {x:e.clientX,y:e.clientY});
    if(pointers.size >= 2){ const c=center(); threeVis.drag={mode:'pinch',x:c.x,y:c.y,dist:dist(),cameraZ:threeVis.cameraZ,panX:threeVis.panX,panY:threeVis.panY,moved:false}; }
    else threeVis.drag = {mode:(e.button===2 || threeVis.panMode || e.shiftKey) ? 'pan' : 'drag',x:e.clientX,y:e.clientY,yaw:threeVis.yaw,pitch:threeVis.pitch,panX:threeVis.panX,panY:threeVis.panY,moved:false};
    viewport.style.cursor='grabbing';
  }, {passive:false});
  viewport.addEventListener('pointermove', e=>{
    if(!pointers.has(e.pointerId) || !threeVis.drag) return;
    if(e.cancelable) e.preventDefault();
    pointers.set(e.pointerId, {x:e.clientX,y:e.clientY});
    const d=threeVis.drag;
    if(d.mode === 'pinch'){
      if(pointers.size < 2) return;
      const c=center(); const scale=dist()/Math.max(1,d.dist);
      threeVis.cameraZ = d.cameraZ / Math.max(.35, Math.min(2.8, scale));
      threeVis.panX = d.panX - (c.x-d.x)*.72;
      threeVis.panY = d.panY + (c.y-d.y)*.72;
      d.moved = d.moved || Math.abs(c.x-d.x)+Math.abs(c.y-d.y)>3 || Math.abs(scale-1)>.015;
      clampThreeCamera(); return;
    }
    const dx=e.clientX-d.x, dy=e.clientY-d.y; if(Math.abs(dx)+Math.abs(dy)>3) d.moved=true;
    if(d.mode === 'pan'){
      threeVis.panX=d.panX-dx*.7; threeVis.panY=d.panY+dy*.7;
    } else { threeVis.yaw=d.yaw+dx*.006; threeVis.pitch=d.pitch+dy*.004; }
    clampThreeCamera();
  }, {passive:false});
  const end=e=>{
    pointers.delete(e.pointerId);
    if(threeVis.drag?.moved) viewport.dataset.suppressClick='true';
    if(pointers.size === 1){ const p=[...pointers.values()][0]; threeVis.drag={mode:'drag',x:p.x,y:p.y,yaw:threeVis.yaw,pitch:threeVis.pitch,panX:threeVis.panX,panY:threeVis.panY,moved:true}; }
    else { threeVis.drag=null; viewport.style.cursor='grab'; }
  };
  viewport.addEventListener('pointerup', end); viewport.addEventListener('pointercancel', end); viewport.addEventListener('pointerleave', end);
  viewport.addEventListener('click', e=>{ if(e.target.closest('.three-fullscreen-inspector,.fullscreen-exit,.viewport-fullscreen,.constellation-legend')) return; if(viewport.dataset.suppressClick==='true'){ viewport.dataset.suppressClick='false'; return; } pickThreeNode(e); });
}
function pickThreeNode(e){
  if(!threeVis.camera || !threeVis.group) return;
  const rect = $('#threeViewport').getBoundingClientRect(); const mouseX=e.clientX-rect.left, mouseY=e.clientY-rect.top;
  const v = new threeVis.THREE.Vector3(); let best=null, bestD=Infinity;
  for(const n of threeVis.nodes){
    v.set(n.x,n.y,n.z).applyMatrix4(threeVis.group.matrixWorld).project(threeVis.camera);
    if(v.z < -1 || v.z > 1) continue;
    const sx=(v.x*.5+.5)*rect.width, sy=(-v.y*.5+.5)*rect.height; const d=Math.hypot(sx-mouseX, sy-mouseY);
    if(d < bestD && d < 18){ bestD=d; best=n; }
  }
  if(best) inspectThreeNode(best);
}

async function loadVisualiser(force=false){ if(force) clearThreeScene(); await loadThreeVisualiser(); }
function stopVisualiser(){ clearThreeScene(); }
function resetVisualiser(){ resetThreeCamera(); updateThreeUI(); }
function setVisualiserMode(mode){ switchThreeMode(mode); }
function bindVisualiserEvents(){ bindThreeControls(); }


init().catch(e=>{console.error(e); toast(e.message);});
