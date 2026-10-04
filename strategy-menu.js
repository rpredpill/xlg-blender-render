(()=>{
 function init(){
  const tabs=document.querySelector('#strategy-settings-root .strategy-tabs');
  function paint(mode){
   document.querySelectorAll('[data-mode]').forEach(b=>{if(['nani','joy','fear'].includes(b.dataset.mode)){b.classList.toggle('active',b.dataset.mode===mode);b.setAttribute('aria-pressed',String(b.dataset.mode===mode));}});
   const joy=mode==='joy';document.getElementById('strategy-rule-title').textContent=joy?'Echo · FLOW':'Nani · Core';document.getElementById('strategy-rule').textContent=joy?'S&P 500 · 63거래일 거래대금 중앙값 · Top10 / Exit20 · 점수 비례 · 분기 리밸런싱 · 최신 모의 계산과 과거 백테스트':'Nikkei 100 ∪ Nasdaq-100 · 일본 50% : 미국 50% · 국가 내 유동성시총 비례';document.getElementById('strategy-current').textContent=joy?'현재 · Echo':'현재 · Nani';
  }
  async function select(mode){
   if(mode==='fear'){location.assign('./flow.html');return;}
   const url=new URL(location.href);url.searchParams.set('strategy',mode);history.replaceState(null,'',url);
   if(mode==='joy'){paint(mode);await globalThis.JoyLive.activate();}
   else{globalThis.JoyLive.stop();localStorage.setItem('somx.strategy.active.v1','nani');paint(mode);await globalThis.NaniStrategy.activate();if(localStorage.getItem('somx.strategy.active.v1')!=='nani')return;await globalThis.NaniLive.sync(true);if(localStorage.getItem('somx.strategy.active.v1')==='nani')render();}
  }
  if(tabs){tabs.querySelectorAll('button').forEach(b=>{if(b.dataset.mode!=='nani')b.remove();});tabs.querySelector('[data-mode="nani"]')?.addEventListener('click',()=>select('nani'));
   for(const [mode,label] of [['joy','Echo'],['fear','Pulse']]){const b=document.createElement('button');b.type='button';b.dataset.mode=mode;b.textContent=label;b.onclick=()=>select(mode);tabs.append(b);}
   document.getElementById('strategy-settings-root').addEventListener('click',e=>{if(globalThis.SOMXStrategy.getMode()!=='joy'||!['strategy-preview-btn','strategy-apply-btn'].includes(e.target.id))return;e.preventDefault();e.stopImmediatePropagation();if(e.target.id==='strategy-apply-btn')select('joy');document.getElementById('strategy-preview').textContent='Echo · 메인 화면에 최신 종목·비중·월간 수익률을 표시합니다. 기간 선택으로 과거 백테스트도 확인할 수 있습니다.';},true);
  }
  paint(globalThis.SOMXStrategy.getMode());document.title='Nani · Echo · Pulse';
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
