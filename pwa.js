if ("serviceWorker" in navigator) {
  window.addEventListener("load", async () => {
    try {
      const reg = await navigator.serviceWorker.register("./sw.js?v=4", {updateViaCache:"none"});
      await reg.update();
    } catch (e) {
      console.error(e);
    }
  });
}
