'use strict';
// Presentation only: ratios and all cumulative values are calculated by Python.
function enhanceSupplemental(root){
root.querySelectorAll('.supp-chart').forEach(box=>{
 if(box.dataset.ready)return;box.dataset.ready='1';
 const data=JSON.parse(box.dataset.chart), canvas=box.querySelector('canvas'), select=box.querySelector('select'), readout=box.querySelector('.chart-readout');
 function values(){return data.rows.map(r=>{let v=data.kind==='revenue'?(select.value==='revenue'?r.revenue:r[select.value].value):(select.value==='total'?r.total:r.groups[select.value].net);return v==null?null:Number(v)*(data.kind==='revenue'&&select.value!=='revenue'?100:1);});}
 function draw(){const w=Math.max(300,box.clientWidth),h=260;canvas.width=w*2;canvas.height=h*2;canvas.style.width='100%';canvas.style.height=h+'px';const ctx=canvas.getContext('2d');ctx.scale(2,2);const vs=values(), finite=vs.filter(v=>v!==null&&Number.isFinite(v)),lo=Math.min(0,...finite),hi=Math.max(1,...finite),y=v=>20+(hi-v)/(hi-lo)*200,step=(w-90)/Math.max(1,vs.length);ctx.font='12px sans-serif';ctx.fillStyle='#555';ctx.fillText(hi.toLocaleString(),0,18);ctx.fillText(lo.toLocaleString(),0,222);ctx.beginPath();ctx.moveTo(65,y(0));ctx.lineTo(w,y(0));ctx.strokeStyle='#aaa';ctx.stroke();vs.forEach((v,i)=>{if(v===null)return;ctx.fillStyle=v<0?'#287b57':'#bb5148';ctx.fillRect(65+i*step,Math.min(y(v),y(0)),Math.max(1,step-2),Math.max(1,Math.abs(y(v)-y(0))));});if(data.rows.length){ctx.fillStyle='#555';ctx.fillText(data.rows[0].period,65,250);ctx.fillText(data.rows.at(-1).period,Math.max(65,w-90),250);}}
 canvas.addEventListener('mousemove',e=>{const i=Math.floor((e.offsetX-65)/((box.clientWidth-90)/Math.max(1,data.rows.length)));if(data.rows[i])readout.textContent=data.rows[i].period+' · '+select.selectedOptions[0].text+': '+(values()[i]===null?'缺值':values()[i].toLocaleString(undefined,{maximumFractionDigits:4}));});
 select.addEventListener('change',draw);const observer=new ResizeObserver(()=>{if(!box.isConnected){observer.disconnect();return;}draw();});observer.observe(box);draw();
});

root.querySelectorAll('.supp-query').forEach(form=>{
 if(form.dataset.ready)return;form.dataset.ready='1';
 form.addEventListener('submit',async event=>{
  event.preventDefault();let message=form.querySelector('[role=status]');if(!message){message=document.createElement('p');message.setAttribute('role','status');form.append(message);}message.textContent='讀取本機補充資料…';
  const params=new URLSearchParams(new FormData(form));params.set('fragment','1');
  try{const response=await fetch(form.getAttribute('action')+'?'+params);if(!response.ok){message.textContent='查詢失敗：請確認日期與起迄月份；原表單保留。';return;}
   const doc=new DOMParser().parseFromString(await response.text(),'text/html');
   const old=form.closest('.supp-panel'), fresh=doc.querySelector('.supp-panel');
   // Preserve unsaved update inputs while changing the read-only display range.
   const update=old.querySelector('form[method=post]');if(update)fresh.querySelector('form[method=post]').replaceWith(update);
   old.replaceWith(fresh);enhanceSupplemental(fresh);window.WorkbenchForms?.enhance(fresh);
  }catch{message.textContent='無法連線；表單內容已保留。';}
 });
});
}
window.SupplementalCharts={enhance:enhanceSupplemental};
document.addEventListener('DOMContentLoaded',()=>enhanceSupplemental(document));
