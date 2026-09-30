(()=>{
"use strict";
const BACKFILL_START="2023-07";
const CHECKPOINT_MONTH="2026-01";
const MARKER_KEY="somx.core.canonical.backfill.v1";
const CACHE_CHUNK=120;
let running=false;
const priceCache=new Map();

function status(text){const el=document.getElementById("history-status");if(el)el.textContent=text}
function sameSet(a,b){if(!a||!b||a.length!==b.length)return false;const A=[...a].sort(),B=[...b].sort();return A.every((x,i)=>x===B[i])}
function stateKey(a){return [...a].sort().join("|")}
function monthRange(start,end){const out=[];for(let m=start;m<=end;m=addMonths(m,1))out.push(m);return out}
function combinations6(items){const out=[];const n=items.length;for(let a=0;a<n-5;a++)for(let b=a+1;b<n-4;b++)for(let c=b+1;c<n-3;c++)for(let d=c+1;d<n-2;d++)for(let e=d+1;e<n-1;e++)for(let f=e+1;f<n;f++)out.push([items[a],items[b],items[c],items[d],items[e],items[f]]);return out}
function transitionSet(prev,ranked,universe){const pos=new Map(ranked.map((x,i)=>[x.s,i+1])),keep=prev.filter(s=>universe.has(s)&&(pos.get(s)??Infinity)<=16);for(const x of ranked.slice(0,6)){if(keep.length>=6)break;if(!keep.includes(x.s))keep.push(x.s)}if(keep.length<6){for(const x of ranked){if(keep.length>=6)break;if(!keep.includes(x.s))keep.push(x.s)}}return [...new Set(keep)].sort()}

async function cachedMonthEndPrices(symbols,targetDate){
  const key=isoDate(targetDate);let map=priceCache.get(key);if(!map){map=new Map();priceCache.set(key,map)}
  const missing=symbols.filter(s=>!map.has(s));if(!missing.length)return map;
  const y=targetDate.getUTCFullYear(),m=targetDate.getUTCMonth(),start=new Date(Date.UTC(y,m,18)),end=new Date(Date.UTC(y,m+1,2));
  for(let i=0;i<missing.length;i+=CACHE_CHUNK){
    const part=missing.slice(i,i+CACHE_CHUNK),q=new URLSearchParams({symbols:part.join(","),timeframe:"1Day",start:isoDate(start),end:isoDate(end),adjustment:"all",feed:"iex",limit:"10000",sort:"asc"}),data=await alpaca(`/stocks/bars?${q}`);
    for(const s of part)map.set(s,NaN);
    for(const[s,bars]of Object.entries(data.bars||{})){const valid=bars.filter(b=>new Date(b.t)<=targetDate);if(valid.length)map.set(normalizeTicker(s),Number(valid.at(-1).c))}
  }
  return map;
}
async function rankCore(rebalanceYM){
  const universeDate=monthEndDate(addMonths(rebalanceYM,-1)),recentEnd=monthEndDate(addMonths(rebalanceYM,-1)),earlyEnd=monthEndDate(addMonths(rebalanceYM,-6)),uni=await universeAt(universeDate),[pr,pe]=await Promise.all([cachedMonthEndPrices(uni,recentEnd),cachedMonthEndPrices(uni,earlyEnd)]),ranked=[];
  for(const s of uni){const a=pr.get(s),b=pe.get(s);if(Number.isFinite(a)&&Number.isFinite(b)&&b>0)ranked.push({s,score:a/b-1})}
  ranked.sort((a,b)=>b.score-a.score);if(ranked.length<16)throw new Error(`${rebalanceYM} 모멘텀 랭킹 데이터 부족`);return{ranked,universe:new Set(uni)};
}
function marker(){try{return JSON.parse(localStorage.getItem(MARKER_KEY)||"null")}catch{return null}}
function markDone(convergedMonth){try{localStorage.setItem(MARKER_KEY,JSON.stringify({version:1,start:BACKFILL_START,checkpoint:CHECKPOINT_MONTH,convergedMonth,completedAt:new Date().toISOString()}))}catch{}}
async function waitHistoryIdle(){for(let i=0;i<120&&historyLoading;i++)await new Promise(r=>setTimeout(r,250))}

async function backfillCoreHistory(){
  if(running||activeStrategy?.mode!=="core"||!creds)return;
  const done=marker();if(done?.version===1&&holdingsHistory?.[done.convergedMonth]){await waitHistoryIdle();await ensureMonthlyHistory();renderHistory();return}
  running=true;
  try{
    status("SOMX Core 과거 canonical 기록 준비 중…");
    const first=await rankCore(BACKFILL_START),top16=first.ranked.slice(0,16).map(x=>x.s);let states=combinations6(top16).map(x=>[...x].sort()),convergedMonth=null,canonicalHistory={};
    const months=monthRange(addMonths(BACKFILL_START,1),CHECKPOINT_MONTH);
    for(let i=0;i<months.length;i++){
      const month=months[i];status(`Core 과거 기록 동기화 · ${month} · ${states.length.toLocaleString()} states`);
      const r=await rankCore(month),next=new Map();for(const s of states){const t=transitionSet(s,r.ranked,r.universe);next.set(stateKey(t),t)}states=[...next.values()];
      if(states.length===1){if(!convergedMonth)convergedMonth=month;canonicalHistory[month]=[...states[0]].sort((a,b)=>(r.ranked.findIndex(x=>x.s===a))-(r.ranked.findIndex(x=>x.s===b)))}
      await new Promise(requestAnimationFrame);
    }
    if(states.length!==1||!canonicalHistory[CHECKPOINT_MONTH])throw new Error("Core canonical state가 checkpoint까지 수렴하지 않았습니다.");
    const expected=seededCoreHistory?.[CHECKPOINT_MONTH];if(!sameSet(canonicalHistory[CHECKPOINT_MONTH],expected))throw new Error(`과거 데이터가 현재 Core checkpoint(${CHECKPOINT_MONTH})와 불일치하여 기록을 적용하지 않았습니다.`);
    for(const[month,names]of Object.entries(canonicalHistory)){if(month>=CHECKPOINT_MONTH)continue;holdingsHistory[month]=[...names];weightsHistory[month]=equalWeights(names)}
    saveHoldingsHistory();saveWeightsHistory();markDone(convergedMonth);window.dispatchEvent(new Event("somx:historychange"));
    status(`Core canonical 기록 ${convergedMonth}부터 확장 완료 · 월별 수익률 계산 중…`);await waitHistoryIdle();await ensureMonthlyHistory();renderHistory();const count=Object.keys(monthlyHistory).length;status(`각 달 첫 거래일 시가 → 마지막 거래일 종가 · ${count}개월 기록`);
  }catch(e){console.error("core backfill",e);status(e?.message||"Core 과거 기록 확장 실패");}
  finally{running=false}
}

const historyBtn=document.getElementById("historyBtn");
historyBtn?.addEventListener("click",e=>{e.stopImmediatePropagation();document.getElementById("history-modal")?.classList.add("show");renderHistory();if(!creds)return;if(activeStrategy?.mode==="core")backfillCoreHistory();else ensureMonthlyHistory();},true);

globalThis.SOMXCoreBackfill={run:backfillCoreHistory};
})();