// Local stand-in for the artifact db, for trying the pages outside claude.ai.
// Load it before the page script (e.g. Playwright addInitScript). It implements only what the
// pages use: doc(path).set/update/onSnapshot and collection(path).onSnapshot, all in memory.
// window.__store holds the documents and window.__fire() pushes a snapshot to every listener,
// so a test can play "another device" by editing __store and calling __fire().
(() => {
  const store = {}, subs = [];
  const parent = (p) => p.split("/").slice(0, -1).join("/");
  const fire = () => subs.forEach((s) => s());
  const copy = (v) => JSON.parse(JSON.stringify(v));
  const docRef = (path) => ({
    path,
    set: async (d) => { store[path] = copy(d); setTimeout(fire, 20); },
    update: async (d) => { store[path] = Object.assign(store[path] || {}, copy(d)); setTimeout(fire, 20); },
    onSnapshot: (fn) => { const s = () => fn({ exists: !!store[path], data: () => store[path] && copy(store[path]) }); subs.push(s); setTimeout(s, 10); return () => {}; }
  });
  const db = {
    doc: docRef,
    collection: (cp) => ({
      onSnapshot: (fn) => {
        const s = () => fn({ docs: Object.keys(store).filter((k) => parent(k) === cp).map((k) => ({ data: () => copy(store[k]) })), metadata: { hasPendingWrites: false } });
        subs.push(s); setTimeout(s, 10); return () => {};
      }
    })
  };
  window.__store = store; window.__fire = fire;
  window.claude = { use: async (name) => (name === "db" ? db : null) };
})();
