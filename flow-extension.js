(function(){
 document.addEventListener('DOMContentLoaded',()=>{
   const tabs=document.querySelector('#strategy-settings-root .strategy-tabs');
   if(tabs&&!tabs.querySelector('[data-mode="flow"]')){const button=document.createElement('button');button.type='button';button.dataset.mode='flow';button.textContent='FLOW · Paper';button.onclick=()=>location.assign('./flow.html');tabs.append(button);}
   const link=document.createElement('a');link.href='./flow.html';link.textContent='FLOW · 페이퍼트레이딩 →';link.style.cssText='display:block;margin:12px 0;text-align:center;color:#27937b;font-weight:700';
   document.querySelector('.board')?.before(link);
 });
})();
