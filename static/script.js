// /static/script.js  — FULL, FIXED
"use strict";

let wallets = [];
let sortField = "usd_balance";
let sortDirection = "desc";
let autoCheckIntervalId = null;
let audioCtx = null;

const ASSETS = ["BTC","ETH","TRX","USDT_TRX","USDT_ETH","USDC_ETH","USDC"]; // include alias
const chainChipMode = Object.fromEntries(ASSETS.map(c=>[c,"usd"]));

function $(id){ return document.getElementById(id); }
const qs = (sel,root=document)=>root.querySelector(sel);
const qsa = (sel,root=document)=>[...root.querySelectorAll(sel)];

function formatUsd(v){ const n=Number(v)||0; return n>=1000? n.toLocaleString(undefined,{style:"currency",currency:"USD",maximumFractionDigits:2,minimumFractionDigits:2}) : "$"+n.toFixed(2); }
function formatCoin(c,v){ const n=Number(v)||0; return (c.startsWith?.("USDT")||c.startsWith?.("USDC")||c==="TRX") ? n.toFixed(2) : n.toFixed(8); }
function shortAddress(a){ return !a||a.length<=12 ? (a||"") : a.slice(0,6)+"…"+a.slice(-4); }

function canonical(chain){
  const c=(chain||"").toUpperCase();
  if(c==="USDC") return "USDC_ETH";
  if(c==="USDT") return "USDT_ETH";
  return c;
}

function explorerUrl(chain, address){
  const c=canonical(chain);
  if (c==="BTC") return `https://blockstream.info/address/${address}`;
  if (c==="ETH" || c==="USDT_ETH" || c==="USDC_ETH") return `https://etherscan.io/address/${address}`;
  if (c==="TRX" || c==="USDT_TRX") return `https://tronscan.org/#/address/${address}`;
  return "#";
}

/* Clipboard */
async function copyText(text){
  try{
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
  }catch{}
  try{
    const ta=document.createElement("textarea"); ta.value=text; ta.style.position="fixed"; ta.style.left="-9999px";
    document.body.appendChild(ta); ta.focus(); ta.select();
    const ok=document.execCommand("copy"); document.body.removeChild(ta); return ok;
  }catch{ return false; }
}

function ensureAudioContext(){ if(!audioCtx){ try{ audioCtx=new (window.AudioContext||window.webkitAudioContext)(); }catch{ audioCtx=null; } } }
function beep(f,ms){ ensureAudioContext(); if(!audioCtx) return; const o=audioCtx.createOscillator(), g=audioCtx.createGain(); o.type="sine"; o.frequency.value=f; g.gain.setValueAtTime(0.001,audioCtx.currentTime); g.gain.exponentialRampToValueAtTime(0.2,audioCtx.currentTime+.01); g.gain.exponentialRampToValueAtTime(0.0001,audioCtx.currentTime+ms/1000); o.connect(g); g.connect(audioCtx.destination); o.start(); o.stop(audioCtx.currentTime+ms/1000+.02); }

function notifyDeposit(w, amt){
  if(!("Notification" in window)) return;
  if(Notification.permission==="default"){ Notification.requestPermission(); return; }
  if(Notification.permission!=="granted") return;
  const body=`${formatCoin(w.chain,amt)} received · ${shortAddress(w.address)}`;
  try{ new Notification("Deposit detected",{ body, icon:"/static/favicon1.png" }); }catch{}
}

function setChainStatus(status){
  qsa(".chip").forEach(chip=>{
    const chain=chip.dataset.chain;
    const el=qs(".chip-status", chip);
    const st=(status&&status[canonical(chain)])||{status:"ok",cooldown_remaining:0};
    el.textContent = st.status==="cooldown" ? `COOLDOWN ${st.cooldown_remaining}s` : "OK";
  });
}

function addNotif({type,title,body,meta}){
  const c=$("notifications-container"); if(!c) return;
  const card=document.createElement("div"); card.className="notification-card "+(type||"");
  const t=document.createElement("div"); t.className="notif-title"; t.textContent=title||"";
  const b=document.createElement("div"); b.textContent=body||"";
  const m=document.createElement("div"); m.className="notif-meta"; m.textContent=meta||"";
  card.append(t,b,m); c.append(card);
  const timer=setTimeout(()=>card.remove(),8000);
  card.addEventListener("click",()=>{ clearTimeout(timer); card.remove(); });
}

function totals(){
  const keys=["BTC","ETH","TRX","USDT_TRX","USDT_ETH","USDC_ETH"];
  const t={overallUsd:0, per:Object.fromEntries(keys.map(k=>[k,{coin:0,usd:0}]))};
  for(const w of wallets){
    const c=canonical(w.chain);
    const usd=+w.usd_balance||0, coin=+w.coin_balance||0;
    t.overallUsd+=usd;
    if(t.per[c]){ t.per[c].usd+=usd; t.per[c].coin+=coin; }
  }
  return t;
}

