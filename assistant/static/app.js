import {esc,icon,date} from './ui.js';
import {dashboard,selection,stock,validation,positions,strategies} from './pages.js';
const state={page:location.hash.slice(1)||'dashboard',overview:null,catalog:null,strategyId:null,strategyError:null,market:null,index:'sh000001',indexData:null,selected:null,stockData:null,validation:null,filter:'全部',selectionTab:'观察池',comparison:null,compareBefore:null,compareAfter:null,comparing:false,compareError:null,search:'',chartTab:'日K',validationTab:'推荐表现跟踪',busy:false,token:null,error:null};
const pages={dashboard,selection,stock,validation,positions,strategies};
const nav=[['dashboard','home','工作台'],['selection','chart','滚动选股'],['stock','stock','个股研究'],['validation','check','回测验证'],['strategies','shield','我的策略'],['positions','wallet','持仓与交易']];
const root=document.getElementById('app');
async function api(path,body){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),45000);try{const r=await fetch(path,{signal:controller.signal,method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-Session-Token':state.token}:{},body:body?JSON.stringify(body):undefined});const v=await r.json();if(!r.ok||!v.success)throw new Error(v.error||'读取失败');return v.data}finally{clearTimeout(timer)}}
function toast(message){const e=document.getElementById('toast');e.textContent=message;e.classList.add('show');setTimeout(()=>e.classList.remove('show'),5000)}
function render(){
 const focused=document.activeElement?.id,selectionStart=document.activeElement?.selectionStart;
 const overview=state.overview;
 root.innerHTML=`<aside class="sidebar"><a class="brand" href="#dashboard"><strong>JEV · 个人交易助手</strong><span>专注A股 · 理性交易 · 持续进步</span></a><nav aria-label="主导航">${nav.map(([p,i,label])=>`<a href="#${p}" class="${state.page===p?'active':''}" ${state.page===p?'aria-current="page"':''}>${icon(i)}<span>${label}</span></a>`).join('')}</nav><div class="sidebar-footer"><span><i class="status-dot"></i> 国金证券 · 未连接</span><div>${icon('shield')} 实盘关闭 <span class="off-toggle"></span></div></div></aside><div class="workspace"><header class="topbar"><div class="search-box">${icon('search')}<input id="search" type="search" autocomplete="off" aria-label="搜索股票、代码" placeholder="搜索股票、代码…" value="${esc(state.search)}"></div><div class="header-right"><span class="data-badge">${icon('info')}${overview?.source_status==='ok'?'真实归档 · 非逐笔行情':'数据待核验'}</span><span class="avatar">T</span><span>theo</span><button class="refresh" data-action="refresh" aria-label="刷新行情与归档" ${state.busy?'disabled':''}>${icon('refresh')}</button></div></header><main>${state.error?`<div class="notice amber" role="alert">${esc(state.error)} · 上次成功数据仅供查看，不构成当前买入建议。</div>`:''}${overview?(pages[state.page]||dashboard)(state):`<div class="loading">${state.error?'数据读取未完成，请点击右上角刷新。':'正在读取本机研究归档…'}</div>`}<footer>技术研究辅助，不构成投资建议。</footer></main></div>`;
 if(focused==='search'){const input=document.getElementById('search');input.focus();try{input.setSelectionRange(selectionStart,selectionStart)}catch{}}
}

function syncSelection(){const list=state.overview?.candidates?.filter(c=>(state.filter==='全部'||String(c.status).includes(state.filter))&&(!state.search||`${c.name}${c.code}`.toLowerCase().includes(state.search.toLowerCase())))||[];if(!list.some(c=>c.code===state.selected)&&list.length)state.selected=list[0].code}

async function loadStock(){const code=state.selected;if(!code)return;try{const data=await api('/api/stock?code='+encodeURIComponent(code));if(state.selected===code){state.stockData=data;render()}}catch(e){toast('个股数据暂不可用：'+e.message)}}
async function loadIndex(){const code=state.index;try{const data=await api('/api/stock?code='+code);if(state.index===code){state.indexData=data;render()}}catch(e){toast('指数图表暂不可用')}}
async function refresh(){
 if(state.busy)return;state.busy=true;render();
 try{state.token=(await api('/api/bootstrap')).token;state.overview=await api('/api/overview');state.error=null;if(!state.compareBefore&&!state.compareAfter)state.comparison=null;if(!state.selected||!state.overview.candidates.some(c=>c.code===state.selected))state.selected=state.overview.candidates[0]?.code;render();
 const result=await Promise.allSettled([api('/api/market'),api('/api/validation'),api('/api/strategies')]);
 if(result[0].status==='fulfilled')state.market=result[0].value;else toast('实时指数读取失败，归档分析仍可查看');
 if(result[2].status==='fulfilled'){state.catalog=result[2].value;state.strategyError=null;}else{state.catalog=null;state.strategyError='策略说明读取失败，请刷新重试';}
 if(result[1].status==='fulfilled')state.validation=result[1].value;else toast('验证账本读取失败，原文件保留');
 }catch(e){state.error=e.name==='AbortError'?'数据请求超时，请稍后重试':e.message;if(state.overview)state.overview={...state.overview,source_status:'degraded',candidates:state.overview.candidates.map(c=>({...c,formal_candidate:false,budget:null}))};}
 finally{state.busy=false;render()}
 if(state.overview){if(state.page==='stock')loadStock();else if(state.page==='dashboard')loadIndex()}
}
window.addEventListener('hashchange',()=>{state.page=location.hash.slice(1);if(!pages[state.page])state.page='dashboard';state.search='';render();if(state.page==='stock')loadStock();if(state.page==='dashboard'&&!state.indexData)loadIndex()});
root.addEventListener('input',e=>{if(e.target.id==='search'){state.search=e.target.value.slice(0,80);if(state.page!=='selection'){state.page='selection';history.replaceState(null,'','#selection')}syncSelection();render()}});
root.addEventListener('change',e=>{if(e.target.id==='compare-before')state.compareBefore=e.target.value;if(e.target.id==='compare-after')state.compareAfter=e.target.value});
root.addEventListener('click',async e=>{const el=e.target.closest('[data-action]');if(!el||el.disabled)return;const a=el.dataset.action;
 if(a==='show-changes'){state.selectionTab='名单变化';location.hash='selection';return}
 if(a==='selection-tab'){state.selectionTab=el.dataset.tab;render();return}
 if(a==='compare-selection'){const before=document.getElementById('compare-before')?.value,after=document.getElementById('compare-after')?.value;state.compareBefore=before;state.compareAfter=after;state.comparing=true;state.compareError=null;render();try{state.comparison=await api('/api/strategy-comparison?before='+encodeURIComponent(before||'')+'&after='+encodeURIComponent(after||''));}catch(e){state.compareError=e.message}finally{state.comparing=false;render()}return}
 if(a==='refresh')return refresh();
 if(a==='page'){location.hash=el.dataset.page;return}
 if(a==='filter'){state.filter=el.dataset.filter;syncSelection();render()}
 if(a==='select'){state.selected=el.dataset.code;state.stockData=null;if(state.page==='dashboard'){location.hash='stock';return}render();if(state.page==='stock')loadStock()}
 if(a==='index'){state.index=el.dataset.code;state.indexData=null;render();loadIndex()}
 if(a==='chart'){state.chartTab=el.dataset.tab;render()}
 if(a==='strategy'){state.strategyId=el.dataset.id;render()}
 if(a==='validation-tab'){state.validationTab=el.dataset.tab;render()}
 if(a==='goto-simulation'){state.validationTab='模拟交易验证';location.hash='validation'}
 if(['refresh-simulation','cancel-simulation','close-simulation'].includes(a)){el.disabled=true;try{const endpoint={'refresh-simulation':'refresh','cancel-simulation':'cancel','close-simulation':'close'}[a];const result=await api('/api/simulation/'+endpoint,{id:el.dataset.id,code:el.dataset.code});toast(result.reason?'模拟结果：'+result.reason:'独立模拟账本已更新，实盘未操作。');state.validation=await api('/api/validation');render()}catch(e){toast(e.message);el.disabled=false}return}
 if(a==='simulate'){el.disabled=true;try{const result=await api('/api/simulation/orders',{id:el.dataset.id});toast(result.status==='pending'?'独立模拟委托已记录，等待后续可验证行情。':'本次未创建模拟委托：'+(result.reason||'条件未满足'));state.validation=await api('/api/validation');render()}catch(e){toast(e.message);el.disabled=false}}
});
render();refresh();
// Refresh is only a read of the existing monitor output, not a second analysis scheduler.
setInterval(()=>{if(!document.hidden)refresh()},60000);
