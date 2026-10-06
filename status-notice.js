(()=>{
 'use strict';
 const bar=document.getElementById('status-notice'),message=document.getElementById('status'),close=document.getElementById('status-close');
 if(!bar||!message||!close)return;
 const KEY='flow.paper.dismissed-notices.v1';
 let dismissed=new Set(),current='',drag=null,suspended=false;
 function read(){try{const values=JSON.parse(localStorage.getItem(KEY)||'[]');if(Array.isArray(values))values.filter(v=>typeof v==='string').forEach(v=>dismissed.add(v));}catch{}}
 function resetDrag(){const pointer=drag?.id;drag=null;if(pointer!==undefined&&bar.hasPointerCapture(pointer))bar.releasePointerCapture(pointer);bar.classList.remove('is-dragging');bar.style.transform='';bar.style.opacity='';}
 function show(text){const next=String(text||'').trim();if(next!==current)resetDrag();current=next;message.textContent=next;bar.hidden=suspended||!next||dismissed.has(next);}
 function dismiss(){
  if(!current)return;
  dismissed.add(current);try{localStorage.setItem(KEY,JSON.stringify([...dismissed]));}catch{}
  const focused=bar.contains(document.activeElement);bar.hidden=true;resetDrag();
  if(focused)document.querySelector('.history-ranges button[aria-pressed="true"]')?.focus({preventScroll:true});
 }
 close.addEventListener('click',dismiss);
 bar.addEventListener('pointerdown',e=>{
  if(bar.hidden||!e.isPrimary||e.button!==0||e.target.closest('button'))return;
  drag={id:e.pointerId,x:e.clientX,y:e.clientY,dx:0,horizontal:false};
 });
 bar.addEventListener('pointermove',e=>{
  if(!drag||drag.id!==e.pointerId)return;
  const dx=e.clientX-drag.x,dy=e.clientY-drag.y;
  if(!drag.horizontal){
   if(Math.abs(dy)>10&&Math.abs(dy)>=Math.abs(dx)){resetDrag();return;}
   if(Math.abs(dx)<10||Math.abs(dx)<=Math.abs(dy))return;
   drag.horizontal=true;bar.setPointerCapture(e.pointerId);bar.classList.add('is-dragging');
  }
  drag.dx=dx;bar.style.transform='translateX('+dx+'px)';bar.style.opacity=String(Math.max(.25,1-Math.abs(dx)/Math.max(bar.clientWidth,1)));
 });
 bar.addEventListener('pointerup',e=>{
  if(!drag||drag.id!==e.pointerId)return;
  const remove=drag.horizontal&&Math.abs(drag.dx)>=Math.max(40,Math.min(100,bar.clientWidth*.25));
  if(bar.hasPointerCapture(e.pointerId))bar.releasePointerCapture(e.pointerId);
  if(remove)dismiss();else resetDrag();
 });
 bar.addEventListener('pointercancel',resetDrag);
 bar.addEventListener('lostpointercapture',resetDrag);
 window.addEventListener('storage',e=>{if(e.key===KEY){read();show(current);}});
 read();bar.hidden=true;globalThis.StatusNotice={show,suspend(){suspended=true;resetDrag();bar.hidden=true;},resume(){suspended=false;show(current);}};
})();