function renderHeader(){
  const t=totals();
  $("total-portfolio-usd").textContent = formatUsd(t.overallUsd);
  qsa(".chip").forEach(chip=>{
    const chain=chip.dataset.chain; const c=canonical(chain);
    const mode=chainChipMode[chain]||"usd";
    const v=qs(`.chip-value[data-chain="${chain}"]`, chip);
    const agg=t.per[c] || {coin:0, usd:0};
    v.textContent = mode==="coin" ? formatCoin(c, agg.coin) : formatUsd(agg.usd);
    chip.setAttribute("aria-pressed", String(mode==="coin"));
  });
}

function filterList(){
  const q=($("filter-search")?.value||"").trim().toLowerCase();
  const min=parseFloat($("filter-min-usd")?.value), max=parseFloat($("filter-max-usd")?.value);
  return wallets.filter(w=>{
    const usd=+w.usd_balance||0;
    if(!Number.isNaN(min) && usd<min) return false;
    if(!Number.isNaN(max) && usd>max) return false;
    if(q){
      const l=(w.label||"").toLowerCase(), a=(w.address||"").toLowerCase();
      if(!l.includes(q) && !a.includes(q)) return false;
    }
    return true;
  });
}

function sortList(list){
  const dir=sortDirection==="asc"?1:-1, f=sortField;
  return [...list].sort((a,b)=>{
    let va=a[f], vb=b[f];
    if(f==="usd_balance"||f==="coin_balance"){ va=+va||0; vb=+vb||0; return (va-vb)*dir; }
    const sa=String(va||"").toLowerCase(), sb=String(vb||"").toLowerCase();
    return sa<sb? -1*dir : sa>sb? 1*dir : 0;
  });
}

function chainClass(chain){
  const c=canonical(chain);
  if (c==="BTC") return "btc";
  if (c==="ETH") return "eth";
  if (c==="TRX") return "trx";
  if (c==="USDT_TRX" || c==="USDT_ETH") return "usdt";
  if (c==="USDC_ETH") return "usdc";
  return "eth";
}

function getDotColor(cls){
  const css = getComputedStyle(document.documentElement);
  if (cls==="btc") return css.getPropertyValue("--btc").trim();
  if (cls==="eth") return css.getPropertyValue("--eth").trim();
  if (cls==="trx") return css.getPropertyValue("--trx").trim();
  if (cls==="usdt") return css.getPropertyValue("--usdt").trim();
  if (cls==="usdc") return css.getPropertyValue("--usdc").trim();
  return "#999";
}

function renderCards(list){
  const wrap=$("wallet-cards"); const count=$("wallet-count");
  wrap.innerHTML=""; count.textContent=`${wallets.length} wallet${wallets.length===1?"":"s"}`;

  for(const w of list){
    const cls = chainClass(w.chain);
    const card=document.createElement("div");
    card.className=`card card-${cls}`;
    card.dataset.id=String(w.id);

    const accent=document.createElement("div"); accent.className="card-accent"; card.append(accent);

    const head=document.createElement("div"); head.className="card-head";
    const badge=document.createElement("div"); badge.className="badge";
    const dot=document.createElement("span"); dot.className="dot"; dot.style.background = getDotColor(cls);
    const sym=document.createElement("span"); sym.textContent=canonical(w.chain).replace("_","-");
    badge.append(dot,sym);
    const label=document.createElement("div"); label.textContent=w.label || "—"; label.style.marginLeft="auto";
    head.append(badge,label);

    const addr=document.createElement("div"); addr.className="addr"; addr.textContent=shortAddress(w.address); addr.title=w.address;

    const row1=document.createElement("div"); row1.className="kv";
    row1.innerHTML = `<div class="label">Balance</div><div class="value">${formatCoin(canonical(w.chain),w.coin_balance)}</div>`;

    const row2=document.createElement("div"); row2.className="kv";
    row2.innerHTML = `<div class="label">USD</div><div class="value">${formatUsd(w.usd_balance)}</div>`;

    const actions=document.createElement("div"); actions.className="actions";
    const left=document.createElement("div"); left.className="actions-left";
    const right=document.createElement("div"); right.className="actions-right";

    const copy=document.createElement("button");
    copy.className="btn small icon copy-btn";
    copy.innerHTML = `<span class="i">⎘</span><span class="t">Copy</span>`;
    copy.title = "Copy address"; copy.ariaLabel = "Copy address";

    const explorer=document.createElement("button");
    explorer.className="btn small icon explorer-btn";
    explorer.innerHTML = `<span class="i">↗</span><span class="t">Explorer</span>`;
    explorer.title = "Open in explorer"; explorer.ariaLabel = "Open in explorer";

    const edit=document.createElement("button");
    edit.className="btn small icon edit-btn";
    edit.innerHTML = `<span class="i">✎</span><span class="t">Edit</span>`;

    const del=document.createElement("button");
    del.className="btn small icon danger delete-btn";
    del.innerHTML = `<span class="i">␡</span><span class="t">Delete</span>`;

    left.append(copy, explorer); right.append(edit, del);
    actions.append(left, right);

    card.append(head,addr,row1,row2,actions);
    wrap.append(card);
  }
}

