(()=>{
 'use strict';
 const trigger=document.getElementById('menu-toggle'),drawer=document.getElementById('site-sidebar'),close=document.getElementById('menu-close');
 if(!trigger||!drawer||!close)return;
 let previousOverflow='';
 trigger.addEventListener('click',()=>{
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
  trigger.focus();
 });
 drawer.addEventListener('click',event=>{
  if(event.target!==drawer)return;
  const rect=drawer.getBoundingClientRect();
  if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)drawer.close();
 });
})();
