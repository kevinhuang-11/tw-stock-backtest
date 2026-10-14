'use strict';
for (const form of document.querySelectorAll('.watch-update,.watch-snapshot')) {
  form.addEventListener('submit',async event=>{
    event.preventDefault();const button=form.querySelector('button');const status=form.querySelector('[role=status]');
    if(button.disabled)return;button.disabled=true;status.textContent='處理中…';
    try{
      const response=await fetch(form.action,{method:'POST',body:new FormData(form),headers:{Accept:'application/json'}});
      if(response.redirected){window.location.assign(response.url);return;}
      const result=await response.json();
      if(!response.ok)throw new Error(result.errors?Object.values(result.errors).join('；'):'無法完成，已保留表單');
      status.textContent='已建立更新工作。';
      const link=document.createElement('a');link.href=result.summary_url;link.textContent='查看進度／結果';link.target='_blank';link.rel='noopener';status.append(link);
    }catch(error){status.textContent=error.message||'操作失敗，請稍後重試';}
    finally{button.disabled=false;}
  });
}

// Replace only the observation area; keep update drafts and private note edits.
document.addEventListener('submit',async event=>{
  if(event.target.id!=='watch-query')return;
  event.preventDefault();const form=event.target,button=form.querySelector('button');
  button.disabled=true;let error=form.querySelector('.query-error');
  if(!error){error=document.createElement('p');error.className='query-error';error.setAttribute('role','alert');form.append(error);}
  error.textContent='讀取中…';
  try{
    const url=new URL('/watchlist',location.origin);url.search=new URLSearchParams(new FormData(form)).toString();
    const response=await fetch(url);if(!response.ok)throw new Error('查詢失敗，已保留表單。');
    const doc=new DOMParser().parseFromString(await response.text(),'text/html');
    const area=doc.querySelector('#watch-overview');if(!area)throw new Error('總覽無法讀取');
    document.querySelector('#watch-overview').replaceWith(area);history.replaceState(null,'',url);
    for(const name of ['strategy_id','as_of']){
      const input=document.querySelector('.watch-snapshot [name="'+name+'"]');
      if(input)input.value=url.searchParams.get(name)||'';
    }
  }catch(e){error.textContent=e.message;button.disabled=false;}
});
