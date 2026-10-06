(()=>{
 'use strict';
 const trigger=document.getElementById('menu-toggle'),drawer=document.getElementById('site-sidebar'),close=document.getElementById('menu-close');
 if(!trigger||!drawer||!close)return;
 const KEY='gpt-trading.menu-position.v1',HIDDEN_KEY='gpt-trading.menu-hidden.v1',MARGIN=12;
 let previousOverflow='',gesture=null,suppressClick=false,position=null;
 const bounds=()=>({x:Math.max(MARGIN,window.innerWidth-(trigger.offsetWidth||56)-MARGIN),y:Math.max(MARGIN,window.innerHeight-(trigger.offsetHeight||56)-MARGIN)});
 function place(x,y){const b=bounds();x=Math.max(MARGIN,Math.min(b.x,x));y=Math.max(MARGIN,Math.min(b.y,y));trigger.style.left=x+'px';trigger.style.top=y+'px';trigger.style.right='auto';trigger.style.bottom='auto';position={x:(x-MARGIN)/(b.x-MARGIN||1),y:(y-MARGIN)/(b.y-MARGIN||1)};}
 function restore(){try{const saved=JSON.parse(localStorage.getItem(KEY)||'null');if(saved&&Number.isFinite(saved.x)&&Number.isFinite(saved.y)){const b=bounds();place(MARGIN+saved.x*(b.x-MARGIN),MARGIN+saved.y*(b.y-MARGIN));}}catch{}}
 trigger.addEventListener('pointerdown',event=>{
  if(event.button!==0||event.isPrimary===false||drawer.open)return;
  const rect=trigger.getBoundingClientRect();suppressClick=false;gesture={id:event.pointerId,x:event.clientX,y:event.clientY,left:rect.left,top:rect.top,moved:false};
  trigger.setPointerCapture?.(event.pointerId);
 });
 trigger.addEventListener('pointermove',event=>{
  if(!gesture||event.pointerId!==gesture.id)return;
  const dx=event.clientX-gesture.x,dy=event.clientY-gesture.y;
  if(!gesture.moved&&Math.hypot(dx,dy)<8)return;
  gesture.moved=true;trigger.classList.add('is-dragging');place(gesture.left+dx,gesture.top+dy);
 });
 function finish(event){
  if(!gesture||event.pointerId!==gesture.id)return;
  if(gesture.moved){suppressClick=true;try{localStorage.setItem(KEY,JSON.stringify(position));}catch{}}
  else if(event.type==='pointercancel')suppressClick=true;
  gesture=null;trigger.classList.remove('is-dragging');
 }
 trigger.addEventListener('pointerup',finish);trigger.addEventListener('pointercancel',finish);trigger.addEventListener('lostpointercapture',finish);
 window.addEventListener('resize',()=>{if(position){const p={...position},b=bounds();place(MARGIN+p.x*(b.x-MARGIN),MARGIN+p.y*(b.y-MARGIN));}});
 restore();
 function applyVisibility(hidden){trigger.hidden=hidden;if(hidden&&drawer.open)drawer.close();}
 function readVisibility(){try{return localStorage.getItem(HIDDEN_KEY)==='1';}catch{return false;}}
 applyVisibility(readVisibility());
 let tap=null,tapCount=0,firstTap=0,lastTap=0;
 function clearTaps(){tap=null;tapCount=0;firstTap=lastTap=0;}
 document.addEventListener('pointerdown',event=>{
  if(event.button!==0||event.isPrimary===false){clearTaps();return;}
  tap={id:event.pointerId,x:event.clientX,y:event.clientY,time:Date.now(),moved:false};
 },true);
 document.addEventListener('pointermove',event=>{
  if(tap&&event.pointerId===tap.id&&Math.hypot(event.clientX-tap.x,event.clientY-tap.y)>=8)tap.moved=true;
 },true);
 document.addEventListener('pointerup',event=>{
  if(!tap||event.pointerId!==tap.id)return;
  const current=tap;tap=null;const now=Date.now();
  if(current.moved||now-current.time>350||Math.hypot(event.clientX-current.x,event.clientY-current.y)>=8){clearTaps();return;}
  if(!tapCount||now-lastTap>400||now-firstTap>800){tapCount=0;firstTap=now;}
  tapCount++;lastTap=now;
  if(tapCount===3){suppressClick=true;const hidden=!trigger.hidden;applyVisibility(hidden);try{localStorage.setItem(HIDDEN_KEY,hidden?'1':'0');}catch{}clearTaps();}
 },true);
 document.addEventListener('pointercancel',clearTaps,true);
 window.addEventListener('storage',event=>{if(event.key===HIDDEN_KEY||event.key===null){clearTaps();applyVisibility(readVisibility());}});
 trigger.addEventListener('click',event=>{
  if(trigger.hidden)return;
  if(suppressClick&&event.detail!==0){suppressClick=false;return;}
  suppressClick=false;
  if(drawer.open){drawer.close();return;}
  previousOverflow=document.documentElement.style.overflow;
  drawer.showModal();document.documentElement.style.overflow='hidden';
  trigger.setAttribute('aria-expanded','true');
  drawer.querySelector('[aria-current="page"]')?.focus();
 });
 close.addEventListener('click',()=>drawer.close());
 drawer.addEventListener('close',()=>{
  trigger.setAttribute('aria-expanded','false');
  document.documentElement.style.overflow=previousOverflow;
  if(!trigger.hidden)trigger.focus({preventScroll:true});
 });
 drawer.addEventListener('click',event=>{
  if(event.target!==drawer)return;
  const rect=drawer.getBoundingClientRect();
  if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)drawer.close();
 });
})();
