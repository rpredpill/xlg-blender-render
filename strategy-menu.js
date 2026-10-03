(()=>{
 function init(){
  const tabs=document.querySelector('#strategy-settings-root .strategy-tabs');
  const select=mode=>{
   tabs?.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.mode===mode));
   if(mode==='joy'){globalThis.JoyLive.activate();document.getElementById('strategy-rule-title').textContent='환희 · FLOW';document.getElementById('strategy-rule').textContent='S&P 500 · 63거래일 거래대금 중앙값 · Top10 / Exit20 · 점수 비례 · 분기 리밸런싱 · 백테스트';document.getElementById('strategy-current').textContent='현재 · 환희';}
   else{localStorage.setItem('somx.strategy.active.v1','nani');globalThis.NaniStrategy.activate().then(()=>globalThis.NaniLive.sync(true)).then(()=>render());}
  };
  if(tabs){tabs.querySelectorAll('button').forEach(b=>{if(b.dataset.mode!=='nani')b.remove();});
   tabs.querySelector('[data-mode="nani"]')?.addEventListener('click',()=>select('nani'));
   for(const [mode,label] of [['joy','환희'],['fear','공포']]){const b=document.createElement('button');b.type='button';b.dataset.mode=mode;b.textContent=label;b.onclick=()=>mode==='fear'?location.assign('./flow.html'):select('joy');tabs.append(b);}
   tabs.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.mode===globalThis.SOMXStrategy.getMode()));
   document.getElementById('strategy-settings-root').addEventListener('click',e=>{if(globalThis.SOMXStrategy.getMode()!=='joy'||!['strategy-preview-btn','strategy-apply-btn'].includes(e.target.id))return;e.preventDefault();e.stopImmediatePropagation();if(e.target.id==='strategy-apply-btn')globalThis.JoyLive.activate();document.getElementById('strategy-preview').textContent='환희 적용 · 원형 비중·월말 종목·백테스트 수익률은 메인 화면에서 확인하세요.';},true);
  }
  const nav=document.createElement('nav');nav.setAttribute('aria-label','전략 선택');nav.style.cssText='display:flex;justify-content:center;gap:10px;margin:10px 0 20px;flex-wrap:wrap';
  for(const [label,mode] of [['Nani','nani'],['환희','joy'],['공포 · Paper','fear']]){const b=document.createElement('button');b.type='button';b.textContent=label;b.style.cssText='font:inherit;background:transparent;padding:10px 15px;border:1px solid #b6ccc5;border-radius:12px;color:#287b69;font-weight:700';b.onclick=()=>mode==='fear'?location.assign('./flow.html'):(mode==='nani'?tabs?.querySelector('[data-mode="nani"]')?.click():select(mode));nav.append(b);}
  document.querySelector('.board')?.before(nav);document.title='Nani · 환희 · 공포';
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
