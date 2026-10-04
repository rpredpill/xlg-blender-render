(async function(){
 const response=await fetch('./flow-backtest.json',{cache:'no-store'});if(!response.ok)return;const data=await response.json();
 for(const [id,rows] of [['annual',data.annual],['monthly',data.monthly]]){const body=document.getElementById(id);for(const row of [...rows].reverse()){const tr=document.createElement('tr');for(const value of [row.date+(row.date===data.end.slice(0,id==='annual'?4:7)?' (진행 중)':''),row.FLOW,row.SPY,row.QQQ]){const td=document.createElement('td');td.textContent=typeof value==='number'?(value>=0?'+':'')+(value*100).toFixed(2)+'%':value;tr.append(td);}body.append(tr);}}
})().catch(()=>{});