function renderAll(){ renderHeader(); renderCards(sortList(filterList())); }

/* API */
async function loadWallets(){
  try{
    const r=await fetch("/api/wallets"); const d=await r.json();
    wallets = Array.isArray(d)? d : Array.isArray(d.wallets)? d.wallets : [];
  }catch{ wallets=[]; }
  renderAll();
}

async function addWallet(){
  // FIX: the old code used `and` (Python) instead of `&&` (JS) → broke the whole app
  const chain=qs("#add-chain").value, address=qs("#add-address").value.trim(), label=qs("#add-label").value.trim(), notes=qs("#add-notes").value.trim();
  if(!address){ alert("Address is required."); return; }
  try{
    const r=await fetch("/api/wallets",{ method:"POST", headers:{ "Content-Type":"application/json" }, body:JSON.stringify({ chain, address, label, notes }) });
    if(!r.ok){ const msg=(await r.json())?.detail || "Failed to add wallet"; alert(msg); return; }
    // success path
    await r.json(); // no-op; ensure body consumed in dev tools
    ["#add-address","#add-label","#add-notes"].forEach(s=>qs(s).value="");
    await loadWallets();
  }catch(e){
    console.error(e);
  }
}

async function bulkImport(){
  const chain=qs("#bulk-chain").value, lines=qs("#bulk-lines").value;
  if(!lines.trim()){ alert("Paste at least one line."); return; }
  try{ await fetch("/api/wallets/bulk",{ method:"POST", headers:{ "Content-Type":"application/json" }, body:JSON.stringify({ chain,lines }) }); qs("#bulk-lines").value=""; await loadWallets(); }catch{}
}
async function deleteAllWallets(){ if(!confirm("Delete ALL wallets?")) return; try{ await fetch("/api/wallets",{ method:"DELETE" }); wallets=[]; renderAll(); }catch{} }
async function deleteWallet(id){ if(!confirm("Delete this wallet?")) return; try{ await fetch(`/api/wallets/${id}`,{ method:"DELETE" }); wallets=wallets.filter(x=>x.id!==id); renderAll(); }catch{} }

/* Modal */
let editingId=null;
function openModal(w){ editingId=w.id; $("edit-label").value=w.label||""; $("edit-notes").value=w.notes||""; $("edit-modal-backdrop").classList.remove("hidden"); $("edit-label").focus(); }
function closeModal(){ editingId=null; $("edit-modal-backdrop").classList.add("hidden"); }
async function saveModal(){
  if(editingId==null) return;
  const label=$("edit-label").value, notes=$("edit-notes").value;
  try{
    const r=await fetch(`/api/wallets/${editingId}`,{ method:"PUT", headers:{ "Content-Type":"application/json" }, body:JSON.stringify({ label,notes }) });
    const u=await r.json(); wallets=wallets.map(w=>w.id===u.id?{...w,...u}:w); renderAll();
  }catch{} closeModal();
}

