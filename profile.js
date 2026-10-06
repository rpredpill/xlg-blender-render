(()=>{
 'use strict';
 const button=document.getElementById('profile-button'),input=document.getElementById('profile-file'),photo=document.getElementById('profile-photo'),placeholder=document.getElementById('profile-placeholder');
 if(!button||!input||!photo||!placeholder)return;
 const KEY='gpt-trading.profile-photo.v1';
 function render(value){
  const valid=typeof value==='string'&&/^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(value)&&value.length<1000000;
  photo.hidden=true;placeholder.hidden=false;
  if(valid){photo.onload=()=>{photo.hidden=false;placeholder.hidden=true;};photo.onerror=()=>{photo.hidden=true;placeholder.hidden=false;};photo.src=value;}
  else photo.removeAttribute('src');
 }
 function restore(){try{render(localStorage.getItem(KEY));}catch{render(null);}}
 button.addEventListener('click',()=>input.click());
 input.addEventListener('change',async()=>{
  const file=input.files?.[0];input.value='';if(!file)return;
  if(!file.type.startsWith('image/')||file.size>20*1024*1024){alert('20MB 이하의 이미지 파일을 선택하세요.');return;}
  button.disabled=true;button.setAttribute('aria-busy','true');let url;
  try{
   url=URL.createObjectURL(file);const image=new Image();
   await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(Error('image'));image.src=url;});
   if(!image.naturalWidth||!image.naturalHeight)throw Error('image');
   const size=Math.min(image.naturalWidth,image.naturalHeight),canvas=document.createElement('canvas');canvas.width=canvas.height=256;
   const ctx=canvas.getContext('2d');if(!ctx)throw Error('canvas');ctx.fillStyle='#fff';ctx.fillRect(0,0,256,256);
   ctx.drawImage(image,(image.naturalWidth-size)/2,(image.naturalHeight-size)/2,size,size,0,0,256,256);
   const value=canvas.toDataURL('image/jpeg',.88);
   try{localStorage.setItem(KEY,value);}catch{alert('사진을 저장하지 못했습니다. 브라우저 저장 공간이나 설정을 확인하세요.');return;}
   render(value);
  }catch{alert('이 사진을 불러오지 못했습니다. JPG, PNG 또는 WebP 사진으로 다시 선택하세요.');}
  finally{if(url)URL.revokeObjectURL(url);button.disabled=false;button.removeAttribute('aria-busy');}
 });
 window.addEventListener('storage',event=>{if(event.key===KEY||event.key===null)restore();});restore();
})();
