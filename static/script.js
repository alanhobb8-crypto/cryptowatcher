// static/script.js
(() => {
  const API_BASE = "";

  let wallets = [];
  let selectedWalletId = null;
  let autoCheckTimer = null;

  let sortField = "usd";
  let sortDirection = "desc"; // "asc" | "desc"

  let filterSearch = "";
  let filterMinUsd = null;
  let filterMaxUsd = null;

  const chainDisplayMode = {
    BTC: "usd",
    ETH: "usd",
    TRX: "usd",
  };

  const depositAudio = new Audio("/static/cashier.mp3");
  depositAudio.volume = 0.85;

  // DOM refs
  const totalPortfolioEl = document.getElementById("total-portfolio-usd");
  const chainChipsEl = document.getElementById("chain-chips");
  const walletCountEl = document.getElementById("wallet-count");
  const walletTbodyEl = document.getElementById("wallet-tbody");
  const emptyStateEl = document.getElementById("empty-state");

  const addChainEl = document.getElementById("add-chain");
  const addAddressEl = document.getElementById("add-address");
  const addLabelEl = document.getElementById("add-label");
  const addNotesEl = document.getElementById("add-notes");
  const addWalletBtn = document.getElementById("add-wallet-btn");

  const bulkChainEl = document.getElementById("bulk-chain");
  const bulkLinesEl = document.getElementById("bulk-lines");
  const bulkImportBtn = document.getElementById("bulk-import-btn");
  const deleteAllBtn = document.getElementById("delete-all-btn");

  const autoToggleEl = document.getElementById("auto-toggle");
  const autoIntervalEl = document.getElementById("auto-interval");
  const checkNowBtn = document.getElementById("check-now-btn");

  const filterSearchEl = document.getElementById("filter-search");
  const filterMinUsdEl = document.getElementById("filter-min-usd");
  const filterMaxUsdEl = document.getElementById("filter-max-usd");
  const sortFieldEl = document.getElementById("sort-field");
  const sortDirectionBtn = document.getElementById("sort-direction-btn");
  const sortDirectionIcon = document.getElementById("sort-direction-icon");

  const toastContainer = document.getElementById("toast-container");

  const editModalEl = document.getElementById("edit-modal");
  const editModalClose = document.getElementById("edit-modal-close");
  const editModalSave = document.getElementById("edit-modal-save");
  const editModalCancel = document.getElementById("edit-modal-cancel");
  const editLabelEl = document.getElementById("edit-label");
  const editNotesEl = document.getElementById("edit-notes");

  let editWalletId = null;

  // Utils
  function formatUSD(v) {
    const num = Number(v) || 0;
    return num.toLocaleString(undefined, {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 2,
    });
  }

  function formatCoin(chainOrSymbol, v) {
    const num = Number(v) || 0;
    let maxFraction = 8;
    if (chainOrSymbol === "TRX" || chainOrSymbol === "USDT" || chainOrSymbol === "USDC") {
      maxFraction = 6;
    }
    return num.toLocaleString(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits: maxFraction,
    });
  }

  function shortAddress(addr) {
    if (!addr) return "";
    if (addr.length <= 16) return addr;
    return addr.slice(0, 6) + "…" + addr.slice(-6);
  }

  function clampInterval(seconds) {
    let s = Number(seconds) || 60;
    if (s < 15) s = 15;
    if (s > 3600) s = 3600;
    return s;
  }

  function walletTotalUsd(w) {
    const native = Number(w.usd_balance || 0);
    const tokens = (w.tokens || []).reduce(
      (sum, t) => sum + Number(t.usd_balance || 0),
      0
    );
    return native + tokens;
  }

  // Toast / notifications
  function pushToast({ type, title, body, meta, onClick, timeout = 8000 }) {
    const toast = document.createElement("div");
    toast.className = "toast" + (type === "deposit" ? " toast-deposit" : "");
    const icon = document.createElement("div");
    icon.className = "toast-icon";
    icon.textContent = type === "deposit" ? "💸" : "🔄";
    const content = document.createElement("div");
    content.className = "toast-content";

    const titleEl = document.createElement("div");
    titleEl.className = "toast-title";
    titleEl.textContent = title;

    const bodyEl = document.createElement("div");
    bodyEl.className = "toast-body";
    bodyEl.textContent = body;

    const metaEl = document.createElement("div");
    metaEl.className = "toast-meta";
    metaEl.textContent = meta || "";

    content.appendChild(titleEl);
    content.appendChild(bodyEl);
    if (meta) content.appendChild(metaEl);

    toast.appendChild(icon);
    toast.appendChild(content);

    let timeoutId = null;

    function removeToast() {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
      if (timeoutId) clearTimeout(timeoutId);
    }

    toast.addEventListener("click", () => {
      if (onClick) onClick();
      removeToast();
    });

    toastContainer.appendChild(toast);

    if (timeout > 0) {
      timeoutId = setTimeout(removeToast, timeout);
    }
  }

  function ensureNotificationPermission() {
    if (!("Notification" in window)) return;
    if (Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
  }

  function showDesktopDepositNotification({ title, body }) {
    if (!("Notification" in window)) return;
    if (Notification.permission !== "granted") return;
    try {
      new Notification(title, {
        body,
        icon: "/static/favicon1.png",
      });
    } catch {
      // ignore
    }
  }

  // Totals & header
  function computeTotals() {
    const totals = {
      overallUsd: 0,
      perChain: {
        BTC: { coin: 0, usd: 0 },
        ETH: { coin: 0, usd: 0 },
        TRX: { coin: 0, usd: 0 },
      },
    };
    for (const w of wallets) {
      const chain = w.chain;
      const nativeCoin = Number(w.coin_balance || 0);
      const nativeUsd = Number(w.usd_balance || 0);
      const tokenUsd = (w.tokens || []).reduce(
        (sum, t) => sum + Number(t.usd_balance || 0),
        0
      );
      const totalUsd = nativeUsd + tokenUsd;

      totals.overallUsd += totalUsd;

      if (totals.perChain[chain]) {
        totals.perChain[chain].coin += nativeCoin;
        totals.perChain[chain].usd += totalUsd;
      }
    }
    return totals;
  }

  function renderHeader() {
    const totals = computeTotals();
    totalPortfolioEl.textContent = formatUSD(totals.overallUsd);

    chainChipsEl.innerHTML = "";
    const chains = ["BTC", "ETH", "TRX"];
    chains.forEach((chain) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chain-chip";
      const dot = document.createElement("span");
      dot.className = "chain-dot chain-dot-" + chain.toLowerCase();

      const main = document.createElement("div");
      main.className = "chain-chip-main";
      const label = document.createElement("div");
      label.className = "chain-chip-label";
      label.textContent = chain + ": OK";

      const value = document.createElement("div");
      value.className = "chain-chip-value";
      const mode = chainDisplayMode[chain] || "usd";

      if (mode === "coin") {
        value.textContent = formatCoin(chain, totals.perChain[chain].coin);
      } else {
        value.textContent = formatUSD(totals.perChain[chain].usd);
      }
      const modeSpan = document.createElement("span");
      modeSpan.className = "chain-chip-mode";
      modeSpan.textContent = mode === "coin" ? "coin" : "USD";
      value.appendChild(modeSpan);

      main.appendChild(label);
      main.appendChild(value);

      chip.appendChild(dot);
      chip.appendChild(main);

      chip.addEventListener("click", () => {
        chainDisplayMode[chain] = mode === "coin" ? "usd" : "coin";
        renderHeader();
      });

      chainChipsEl.appendChild(chip);
    });
  }

  // Filtering & sorting
  function currentFilteredSortedWallets() {
    let filtered = wallets.slice();

    if (filterSearch) {
      const q = filterSearch.toLowerCase();
      filtered = filtered.filter(
        (w) =>
          (w.label || "").toLowerCase().includes(q) ||
          (w.address || "").toLowerCase().includes(q)
      );
    }

    if (filterMinUsd != null) {
      filtered = filtered.filter((w) => walletTotalUsd(w) >= filterMinUsd);
    }
    if (filterMaxUsd != null) {
      filtered = filtered.filter((w) => walletTotalUsd(w) <= filterMaxUsd);
    }

    filtered.sort((a, b) => {
      let av, bv;
      switch (sortField) {
        case "usd":
          av = walletTotalUsd(a);
          bv = walletTotalUsd(b);
          break;
        case "balance":
          av = Number(a.coin_balance || 0);
          bv = Number(b.coin_balance || 0);
          break;
        case "chain":
          av = a.chain;
          bv = b.chain;
          break;
        case "label":
          av = (a.label || "").toLowerCase();
          bv = (b.label || "").toLowerCase();
          break;
        case "address":
          av = (a.address || "").toLowerCase();
          bv = (b.address || "").toLowerCase();
          break;
        default:
          av = walletTotalUsd(a);
          bv = walletTotalUsd(b);
      }

      if (av < bv) return sortDirection === "asc" ? -1 : 1;
      if (av > bv) return sortDirection === "asc" ? 1 : -1;
      return 0;
    });

    return filtered;
  }

  // Rendering
  function renderWalletTable({ depositIds = [] } = {}) {
    const filtered = currentFilteredSortedWallets();
    walletTbodyEl.innerHTML = "";

    walletCountEl.textContent =
      filtered.length + (filtered.length === 1 ? " wallet" : " wallets");

    if (filtered.length === 0) {
      emptyStateEl.style.display = "block";
    } else {
      emptyStateEl.style.display = "none";
    }

    filtered.forEach((w) => {
      const tr = document.createElement("tr");
      tr.dataset.walletRowId = String(w.id);
      tr.classList.add("wallet-row-" + w.chain.toLowerCase());
      if (depositIds.includes(w.id)) {
        tr.classList.add("deposit-pulse");
      }
      if (selectedWalletId === w.id) {
        tr.classList.add("selected-row");
      }

      // Chain
      const tdChain = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = "chain-badge";
      const dot = document.createElement("span");
      dot.className =
        "dot dot-" + w.chain.toLowerCase();
      const txt = document.createElement("span");
      txt.textContent = w.chain;
      badge.appendChild(dot);
      badge.appendChild(txt);
      tdChain.appendChild(badge);

      // Label + tokens
      const tdLabel = document.createElement("td");
      const labelEl = document.createElement("span");
      labelEl.className = "wallet-label";
      labelEl.textContent = w.label || "(no label)";
      tdLabel.appendChild(labelEl);

      if (w.tokens && w.tokens.length > 0) {
        const tokenStrip = document.createElement("div");
        tokenStrip.className = "token-strip";
        w.tokens.forEach((t) => {
          const pill = document.createElement("span");
          pill.className = "token-pill";
          const coinStr = formatCoin(t.symbol, t.coin_balance);
          const usdStr = formatUSD(t.usd_balance);
          pill.textContent = `${t.symbol} (${t.standard}) · ${coinStr} · ${usdStr}`;
          tokenStrip.appendChild(pill);
        });
        tdLabel.appendChild(tokenStrip);
      }

      // Address
      const tdAddr = document.createElement("td");
      const addrEl = document.createElement("span");
      addrEl.className = "wallet-address";
      addrEl.textContent = shortAddress(w.address);
      tdAddr.appendChild(addrEl);

      // Native balance
      const tdBalance = document.createElement("td");
      tdBalance.className = "numeric";
      tdBalance.textContent = formatCoin(w.chain, w.coin_balance);

      // USD (native + tokens)
      const tdUsd = document.createElement("td");
      tdUsd.className = "numeric";
      tdUsd.textContent = formatUSD(walletTotalUsd(w));

      // Actions
      const tdActions = document.createElement("td");
      tdActions.className = "actions-cell";

      const editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "icon-btn";
      editBtn.textContent = "Edit";
      editBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openEditModal(w);
      });

      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "icon-btn danger";
      delBtn.textContent = "Delete";
      delBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteWallet(w.id);
      });

      tdActions.appendChild(editBtn);
      tdActions.appendChild(delBtn);

      tr.appendChild(tdChain);
      tr.appendChild(tdLabel);
      tr.appendChild(tdAddr);
      tr.appendChild(tdBalance);
      tr.appendChild(tdUsd);
      tr.appendChild(tdActions);

      tr.addEventListener("click", () => {
        selectedWalletId = w.id;
        renderWalletTable();
      });

      tr.addEventListener("dblclick", () => {
        if (!w.notes) return;
        pushToast({
          type: "info",
          title: w.label || shortAddress(w.address),
          body: w.notes,
          meta: "Notes",
          timeout: 12000,
        });
      });

      walletTbodyEl.appendChild(tr);
    });
  }

  function renderAll(options = {}) {
    renderHeader();
    renderWalletTable(options);
  }

  // API helpers
  async function apiGet(path) {
    const res = await fetch(API_BASE + path);
    if (!res.ok) {
      throw new Error("HTTP " + res.status);
    }
    return res.json();
  }

  async function apiPost(path, body) {
    const res = await fetch(API_BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : null,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error("HTTP " + res.status + ": " + text);
    }
    return res.json();
  }

  async function apiPut(path, body) {
    const res = await fetch(API_BASE + path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error("HTTP " + res.status + ": " + text);
    }
    return res.json();
  }

  async function apiDelete(path) {
    const res = await fetch(API_BASE + path, { method: "DELETE" });
    if (!res.ok) {
      const text = await res.text();
      throw new Error("HTTP " + res.status + ": " + text);
    }
    return res.json();
  }

  // Load wallets
  async function loadWallets() {
    try {
      const data = await apiGet("/api/wallets");
      wallets = data || [];
      renderAll();
    } catch (err) {
      console.error("Failed to load wallets", err);
      pushToast({
        type: "info",
        title: "Error loading wallets",
        body: "Check the backend logs for details.",
        meta: "",
        timeout: 8000,
      });
    }
  }

  // Add wallet
  async function addWallet() {
    const chain = addChainEl.value;
    const address = addAddressEl.value.trim();
    const label = addLabelEl.value.trim();
    const notes = addNotesEl.value.trim();

    if (!address) {
      addAddressEl.focus();
      return;
    }

    try {
      const created = await apiPost("/api/wallets", {
        chain,
        address,
        label,
        notes,
      });
      wallets.push({
        ...created,
        raw_balance: created.last_raw_balance || 0,
        coin_balance: 0,
        usd_balance: 0,
        tokens: [],
      });
      addAddressEl.value = "";
      addLabelEl.value = "";
      addNotesEl.value = "";
      renderAll();
    } catch (err) {
      console.error("Add wallet failed", err);
      pushToast({
        type: "info",
        title: "Failed to add wallet",
        body: "Make sure the chain and address are valid.",
        meta: "",
      });
    }
  }

  // Bulk import
  async function bulkImport() {
    const chain = bulkChainEl.value;
    const lines = bulkLinesEl.value.trim();
    if (!lines) {
      bulkLinesEl.focus();
      return;
    }
    try {
      const created = await apiPost("/api/wallets/bulk", { chain, lines });
      created.forEach((w) => {
        wallets.push({
          ...w,
          raw_balance: w.last_raw_balance || 0,
          coin_balance: 0,
          usd_balance: 0,
          tokens: [],
        });
      });
      bulkLinesEl.value = "";
      renderAll();
    } catch (err) {
      console.error("Bulk import failed", err);
      pushToast({
        type: "info",
        title: "Bulk import failed",
        body: "Check your input format.",
        meta: "",
      });
    }
  }

  // Delete wallet
  async function deleteWallet(id) {
    if (!confirm("Delete this wallet from your local list?")) return;
    try {
      await apiDelete(`/api/wallets/${id}`);
      wallets = wallets.filter((w) => w.id !== id);
      if (selectedWalletId === id) {
        selectedWalletId = null;
      }
      renderAll();
    } catch (err) {
      console.error("Delete wallet failed", err);
      pushToast({
        type: "info",
        title: "Failed to delete wallet",
        body: "Check backend logs.",
        meta: "",
      });
    }
  }

  // Delete all
  async function deleteAllWallets() {
    if (!confirm("Delete ALL wallets from your local list?")) return;
    try {
      await apiDelete("/api/wallets");
      wallets = [];
      selectedWalletId = null;
      renderAll();
    } catch (err) {
      console.error("Delete all failed", err);
      pushToast({
        type: "info",
        title: "Failed to delete all wallets",
        body: "Check backend logs.",
        meta: "",
      });
    }
  }

  // Edit modal
  function openEditModal(wallet) {
    editWalletId = wallet.id;
    editLabelEl.value = wallet.label || "";
    editNotesEl.value = wallet.notes || "";
    editModalEl.classList.remove("hidden");
    editLabelEl.focus();
  }

  function closeEditModal() {
    editWalletId = null;
    editModalEl.classList.add("hidden");
  }

  async function saveEditModal() {
    if (!editWalletId) return;
    const label = editLabelEl.value;
    const notes = editNotesEl.value;
    try {
      const updated = await apiPut(`/api/wallets/${editWalletId}`, {
        label,
        notes,
      });
      wallets = wallets.map((w) =>
        w.id === updated.id ? { ...w, ...updated } : w
      );
      renderAll();
      closeEditModal();
    } catch (err) {
      console.error("Update wallet failed", err);
      pushToast({
        type: "info",
        title: "Failed to update wallet",
        body: "Check backend logs.",
        meta: "",
      });
    }
  }

  // Auto check
  function startAutoCheck() {
    const interval = clampInterval(autoIntervalEl.value);
    autoIntervalEl.value = String(interval);
    if (autoCheckTimer) {
      clearInterval(autoCheckTimer);
    }
    autoCheckTimer = setInterval(() => {
      triggerCheck(false);
    }, interval * 1000);
  }

  function stopAutoCheck() {
    if (autoCheckTimer) {
      clearInterval(autoCheckTimer);
      autoCheckTimer = null;
    }
  }

  // Check balances
  async function triggerCheck(manual = true) {
    if (wallets.length === 0) {
      pushToast({
        type: "info",
        title: "No wallets to check",
        body: "Add a wallet first.",
        meta: "",
        timeout: 4000,
      });
      return;
    }

    const prevById = new Map();
    wallets.forEach((w) => {
      prevById.set(w.id, JSON.parse(JSON.stringify(w)));
    });

    try {
      const res = await apiPost("/api/check", {});
      const newWallets = res.wallets || [];
      const deposits = res.deposits || [];
      const totalUsd = Number(res.total_usd || 0);

      wallets = newWallets;

      let changedCount = 0;
      const changedIds = [];
      for (const w of wallets) {
        const prev = prevById.get(w.id);
        if (!prev) {
          changedCount++;
          changedIds.push(w.id);
        } else {
          const rawChanged =
            Number(prev.raw_balance || 0) !== Number(w.raw_balance || 0);
          const totalUsdPrev = walletTotalUsd(prev);
          const totalUsdCurr = walletTotalUsd(w);
          if (rawChanged || totalUsdPrev !== totalUsdCurr) {
            changedCount++;
            changedIds.push(w.id);
          }
        }
      }

      // Deposits (native coin-based)
      if (deposits.length > 0) {
        deposits.forEach((id) => {
          const newW = wallets.find((w) => w.id === id);
          const prevW = prevById.get(id) || {
            coin_balance: 0,
            usd_balance: 0,
            tokens: [],
          };
          if (!newW) return;
          const diffCoin =
            Number(newW.coin_balance || 0) - Number(prevW.coin_balance || 0);
          const diffUsd =
            walletTotalUsd(newW) - walletTotalUsd(prevW);

          if (diffCoin <= 0 && diffUsd <= 0) return;

          try {
            depositAudio.currentTime = 0;
            const p = depositAudio.play();
            if (p && typeof p.then === "function") p.catch(() => {});
          } catch {
            // ignore
          }

          const coinStr = formatCoin(newW.chain, diffCoin > 0 ? diffCoin : newW.coin_balance);
          const usdStr = formatUSD(diffUsd > 0 ? diffUsd : walletTotalUsd(newW));

          showDesktopDepositNotification({
            title: "Deposit detected",
            body: `${coinStr} on ${newW.chain} · ${shortAddress(newW.address)}`,
          });

          pushToast({
            type: "deposit",
            title: "Deposit detected",
            body: newW.label || shortAddress(newW.address),
            meta: `${coinStr} · ${usdStr}`,
            onClick: () => {
              selectedWalletId = newW.id;
              const row = document.querySelector(
                `[data-wallet-row-id="${newW.id}"]`
              );
              if (row && row.scrollIntoView) {
                row.scrollIntoView({ behavior: "smooth", block: "center" });
              }
              renderWalletTable();
            },
            timeout: 9000,
          });
        });
      }

      if (changedCount > 0) {
        pushToast({
          type: "info",
          title: "Balances updated",
          body:
            changedCount === 1
              ? "1 wallet changed"
              : `${changedCount} wallets changed`,
          meta: `Portfolio ${formatUSD(totalUsd)}`,
          timeout: 7000,
        });
      }

      renderAll({ depositIds: deposits });
    } catch (err) {
      console.error("Check failed", err);
      pushToast({
        type: "info",
        title: "Check failed",
        body: "Could not refresh balances. Network or upstream issue.",
        meta: "",
      });
    }
  }

  // Events
  function attachEvents() {
    addWalletBtn.addEventListener("click", addWallet);
    addAddressEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        addWallet();
      }
    });

    bulkImportBtn.addEventListener("click", bulkImport);
    deleteAllBtn.addEventListener("click", deleteAllWallets);

    autoToggleEl.addEventListener("change", () => {
      if (autoToggleEl.checked) {
        startAutoCheck();
      } else {
        stopAutoCheck();
      }
    });

    autoIntervalEl.addEventListener("change", () => {
      if (autoToggleEl.checked) {
        startAutoCheck();
      }
    });

    checkNowBtn.addEventListener("click", () => triggerCheck(true));

    filterSearchEl.addEventListener("input", () => {
      filterSearch = filterSearchEl.value.trim();
      renderWalletTable();
    });

    filterMinUsdEl.addEventListener("input", () => {
      const v = filterMinUsdEl.value;
      filterMinUsd = v === "" ? null : Number(v);
      renderWalletTable();
    });

    filterMaxUsdEl.addEventListener("input", () => {
      const v = filterMaxUsdEl.value;
      filterMaxUsd = v === "" ? null : Number(v);
      renderWalletTable();
    });

    sortFieldEl.addEventListener("change", () => {
      sortField = sortFieldEl.value;
      renderWalletTable();
    });

    sortDirectionBtn.addEventListener("click", () => {
      sortDirection = sortDirection === "asc" ? "desc" : "asc";
      sortDirectionIcon.textContent = sortDirection === "asc" ? "↑" : "↓";
      renderWalletTable();
    });

    document
      .querySelectorAll(".wallet-table th[data-sort-field]")
      .forEach((th) => {
        th.addEventListener("click", () => {
          const field = th.getAttribute("data-sort-field");
          if (!field) return;
          if (sortField === field) {
            sortDirection = sortDirection === "asc" ? "desc" : "asc";
          } else {
            sortField = field;
            sortFieldEl.value = field;
            sortDirection = "desc";
          }
          sortDirectionIcon.textContent = sortDirection === "asc" ? "↑" : "↓";
          renderWalletTable();
        });
      });

    // Modal
    editModalClose.addEventListener("click", closeEditModal);
    editModalCancel.addEventListener("click", closeEditModal);
    editModalSave.addEventListener("click", saveEditModal);
    editModalEl.addEventListener("click", (e) => {
      if (e.target === editModalEl) {
        closeEditModal();
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !editModalEl.classList.contains("hidden")) {
        closeEditModal();
      }
    });
  }

  function init() {
    ensureNotificationPermission();
    attachEvents();
    loadWallets();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
