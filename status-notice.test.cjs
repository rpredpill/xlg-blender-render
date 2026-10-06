const fs=require('fs'),vm=require('vm'),assert=require('assert');
const code=fs.readFileSync(__dirname+'/status-notice.js','utf8'),data=new Map();
function start(){
 const events={},classes=new Set(),bar={hidden:true,clientWidth:360,style:{},classList:{add:x=>classes.add(x),remove:x=>classes.delete(x)},contains:x=>x===close,addEventListener:(n,f)=>events[n]=f,setPointerCapture(id){this.capture=id},hasPointerCapture(id){return this.capture===id},releasePointerCapture(){this.capture=null}},msg={textContent:'알림 A'},close={addEventListener:(n,f)=>events['close-'+n]=f},button={focus(){button.focused=true}},windows={};
 const document={activeElement:close,getElementById:id=>({'status-notice':bar,status:msg,'status-close':close}[id]),querySelector:()=>button},ctx={document,localStorage:{getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v)},window:{addEventListener:(n,f)=>windows[n]=f}};ctx.globalThis=ctx;vm.createContext(ctx);vm.runInContext(code,ctx);
 const pointer=(n,x,y=0)=>events[n]({pointerId:1,isPrimary:true,button:0,clientX:x,clientY:y,target:{closest:()=>null}});
 return {bar,msg,events,show:ctx.StatusNotice.show,pointer,button,windows};
}
let a=start();assert.equal(a.bar.hidden,false);a.events['close-click']();assert(a.bar.hidden);assert(a.button.focused);a.show('알림 A');assert(a.bar.hidden);a.show('알림 B');assert(!a.bar.hidden);a.show('알림 A');assert(a.bar.hidden);
a=start();assert(a.bar.hidden);a.show('알림 C');a.pointer('pointerdown',100);a.pointer('pointermove',80,35);a.pointer('pointerup',50,100);assert(!a.bar.hidden,'vertical scroll must not dismiss');
a.pointer('pointerdown',100);a.pointer('pointermove',125);a.pointer('pointerup',125);assert(!a.bar.hidden,'short swipe must not dismiss');assert.equal(a.bar.style.transform,'');
a.pointer('pointerdown',100);a.pointer('pointermove',240);a.pointer('pointerup',240);assert(a.bar.hidden,'right swipe dismiss');
a.show('알림 D');a.pointer('pointerdown',200);a.pointer('pointermove',50);a.pointer('pointerup',50);assert(a.bar.hidden,'left swipe dismiss');
a=start();a.show('알림 C');assert(a.bar.hidden,'swipe persists after reload');a.show('알림 E');assert(!a.bar.hidden);a.pointer('pointerdown',100);a.pointer('pointermove',150);a.events.pointercancel();assert(!a.bar.hidden);assert.equal(a.bar.style.transform,'');
a.show('알림 A');assert(a.bar.hidden);a.show('알림 F');assert(!a.bar.hidden);
console.log('PASS close, persistent suppression, new messages, both swipe directions, vertical scroll, short/cancelled gestures and focus');
