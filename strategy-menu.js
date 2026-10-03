(function(){
 function init(){
   const tabs=document.querySelector('#strategy-settings-root .strategy-tabs');
   if(tabs){tabs.querySelectorAll('button').forEach(b=>{if(b.dataset.mode!=='nani')b.remove();});
     for(const [mode,label,url] of [['joy','환희','./flow-research.html'],['fear','공포','./flow.html']]){const b=document.createElement('button');b.type='button';b.dataset.mode=mode;b.textContent=label;b.onclick=()=>location.assign(url);tabs.append(b);}
     tabs.querySelector('[data-mode="nani"]')?.click();
   }
   const nav=document.createElement('nav');nav.setAttribute('aria-label','전략 선택');nav.style.cssText='display:flex;justify-content:center;gap:10px;margin:10px 0 20px;flex-wrap:wrap';
   for(const [label,url] of [['Nani','./'],['환희 · 백테스트','./flow-research.html'],['공포 · Paper 기록','./flow.html']]){const a=document.createElement('a');a.textContent=label;a.href=url;a.style.cssText='padding:10px 15px;border:1px solid #b6ccc5;border-radius:12px;text-decoration:none;color:#287b69;font-weight:700';nav.append(a);}
   document.querySelector('.board')?.before(nav);
   document.title='Nani · 환희 · 공포';
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
