export const esc = v => String(v ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const num = (v,d=2) => v === null || v === undefined || v === '' || !Number.isFinite(Number(v)) ? '—' : Number(v).toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d});
export const money = v => v == null ? '—' : Math.abs(Number(v))>=1e8 ? num(Number(v)/1e8)+'亿' : num(v);
export const pct = v => v==null ? '—' : `${Number(v)>0?'+':''}${num(v)}%`;
export const date = v => {if(!v)return '尚无数据';const s=String(v);return /^\d{14}$/.test(s)?`${s.slice(0,4)}-${s.slice(4,6)}-${s.slice(6,8)} ${s.slice(8,10)}:${s.slice(10,12)}:${s.slice(12)}`:s.replace('T',' ').replace(/\+08:00$/,'').slice(0,19)};
export const tone = v => Number(v)>0?'up':Number(v)<0?'down':'';
export const badge = (text,kind='gray')=>`<span class="badge ${kind}">${esc(text||'待核验')}</span>`;
export const empty = (title,body='') => `<div class="empty"><span class="empty-mark">${icon('chart')}</span><strong>${esc(title)}</strong><p>${esc(body)}</p></div>`;
const icons={home:'M3 10 12 3l9 7v11h-6v-7H9v7H3z',chart:'M4 20V4m0 16h17M8 16v-5m5 5V7m5 9v-8',stock:'M14 3H5v18h13v-7M9 7h3m-3 4h2m8 1 3 3m-2-7a4 4 0 1 0-8 0 4 4 0 0 0 8 0',check:'M4 4h16v16H4z M8 12l3 3 6-7',wallet:'M3 7h18v13H3zM6 7V4h12v3m-2 6h5',search:'M20 20l-5-5m2-6A6 6 0 1 1 5 5a6 6 0 0 1 12 4',refresh:'M20 7v5h-5M4 17v-5h5M5 8a8 8 0 0 1 13-3l2 3M4 16l2 3a8 8 0 0 0 13-3',arrow:'m9 5 7 7-7 7',shield:'M12 3 3 7v6c0 4 9 8 9 8s9-4 9-8V7zM8 12l3 3 5-6',info:'M12 11v6m0-10v1M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',menu:'M4 6h16M4 12h16M4 18h16'};
export function icon(name){return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${icons[name]||icons.chart}"/></svg>`}
export const panel=(title,content,extra='',cls='')=>`<section class="panel ${cls}"><div class="panel-head"><h2>${title}</h2>${extra}</div>${content}</section>`;
export const button=(label,action,cls='',attrs='')=>`<button class="button ${cls}" data-action="${action}" ${attrs}>${label}</button>`;
export const metric=(label,value,note='',cls='')=>`<div class="metric"><div class="muted">${esc(label)}</div><div class="metric-value ${cls}">${value}</div><div class="caption">${esc(note)}</div></div>`;
export function lineChart(points,{color='#2875ed',height=220,percent=false}={}){
 const p=(points||[]).map(v=>({time:v.time||v.date,value:Number(v.price??v.value??v.close)})).filter(v=>Number.isFinite(v.value)&&v.value>0);
 if(p.length<2)return empty('等待连续行情样本','至少两个有效时点后展示走势，不补造曲线。');
 const w=660,h=height,l=48,r=15,t=15,b=30,min=Math.min(...p.map(v=>v.value)),max=Math.max(...p.map(v=>v.value)),pad=(max-min)*.12||min*.005,lo=min-pad,hi=max+pad;
 const y=v=>t+(hi-v)/(hi-lo)*(h-t-b),x=i=>l+i/(p.length-1)*(w-l-r);
 const path=p.map((v,i)=>`${i?'L':'M'}${x(i).toFixed(2)},${y(v.value).toFixed(2)}`).join(' ');
 const grids=Array.from({length:5},(_,i)=>{const v=lo+(hi-lo)*i/4;return `<line x1="${l}" x2="${w-r}" y1="${y(v)}" y2="${y(v)}" class="gridline"/><text x="${l-8}" y="${y(v)+4}" text-anchor="end">${num(v)}</text>`}).join('');
 return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc('行情走势，'+p.length+'个采样点')}">${grids}<path d="${path} L${x(p.length-1)},${h-b} L${l},${h-b}Z" fill="${color}" opacity=".06"/><path d="${path}" fill="none" stroke="${color}" stroke-width="2"/><text x="${l}" y="${h-5}">${esc(date(p[0].time).slice(5,16))}</text><text x="${w-r}" y="${h-5}" text-anchor="end">${esc(date(p.at(-1).time).slice(5,16))}</text></svg>`;
}
export function candlesChart(rows,h=260){
 const a=(rows||[]).filter(r=>[r.open,r.close,r.high,r.low].every(v=>Number.isFinite(Number(v))&&Number(v)>0)).slice(-55);
 if(!a.length)return empty('日线暂不可用','行情源恢复后重试。');
 const w=700,l=48,r=14,t=18,b=38,lo=Math.min(...a.map(v=>v.low))*.99,hi=Math.max(...a.map(v=>v.high))*1.01,y=v=>t+(hi-v)/(hi-lo)*(h-t-b),dx=(w-l-r)/a.length;
 const grid=Array.from({length:5},(_,i)=>{let v=lo+(hi-lo)*i/4;return `<line class="gridline" x1="${l}" x2="${w-r}" y1="${y(v)}" y2="${y(v)}"/><text x="${l-7}" y="${y(v)+4}" text-anchor="end">${num(v)}</text>`}).join('');
 const bars=a.map((v,i)=>{let x=l+(i+.5)*dx,c=v.close>=v.open?'#e34751':'#20946f';return `<line x1="${x}" x2="${x}" y1="${y(v.high)}" y2="${y(v.low)}" stroke="${c}"/><rect x="${x-dx*.28}" y="${Math.min(y(v.open),y(v.close))}" width="${dx*.56}" height="${Math.max(1,Math.abs(y(v.open)-y(v.close)))}" fill="${c}"/>`}).join('');
 const maxVolume=Math.max(...a.map(v=>Number(v.volume)||0),1);
 const volume=`<svg class="chart volume-chart" preserveAspectRatio="none" viewBox="0 0 700 70" role="img" aria-label="每日成交量"><text x="4" y="12">成交量</text>${a.map((v,i)=>`<rect x="${l+i*dx+dx*.2}" y="${60-(Number(v.volume)||0)/maxVolume*44}" width="${dx*.6}" height="${(Number(v.volume)||0)/maxVolume*44}" fill="${v.close>=v.open?'#e98a93':'#74b7a2'}"/>`).join('')}<line class="gridline" x1="48" x2="686" y1="61" y2="61"/></svg>`;
 return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="前复权日K线">${grid}${bars}<text x="${l}" y="${h-8}">${esc(a[0].date)}</text><text x="${w-r}" y="${h-8}" text-anchor="end">${esc(a.at(-1).date)}</text></svg>${volume}`;
}
export function financialChart(rows){
 const a=(rows||[]).filter(r=>r.revenue!=null).slice(0,4).reverse();if(!a.length)return empty('暂无可核验财报','营收、净利润与现金流待数据源返回。');
 const max=Math.max(...a.map(v=>Math.abs(v.revenue)),1),w=500,h=175,dx=105;
 return `<svg class="chart finance-chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="报告期累计营收和净利润，亿元">${a.map((v,i)=>{const x=45+i*dx,r=120*v.revenue/max,n=120*Math.abs(v.net_profit||0)/max;return `<rect x="${x}" y="${140-r}" width="30" height="${r}" fill="#3184f4" rx="2"/><rect x="${x+35}" y="${(v.net_profit||0)<0?140:140-n}" width="22" height="${Math.max(1,n)}" fill="#f7ad4a" rx="2"/><text x="${x+15}" y="${134-r}" text-anchor="middle">${num(v.revenue/1e8,1)}</text><text x="${x+25}" y="170" text-anchor="middle">${esc(v.date)}</text>`}).join('')}<line class="gridline" x1="25" x2="490" y1="140" y2="140"/></svg>`;
}
