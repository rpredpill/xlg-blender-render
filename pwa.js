const LAYOUT_KEY="somx.layout.v1";
const validLayouts=new Set(["classic","vertical"]);
function applyLayout(layout){
  const value=validLayouts.has(layout)?layout:"vertical";
  document.documentElement.dataset.layout=value;
  document.body.dataset.layout=value;
  document.querySelectorAll("[data-layout-choice]").forEach(btn=>{
    const active=btn.dataset.layoutChoice===value;
    btn.classList.toggle("active",active);
    btn.setAttribute("aria-pressed",active?"true":"false");
  });
  try{localStorage.setItem(LAYOUT_KEY,value)}catch(e){}
}
applyLayout((()=>{try{return localStorage.getItem(LAYOUT_KEY)||"vertical"}catch(e){return "vertical"}})());
document.querySelectorAll("[data-layout-choice]").forEach(btn=>{
  btn.addEventListener("click",()=>applyLayout(btn.dataset.layoutChoice));
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", async () => {
    try {
      const reg = await navigator.serviceWorker.register("./sw.js?v=5", {updateViaCache:"none"});
      await reg.update();
    } catch (e) {
      console.error(e);
    }
  });
}
