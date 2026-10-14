/* Project-owned Canvas renderer. Indicator values come only from the backend. */
'use strict';
let queryVersion=0;
function number(value) { if (value === null || value === undefined || value === '') return null; const n=Number(value); return Number.isFinite(n)?n:null; }
function setupChart() {
  const root=document.querySelector('#chart'); if(!root) return;
  const data=JSON.parse(root.dataset.chart), all=data.rows, canvas=root.querySelector('canvas'), ctx=canvas.getContext('2d');
  let left=0, count=all.length, selected=-1, drag=null;
  const zoom=root.querySelector('#chart-zoom'), start=root.querySelector('#chart-start'), end=root.querySelector('#chart-end');
  start.value=all[0].date; end.value=all[all.length-1].date; zoom.min=Math.min(5,all.length);
  const enabled=key=>root.querySelector(`input[value="${key}"]`).checked;
  const colors={sma:'#b56c00',ema:'#087fc4',rsi:'#8b49b6',macd:'#007d79',signal:'#bc7000'};
  function draw(){
    const width=canvas.clientWidth, height=canvas.clientHeight, dpr=window.devicePixelRatio||1;
    canvas.width=width*dpr;canvas.height=height*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);
    const rows=all.slice(left,left+count), pad=48, right=width-12, dx=(right-pad)/Math.max(1,rows.length);
    const panels=[{top:20,bottom:height*.44,label:'日 K / SMA / EMA'}, {top:height*.49,bottom:height*.62,label:'成交量'}, {top:height*.67,bottom:height*.79,label:'RSI'}, {top:height*.84,bottom:height-24,label:'MACD / signal / histogram'}];
    function panel(i,values){const p=panels[i]; const valid=values.map(number).filter(v=>v!==null);let lo=valid.length?Math.min(...valid):0,hi=valid.length?Math.max(...valid):1;if(lo===hi){lo-=1;hi+=1;}const y=v=>p.bottom-(v-lo)/(hi-lo)*(p.bottom-p.top);ctx.strokeStyle='#d8e0e8';ctx.beginPath();ctx.moveTo(pad,p.top);ctx.lineTo(pad,p.bottom);ctx.lineTo(right,p.bottom);ctx.stroke();ctx.fillStyle='#52667c';ctx.font='11px system-ui';ctx.fillText(p.label,pad,p.top-5);ctx.fillText(hi.toFixed(2),1,p.top+8);ctx.fillText(lo.toFixed(2),1,p.bottom);return y;}
    const priceValues=rows.flatMap(r=>[r.high,r.low,...(enabled('sma')?[r.sma]:[]),...(enabled('ema')?[r.ema]:[])]);
    const yp=panel(0,priceValues),yv=panel(1,[0,...rows.map(r=>r.volume)]),yr=panel(2,[0,100]),ym=panel(3,[0,...rows.flatMap(r=>[r.macd,r.signal,r.histogram])]);
    function line(field,y){ctx.strokeStyle=colors[field];ctx.lineWidth=1.5;ctx.beginPath();let active=false;rows.forEach((r,i)=>{const v=number(r[field]);if(v===null){active=false;return;}const x=pad+(i+.5)*dx;if(active)ctx.lineTo(x,y(v));else ctx.moveTo(x,y(v));active=true;});ctx.stroke();}
    rows.forEach((r,i)=>{const x=pad+(i+.5)*dx, w=Math.max(1,Math.min(12,dx*.65)),o=number(r.open),c=number(r.close),h=number(r.high),l=number(r.low);ctx.strokeStyle=ctx.fillStyle=o!==null&&c!==null&&c>=o?'#b83b44':'#16836b';if([o,c,h,l].every(v=>v!==null&&v>0)){ctx.beginPath();ctx.moveTo(x,yp(h));ctx.lineTo(x,yp(l));ctx.stroke();ctx.fillRect(x-w/2,Math.min(yp(o),yp(c)),w,Math.max(1,Math.abs(yp(o)-yp(c))));}const v=number(r.volume);if(v!==null){ctx.fillStyle='#8caac4';ctx.fillRect(x-w/2,yv(v),w,Math.max(1,yv(0)-yv(v)));}const hist=number(r.histogram);if(enabled('macd')&&hist!==null){ctx.fillStyle=hist>=0?'#b83b44':'#16836b';ctx.fillRect(x-w/2,Math.min(ym(0),ym(hist)),w,Math.max(1,Math.abs(ym(hist)-ym(0))));}});
    if(enabled('sma'))line('sma',yp);if(enabled('ema'))line('ema',yp);if(enabled('rsi'))line('rsi',yr);if(enabled('macd')){line('macd',ym);line('signal',ym);}
    ctx.fillStyle='#52667c';ctx.fillText(rows[0].date,pad,height-5);ctx.fillText(rows[rows.length-1].date,Math.max(pad,right-80),height-5);
    if(selected>=left&&selected<left+count){const x=pad+(selected-left+.5)*dx;ctx.strokeStyle='#555';ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(x,15);ctx.lineTo(x,height-20);ctx.stroke();ctx.setLineDash([]);}
  }
  function clamp(){count=Math.max(1,Math.min(all.length,Math.round(count)));left=Math.max(0,Math.min(all.length-count,Math.round(left)));zoom.value=count;draw();}
  root.querySelectorAll('.indicator').forEach(e=>e.addEventListener('change',draw));zoom.addEventListener('input',()=>{const endIndex=left+count;count=Number(zoom.value);left=endIndex-count;clamp();});
  root.querySelector('#chart-reset').onclick=()=>{left=0;count=all.length;start.value=all[0].date;end.value=all.at(-1).date;clamp();};
  root.querySelector('#chart-range').onclick=()=>{const a=all.findIndex(r=>r.date>=start.value),b=all.findLastIndex(r=>r.date<=end.value);if(a<0||b<a){root.querySelector('#chart-tooltip').textContent='此顯示範圍沒有行情；未改動計算資料。';return;}left=a;count=b-a+1;clamp();};
  canvas.addEventListener('wheel',e=>{e.preventDefault();const old=count;count=Math.max(Math.min(5,all.length),Math.min(all.length,Math.round(count*(e.deltaY>0?1.2:.8))));left+=(old-count)/2;clamp();},{passive:false});
  canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,left};canvas.setPointerCapture(e.pointerId);});canvas.addEventListener('pointerup',()=>drag=null);canvas.addEventListener('pointercancel',()=>drag=null);
  canvas.addEventListener('pointermove',e=>{if(drag){left=drag.left-(e.clientX-drag.x)/(canvas.clientWidth-60)*count;clamp();}const x=e.clientX-canvas.getBoundingClientRect().left;selected=left+Math.max(0,Math.min(count-1,Math.floor((x-48)/(canvas.clientWidth-60)*count)));const r=all[selected];if(!r)return;root.querySelector('#chart-tooltip').textContent=['日期 '+r.date,...['open','high','low','close','volume','sma','ema','rsi','macd','signal','histogram'].map(k=>k+' '+(r[k]??'缺值'))].join(' · ');draw();});
  if(window.stockResize)window.stockResize.disconnect();window.stockResize=new ResizeObserver(draw);window.stockResize.observe(canvas);draw();
}
function setup(){
  if(window.WorkbenchForms)window.WorkbenchForms.enhance(document);
  setupChart();
  window.SupplementalCharts?.enhance(document);
  document.querySelectorAll('.watch-form').forEach(form=>form.addEventListener('submit',async e=>{e.preventDefault();const out=form.querySelector('.watch-status'),data=new FormData(form);data.set('action',e.submitter?.value||'save');out.textContent='儲存中…';try{const response=await fetch(form.getAttribute('action')||location.href,{method:'POST',body:data,headers:{Accept:'application/json'}});if(!response.ok){out.textContent=await response.text();return;}const saved=await response.json();form.elements.revision.value=saved.revision;out.textContent=saved.message+'（不會下載行情或寄信）';if(!saved.active)form.elements.note.value='';}catch{out.textContent='連線失敗；請保留草稿，重新載入確認是否已儲存。';}}));
  const form=document.querySelector('#query-form');if(form)form.addEventListener('submit',async e=>{e.preventDefault();const version=++queryVersion;const out=document.querySelector('#loading');out.textContent='查詢與計算中…';const params=new URLSearchParams(new FormData(form));const url=(form.getAttribute('action')||location.pathname)+'?'+params;try{const response=await fetch(url+'&fragment=1');const html=await response.text();if(version!==queryVersion)return;if(!response.ok){out.textContent=html;return;}const savedSupplement=document.querySelector('.supp-panel');document.querySelector('#stock-panel').innerHTML=html;if(savedSupplement)document.querySelector('.supp-panel')?.replaceWith(savedSupplement);history.replaceState(null,'',url);out.textContent='已載入；未下載行情。';setup();}catch{out.textContent='查詢失敗；保留目前畫面。';}});
}
document.addEventListener('DOMContentLoaded',setup);