/* Check */
async function runCheck(manual){
  const prev=new Map(wallets.map(w=>[w.id,{ raw:+(w.raw_balance||w.last_raw_balance||0)||0, usd:+w.usd_balance||0, coin:+w.coin_balance||0 }]));
  try{
    const r=await fetch("/api/check",{ method:"POST" }); const d=await r.json();
    if(Array.isArray(d.wallets)) wallets=d.wallets; else if(Array.isArray(d)) wallets=d;
    renderAll(); setChainStatus(d.chain_status);

    const deposits=Array.isArray(d.deposits)? d.deposits : [];
    let changed=0;
    for(const w of wallets){ const p=prev.get(w.id)||{raw:0,usd:0}; const cr=+(w.raw_balance||w.last_raw_balance||0)||0; const cu=+w.usd_balance||0; if(cr!==p.raw||cu!==p.usd) changed++; }
    for(const id of deposits){
      const w=wallets.find(x=>x.id===id); if(!w) continue;
      const p=prev.get(id)||{ coin:0, usd:0 };
      const dCoin=(+w.coin_balance||0) - (+p.coin||0) || (+w.coin_balance||0);
      const dUsd=(+w.usd_balance||0) - (+p.usd||0) || (+w.usd_balance||0);
      const card=qs(`.card[data-id="${id}"]`); if(card){ card.classList.add("deposit"); setTimeout(()=>card.classList.remove("deposit"), 900); }
      beep(880,140); notifyDeposit(w, dCoin);
      addNotif({ type:"deposit", title:"Deposit detected", body:w.label||shortAddress(w.address), meta:`${formatCoin(canonical(w.chain),dCoin)} · ${formatUsd(dUsd)}` });
    }
    if(changed>0 || manual){
      const t=totals(); addNotif({ type:"updated", title:"Balances updated", body: changed>0? `${changed} wallet${changed===1?"":"s"} changed` : `${wallets.length} checked`, meta:`Portfolio ${formatUsd(t.overallUsd)}` });
      beep(520,110);
    }
  }catch(e){
    console.error(e);
  }
}

/* Auto-check */
function enableAuto(){
  const inp=$("auto-check-interval"); let s=parseInt(inp.value,10); if(Number.isNaN(s)) s=60; s=Math.min(3600,Math.max(15,s)); inp.value=String(s);
  localStorage.setItem("cw:autoCheck","1"); localStorage.setItem("cw:autoCheckInterval",String(s));
  if(autoCheckIntervalId) clearInterval(autoCheckIntervalId);
  autoCheckIntervalId=setInterval(()=>runCheck(false), s*1000);
}
function disableAuto(){ if(autoCheckIntervalId){ clearInterval(autoCheckIntervalId); autoCheckIntervalId=null; } localStorage.setItem("cw:autoCheck","0"); }

/* Events */
function wire(){
  qs("#check-now-btn")?.addEventListener("click",(e)=>{ e.preventDefault(); runCheck(true); });
  qs("#add-wallet-btn")?.addEventListener("click",(e)=>{ e.preventDefault(); addWallet(); });
  qs("#bulk-import-btn")?.addEventListener("click",(e)=>{ e.preventDefault(); bulkImport(); });
  qs("#delete-all-btn")?.addEventListener("click",(e)=>{ e.preventDefault(); deleteAllWallets(); });

  qsa(".chip").forEach(chip=>{
    chip.addEventListener("click",()=>{
      const chain=chip.dataset.chain; const cur=chainChipMode[chain]||"usd"; chainChipMode[chain] = cur==="usd" ? "coin" : "usd"; renderHeader();
    });
  });

  ["filter-search","filter-min-usd","filter-max-usd"].forEach(id=>$(id)?.addEventListener("input",()=>renderAll()));

  $("auto-check-toggle")?.addEventListener("change",(e)=>{ e.target.checked? enableAuto() : disableAuto(); });
  $("edit-cancel-btn")?.addEventListener("click",(e)=>{ e.preventDefault(); closeModal(); });
  $("edit-save-btn")?.addEventListener("click",(e)=>{ e.preventDefault(); saveModal(); });
  $("edit-modal-backdrop")?.addEventListener("click",(e)=>{ if(e.target.id==="edit-modal-backdrop") closeModal(); });

  $("wallet-cards")?.addEventListener("click", async (e)=>{
    const card=e.target.closest(".card"); if(!card) return;
    const id=parseInt(card.dataset.id,10); if(Number.isNaN(id)) return;
    const w = wallets.find(x=>x.id===id); if(!w) return;
    const btn=e.target.closest("button"); if(!btn) return;

    if(btn.classList.contains("copy-btn")){
      const ok = await copyText(w.address);
      addNotif({ type: ok ? "updated" : "error", title: ok ? "Copied!" : "Copy failed", body: shortAddress(w.address), meta: canonical(w.chain) });
      if (ok) beep(700,90);
      return;
    }
    if(btn.classList.contains("explorer-btn")){ window.open(explorerUrl(w.chain,w.address), "_blank", "noopener"); return; }
    if(btn.classList.contains("edit-btn")){ openModal(w); return; }
    if(btn.classList.contains("delete-btn")){ deleteWallet(id); return; }
  });

  const saved=localStorage.getItem("cw:autoCheck"); const savedInt=parseInt(localStorage.getItem("cw:autoCheckInterval")||"60",10);
  if(saved==="1"){ $("auto-check-toggle").checked=true; $("auto-check-interval").value=String(Math.min(3600,Math.max(15,savedInt||60))); enableAuto(); }
}

document.addEventListener("DOMContentLoaded",()=>{ wire(); loadWallets(); });
