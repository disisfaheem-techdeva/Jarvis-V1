/* TechDeva till screen (3 Nov 2026). Talks only to /pos/* (app/pos_routes.py);
   hardware is window.PosHW (pos-hw.js). Every figure shown comes from the
   server's answer, never from a guess: the bill total on screen is a preview,
   and the receipt is what the server saved. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var HW = window.PosHW;
  // the POS has its own address (pos.techdeva.net); the dashboard lives on api.
  var DASH = location.hostname.indexOf("pos.") === 0 ? location.origin.replace("//pos.", "//api.") + "/dashboard" : "/dashboard";
  var S = { items: [], cat: "", mode: "counter", cart: [], bill: null, settings: {}, isOwner: false, methods: ["cash"],
            business: "", me: "", shift: null, device: HW.device(), ref: null, discount: 0, approvals: {}, payFor: null };

  // ---------------------------------------------------------------- basics
  function esc(v) { return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function rs(n) { return n == null ? "—" : "Rs " + Number(n).toLocaleString("en-PK", { maximumFractionDigits: 2 }); }
  function r2(n) { return Math.round(n * 100) / 100; }
  function newRef() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "");
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
  }
  function api(method, path, body) {
    return fetch(path, { method: method, credentials: "include", headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { banner(""); return { ok: r.ok, status: r.status, data: d }; }); })
      .catch(function () {
        banner("No connection to the server. Nothing was saved. Check the internet; the till retries the same sale safely.");
        return { ok: false, status: 0, data: { detail: "Could not reach the server. Nothing was saved." } };
      });
  }
  function why(r) { return (r.data && r.data.detail) || "That did not work."; }
  function say(t) { $("msg").textContent = t || ""; }
  function banner(t) { $("banner").hidden = !t; $("banner").textContent = t || ""; }
  function beep() {
    try {
      var a = new (window.AudioContext || window.webkitAudioContext)(), o = a.createOscillator();
      o.frequency.value = 330; o.connect(a.destination); o.start(); o.stop(a.currentTime + 0.18);
    } catch (e) { /* no sound: the message still shows */ }
  }
  function signedOut() {
    $("main").innerHTML = '<div class="state"><h1 class="font-display text-xl">Sign in to use the till</h1>' +
      '<p>Sign in with your staff PIN or the owner email.</p><a class="td-btn" href="/login">Sign in</a></div>';
  }
  // a business without the POS: one honest screen, nothing half-working
  function posOff() {
    ["shiftBtn", "openBtn", "billsBtn", "setupBtn"].forEach(function (k) { $(k).hidden = true; });
    $("main").innerHTML = '<div class="state"><h1 class="font-display text-xl">POS is not on for your business</h1>' +
      '<p>TechDeva POS is a separate product. Ask TechDeva to turn it on for you; your sales then go straight into your dashboard.</p>' +
      '<a class="td-btn td-btn--secondary" href="' + DASH + '">Back to dashboard</a></div>';
  }
  function guard(r) { if (r.status === 401) { closeAll(); signedOut(); return false; } return true; }
  function closeAll() { Array.prototype.forEach.call(document.querySelectorAll("dialog[open]"), function (d) { d.close(); }); }
  document.addEventListener("click", function (e) { var c = e.target.closest("[data-close]"); if (c) c.closest("dialog").close(); });

  // ask for one line of text; resolves null when cancelled
  function ask(title, label, value) {
    return new Promise(function (resolve) {
      $("askTitle").textContent = title; $("askLabel").textContent = label; $("askIn").value = value || ""; $("askMsg").textContent = "";
      var done = false;
      $("askForm").onsubmit = function (e) { e.preventDefault(); done = true; $("askDlg").close(); resolve($("askIn").value.trim()); };
      $("askDlg").onclose = function () { if (!done) resolve(null); };
      $("askDlg").showModal(); $("askIn").focus();
    });
  }

  // a manager's PIN for one kind of action; resolves the approval or null
  function getApproval(action, reason) {
    if (S.isOwner) return Promise.resolve("owner");
    if (S.approvals[action]) return Promise.resolve(S.approvals[action]);
    return new Promise(function (resolve) {
      $("pinWhy").textContent = reason; $("pinIn").value = ""; $("pinMsg").textContent = "";
      var done = false;
      $("pinForm").onsubmit = function (e) {
        e.preventDefault();
        api("POST", "/pos/manager-pin", { pin: $("pinIn").value, action: action }).then(function (r) {
          if (!guard(r)) return;
          if (!r.ok) { $("pinMsg").textContent = why(r); $("pinIn").value = ""; $("pinIn").focus(); return; }
          done = true; S.approvals[action] = r.data.approval;
          setTimeout(function () { delete S.approvals[action]; }, (r.data.expires_in - 20) * 1000);
          $("pinDlg").close(); resolve(r.data.approval);
        });
      };
      $("pinDlg").onclose = function () { if (!done) resolve(null); };
      $("pinDlg").showModal(); $("pinIn").focus();
    });
  }
  function approvals() { var a = {}; for (var k in S.approvals) a[k] = S.approvals[k]; return a; }

  // ---------------------------------------------------------------- load
  function load() {
    return api("GET", "/pos/catalogue").then(function (r) {
      if (!guard(r)) return;
      if (r.status === 403 && /not on/i.test(why(r))) { posOff(); return; }
      if (!r.ok) { $("grid").innerHTML = '<div class="state">' + esc(why(r)) + ' <button type="button" class="td-btn td-btn--secondary td-btn--sm" id="retry">Try again</button></div>'; $("retry").onclick = load; return; }
      var d = r.data;
      S.items = d.items || []; S.isOwner = !!d.is_owner; S.methods = d.methods || ["cash"]; S.settings = d.settings || {};
      S.business = d.business || ""; S.me = d.me || "";
      if ((S.settings.registers || []).indexOf(S.device.register) < 0) S.device.register = (S.settings.registers || ["Till 1"])[0];
      $("biz").textContent = S.business || "Till"; document.title = (S.business ? S.business + " · " : "") + "TechDeva POS";
      cats(); grid(); render();
      $("jvBtn").hidden = false;      // the till works, so Jarvis can bill on it
      return loadShift();
    });
  }
  function refreshDay() {
    api("GET", "/pos/today").then(function (r) {
      if (!r.ok) return;
      var d = r.data, t = (S.me ? S.me + " · " : "") + d.sales + " sale" + (d.sales === 1 ? "" : "s") + " today";
      if (d.udhaar) t += " · " + d.udhaar + " on udhaar";
      if (d.money) t += " · " + rs(d.money.collected) + " collected";
      $("day").textContent = t; $("openCount").textContent = d.open; S.open = d.open_bills || [];
    });
  }
  function loadShift() {
    return api("GET", "/pos/shift?register=" + encodeURIComponent(S.device.register)).then(function (r) {
      if (!r.ok) return;
      S.shift = r.data.shift; S.shiftRequired = r.data.required;
      $("shiftBtn").textContent = S.shift ? S.device.register + " · " + S.shift.number : S.device.register + " · no shift";
      $("shiftBtn").classList.toggle("warn", !S.shift && S.shiftRequired);
      refreshDay();
      if (!S.shift && S.shiftRequired) openShiftDlg();
    });
  }

  // ---------------------------------------------------------------- products
  function catOf(i) { return i.category || (i.kind === "service" ? "Services" : ""); }
  function cats() {
    var seen = {}, list = [];
    S.items.forEach(function (i) { var c = catOf(i); if (c && !seen[c]) { seen[c] = 1; list.push(c); } });
    $("cats").innerHTML = !list.length ? "" : ['<button type="button" data-cat="" aria-pressed="' + (S.cat === "") + '">All</button>'].concat(list.map(function (c) {
      return '<button type="button" data-cat="' + esc(c) + '" aria-pressed="' + (S.cat === c) + '">' + esc(c) + "</button>";
    })).join("");
  }
  function shown() {
    var q = $("search").value.trim().toLowerCase();
    return S.items.filter(function (i) {
      if (S.cat && catOf(i) !== S.cat) return false;
      return !q || i.name.toLowerCase().indexOf(q) >= 0 || (i.sku && i.sku.toLowerCase() === q);
    });
  }
  function grid() {
    if (!S.items.length) { $("grid").innerHTML = '<div class="state">Nothing to sell yet. Add products and prices on the dashboard (Business › Products). <a class="td-btn td-btn--secondary td-btn--sm" href="' + DASH + '">Open dashboard</a></div>'; return; }
    var list = shown();
    if (!list.length) { $("grid").innerHTML = '<div class="state">No product matches “' + esc($("search").value) + '”.</div>'; return; }
    $("grid").innerHTML = list.map(function (i) {
      var stock = i.on_hand == null ? "" : '<span class="stock' + (i.on_hand <= 0 ? " out" : "") + '">' + (i.on_hand <= 0 ? "Out of stock" : i.on_hand + " left") + "</span>";
      return '<button type="button" class="item" data-id="' + esc(i.id) + '"' + (i.price == null ? ' aria-disabled="true" title="No price set"' : "") + ">" +
        '<span class="name">' + esc(i.name) + '</span><span class="price td-num">' + (i.price == null ? "No price" : rs(i.price)) + "</span>" + stock + "</button>";
    }).join("");
  }
  function add(id) {
    var it = S.items.find(function (i) { return i.id === id; });
    if (!it) return;
    if (it.price == null) { beep(); say("Set a price for " + it.name + " on the dashboard first."); return; }
    var l = S.cart.find(function (x) { return x.id === id && !x.note; });
    if (l) l.qty += 1; else S.cart.push({ id: id, name: it.name, price: it.price, kind: it.kind, qty: 1, note: "" });
    if (it.on_hand != null && it.on_hand <= 0) say(it.name + " shows no stock. Sold anyway; check the shelf count."); else say("");
    render();
  }
  function addByCode(code) {
    var q = String(code || "").trim().toLowerCase();
    if (!q) return;
    var hit = S.items.filter(function (i) { return i.sku && i.sku.toLowerCase() === q; });
    if (!hit.length) hit = S.items.filter(function (i) { return i.name.toLowerCase() === q; });
    if (!hit.length) { var near = shown(); if (near.length === 1) hit = near; }
    if (hit.length === 1) { add(hit[0].id); $("search").value = ""; grid(); return; }
    beep(); say("No product with code “" + code + "”. Add the code (SKU) to the product on the dashboard.");
  }

  // ---------------------------------------------------------------- bill
  function taxPreview(base) {
    var s = S.bill ? { tax_mode: S.bill.tax_mode, tax_rate: S.bill.tax_rate } : S.settings;
    var rate = Number(s.tax_rate || 0);
    if (s.tax_mode === "exclusive") return { tax: r2(base * rate / 100), add: true, rate: rate };
    if (s.tax_mode === "inclusive") return { tax: r2(base - base / (1 + rate / 100)), add: false, rate: rate };
    return { tax: 0, add: false, rate: 0 };
  }
  function totals() {
    var sub = r2((S.bill ? S.bill.subtotal : 0) + S.cart.reduce(function (s, l) { return s + l.qty * l.price; }, 0));
    var disc = S.bill ? S.bill.discount : S.discount;
    var base = Math.max(0, r2(sub - disc)), t = taxPreview(base);
    return { sub: sub, disc: disc, tax: t, total: r2(base + (t.add ? t.tax : 0)) };
  }
  function render() {
    var b = S.bill, html = "", t = totals();
    $("billTitle").textContent = b ? b.number + (b.table ? " · Table " + b.table : "") + (b.mode_label ? " · " + b.mode_label : "") : "New sale";
    $("modes").hidden = !!b; $("cancelBillBtn").hidden = !b;
    ["table", "cname", "cphone"].forEach(function (k) { $(k).disabled = !!b; });
    $("tableField").hidden = (b ? b.mode : S.mode) !== "dine_in";
    if (b) b.items.forEach(function (l) {
      html += '<li class="line saved"><span class="name">' + esc(l.description) + (l.note ? '<span class="note">' + esc(l.note) + "</span>" : "") +
        (l.sent_to_kitchen ? '<span class="note">sent to kitchen</span>' : "") + '</span><span class="qty td-num">' + Number(l.quantity) + ' ×</span>' +
        '<span class="amt td-num">' + rs(l.line_total) + '</span><button type="button" class="x" data-void="' + l.id + '" aria-label="Remove ' + esc(l.description) + '">×</button></li>';
    });
    S.cart.forEach(function (l, n) {
      html += '<li class="line"><button type="button" class="name linkbtn" data-note="' + n + '" title="Add a note">' + esc(l.name) +
        '<span class="note">' + (l.note ? esc(l.note) : "+ note") + "</span></button>" +
        '<span class="stepper"><button type="button" data-dec="' + n + '" aria-label="One less ' + esc(l.name) + '">−</button>' +
        '<span class="td-num">' + l.qty + '</span><button type="button" data-inc="' + n + '" aria-label="One more ' + esc(l.name) + '">+</button></span>' +
        '<span class="amt td-num">' + rs(l.qty * l.price) + "</span></li>";
    });
    $("lines").innerHTML = html || '<li class="empty">Tap or scan a product to add it.</li>';
    $("sub").textContent = rs(t.sub);
    $("discShow").textContent = t.disc ? "−" + rs(t.disc) : "—";
    $("discBtn").disabled = !!b;
    $("taxRow").hidden = !t.tax.tax;
    $("taxLabel").textContent = (t.tax.add ? "" : "Incl. ") + (S.settings.tax_label || "Tax") + " " + t.tax.rate + "%";
    $("taxShow").textContent = rs(t.tax.tax);
    $("total").textContent = rs(t.total); $("mtotal").textContent = rs(t.total);
    $("holdBtn").textContent = b ? "Add to bill" : "Keep open";
    $("holdBtn").disabled = !S.cart.length;
    $("payBtn").disabled = !(S.cart.length || b) || t.total <= 0;
    HW.showCustomer({ business: S.business, total: t.total, msg: "",
      lines: (b ? b.items.map(function (l) { return { name: l.description, qty: Number(l.quantity), amount: l.line_total }; }) : [])
        .concat(S.cart.map(function (l) { return { name: l.name, qty: l.qty, amount: r2(l.qty * l.price) }; })) });
  }
  function reset() {
    S.cart = []; S.bill = null; S.ref = null; S.discount = 0; S.approvals = {};
    $("table").value = ""; $("cname").value = ""; $("cphone").value = ""; say(""); render(); $("search").focus();
  }
  function cartItems() {
    return S.cart.map(function (l) { var o = { quantity: l.qty, note: l.note || undefined }; o[l.kind === "service" ? "service_id" : "product_id"] = l.id; return o; });
  }
  function saleBody(extra) {
    var b = { client_ref: S.ref || (S.ref = newRef()), register: S.device.register, mode: S.mode, table: $("table").value,
              customer_name: $("cname").value, customer_phone: $("cphone").value, items: cartItems(), discount: S.discount,
              approval: approvals() };
    for (var k in extra) b[k] = extra[k];
    return b;
  }
  function busy(on) { ["holdBtn", "payBtn", "payGo"].forEach(function (k) { $(k).disabled = on; }); }
  function sendKitchen(billId) {
    if (!S.settings.kitchen_tickets) return;
    api("POST", "/pos/bills/" + billId + "/kitchen").then(function (r) {
      if (r.ok && r.data.lines.length) HW.printKitchen(r.data).catch(function (e) { say("Kitchen ticket not printed: " + e.message); });
    });
  }

  function hold() {
    if (!S.cart.length) return;
    busy(true);
    var p = S.bill ? api("POST", "/pos/bills/" + S.bill.id + "/items", { items: cartItems(), approval: approvals() })
                   : api("POST", "/pos/sale", saleBody({ hold: true }));
    p.then(function (r) {
      busy(false);
      if (!guard(r)) return;
      if (!r.ok) { say(why(r)); render(); return; }
      sendKitchen(r.data.id);
      reset(); say(r.data.number + " kept open" + (r.data.table ? " for table " + r.data.table : "") + ".");
      refreshDay();
    });
  }

  function discount() {
    if (S.bill) return;
    getApproval("discount", "A discount needs the owner's or a manager's PIN.").then(function (a) {
      if (!a) return;
      return ask("Discount", "Amount in Rs, or a percent like 10%", S.discount ? String(S.discount) : "").then(function (v) {
        if (v === null) return;
        var sub = totals().sub, d = /%$/.test(v) ? r2(sub * parseFloat(v) / 100) : parseFloat(v);
        if (!(d >= 0) || d > sub) { say("The discount must be between 0 and the bill."); return; }
        S.discount = d; say(""); render();
      });
    });
  }

  // ---------------------------------------------------------------- pay
  function payRow(method, amount) {
    var n = $("pays").children.length, row = document.createElement("div");
    row.className = "payrow";
    row.innerHTML = '<label class="td-sr-only" for="pm' + n + '">Method</label><select id="pm' + n + '" class="td-input">' +
      S.methods.map(function (m) { return '<option value="' + m + '"' + (m === method ? " selected" : "") + ">" + m.charAt(0).toUpperCase() + m.slice(1) + "</option>"; }).join("") + "</select>" +
      '<label class="td-sr-only" for="pa' + n + '">Amount</label><input id="pa' + n + '" class="td-input td-num" inputmode="decimal" value="' + (amount || "") + '">' +
      '<label class="td-sr-only" for="pr' + n + '">Slip or reference</label><input id="pr' + n + '" class="td-input ref" maxlength="120" placeholder="Slip / ref no.">';
    $("pays").appendChild(row);
    var sel = row.querySelector("select"), refIn = row.querySelector(".ref");
    sel.onchange = function () { refIn.hidden = sel.value === "cash"; change(); };
    refIn.hidden = method === "cash";
    row.querySelector("input").oninput = change;
  }
  function payments() {
    return Array.prototype.map.call($("pays").children, function (row) {
      var i = row.querySelectorAll("input");
      return { method: row.querySelector("select").value, amount: parseFloat(i[0].value), reference: i[1].value };
    }).filter(function (p) { return p.amount > 0; });
  }
  function due() { return S.payFor ? S.payFor.balance : totals().total; }
  function change() {
    var t = due(), cash = 0, other = 0, credit = $("credit").checked;
    payments().forEach(function (p) { if (p.method === "cash") cash += p.amount; else other += p.amount; });
    var left = r2(t - other - cash);
    $("payMsg").textContent = "";
    if (other - t > 0.009) $("change").innerHTML = '<span class="danger">Card/bank/online is more than the bill.</span>';
    else if (left > 0.009) $("change").innerHTML = (credit ? "On udhaar " : "Still to pay ") + "<strong class='td-num'>" + rs(left) + "</strong>";
    else $("change").innerHTML = "Change to give <strong class='td-num big'>" + rs(-left) + "</strong>";
    $("payGo").disabled = (left > 0.009 && !credit) || other - t > 0.009;
  }
  function quick() {
    var t = due(), set = [t];
    [100, 500, 1000, 5000].forEach(function (s) { var v = Math.ceil(t / s) * s; if (v > t && set.indexOf(v) < 0 && set.length < 5) set.push(v); });
    $("quick").innerHTML = set.map(function (v, n) { return '<button type="button" class="td-btn td-btn--secondary" data-cash="' + v + '">' + (n ? rs(v) : "Exact") + "</button>"; }).join("");
  }
  function openPay(collectBill) {
    S.payFor = collectBill || null;
    if (due() <= 0) return;
    $("payTitle").textContent = collectBill ? "Collect udhaar · " + collectBill.number : "Take payment";
    $("credit").checked = false; $("credit").parentNode.hidden = !!collectBill;
    $("due").textContent = rs(due()); $("pays").innerHTML = ""; payRow("cash", ""); quick(); change();
    $("payDlg").showModal(); $("pays").querySelector("input").focus();
  }
  function doPay() {
    var ps = payments(), credit = $("credit").checked;
    if (credit && !(S.bill ? S.bill.customer_name : $("cname").value.trim()) ) { $("payMsg").textContent = "Udhaar needs the customer's name and phone on the bill."; return; }
    var pre = credit && !S.settings.allow_staff_credit ? getApproval("credit", "Udhaar needs the owner's or a manager's PIN.") : Promise.resolve("ok");
    pre.then(function (a) {
      if (!a) return;
      busy(true); $("payGo").textContent = "Saving…";
      var p;
      if (S.payFor) p = api("POST", "/pos/bills/" + S.payFor.id + "/collect", { register: S.device.register, payments: ps });
      else if (S.bill) {
        var settle = function () { return api("POST", "/pos/bills/" + S.bill.id + "/settle", { register: S.device.register, payments: ps, credit: credit, approval: approvals() }); };
        p = S.cart.length ? api("POST", "/pos/bills/" + S.bill.id + "/items", { items: cartItems(), approval: approvals() }).then(function (r) { return r.ok ? settle() : r; }) : settle();
      } else p = api("POST", "/pos/sale", saleBody({ payments: ps, credit: credit }));
      p.then(function (r) {
        busy(false); $("payGo").textContent = "Done";
        if (!guard(r)) return;
        if (!r.ok) { $("payMsg").textContent = why(r); return; }
        var cash = ps.some(function (x) { return x.method === "cash"; });
        $("payDlg").close(); S.payFor = null;
        // the sale is saved: empty the bill now, so nothing from it can reach the next one
        reset(); showReceipt(r.data, { autoPrint: true, cash: cash }); refreshDay(); load();
        HW.showCustomer({ business: S.business, total: r.data.total, lines: [], msg: r.data.change_given ? "Thank you! Your change: " + rs(r.data.change_given) : "Thank you!" });
      });
    });
  }

  // ---------------------------------------------------------------- receipt
  function showReceipt(b, opt) {
    var lines = b.items.map(function (l) {
      return "<tr><td>" + esc(l.description) + (l.note ? '<div class="note">' + esc(l.note) + "</div>" : "") + '</td><td class="td-num">' + Number(l.quantity) + '</td><td class="td-num">' + rs(l.line_total) + "</td></tr>";
    }).join("");
    var row = function (a, v, cls) { return '<div class="row' + (cls ? " " + cls : "") + '"><span>' + esc(a) + '</span><span class="td-num">' + v + "</span></div>"; };
    var h = '<h2 id="rcptTitle" class="rc-biz">' + esc(b.business || S.business) + "</h2>" +
      (b.receipt && b.receipt.header ? '<div class="rc-sub">' + esc(b.receipt.header) + "</div>" : "") +
      '<div class="rc-sub">' + esc(b.number) + " · " + esc(new Date(b.recognised_at || b.delivered_at || b.created_at).toLocaleString("en-PK")) +
      (b.mode_label ? " · " + esc(b.mode_label) : "") + (b.table ? " · Table " + esc(b.table) : "") + (b.cashier ? " · " + esc(b.cashier) : "") + "</div>" +
      "<table><thead><tr><th>Item</th><th>Qty</th><th>Amount</th></tr></thead><tbody>" + lines + "</tbody></table>" +
      row("Subtotal", rs(b.subtotal)) + (b.discount ? row("Discount", "−" + rs(b.discount)) : "") +
      (b.tax ? row((b.tax_mode === "inclusive" ? "Incl. " : "") + b.tax_label + " " + b.tax_rate + "%", rs(b.tax)) : "") +
      row("Total", rs(b.total), "grand") +
      (b.payments || []).map(function (p) { return row(p.method + (p.reference ? " (" + p.reference + ")" : ""), rs(p.amount)); }).join("") +
      (b.cash_tendered != null ? row("Cash given", rs(b.cash_tendered)) + row("Change", rs(b.change_given)) : "") +
      (b.refunds || []).map(function (f) { return row("Refund " + f.number + " (" + f.method + ")", "−" + rs(f.amount)); }).join("") +
      (b.balance > 0 && !b.open ? row("Still owed (udhaar)", rs(b.balance), "owed") : "") +
      '<div class="rc-sub">' + (b.open ? "Open bill: not paid yet" : b.balance > 0 ? "Udhaar" : "Paid. " + esc((b.receipt && b.receipt.footer) || "")) + "</div>";
    $("receipt").innerHTML = h; $("rcptMsg").textContent = "";
    S.lastReceipt = b; S.lastCash = !!(opt && opt.cash);
    $("rcptDlg").showModal(); $("nextBtn").focus();
    if (opt && opt.autoPrint && S.device.autoPrint) printLast();
    else if (opt && opt.cash && S.device.drawer) HW.openDrawer().catch(function (e) { $("rcptMsg").textContent = e.message; });
  }
  function printLast() {
    $("rcptMsg").textContent = "Printing…";
    HW.printReceipt(S.lastReceipt, { cash: S.lastCash }).then(function (r) {
      $("rcptMsg").textContent = r === "skipped" ? "Printing is off on this till." : "Printed.";
    }).catch(function (e) { $("rcptMsg").textContent = "Not printed: " + e.message; });
  }

  // ---------------------------------------------------------------- open bills
  function openBills() {
    api("GET", "/pos/today").then(function (r) {
      if (!guard(r)) return;
      var list = r.ok ? r.data.open_bills : null;
      $("openList").innerHTML = !r.ok ? '<li class="state">' + esc(why(r)) + "</li>" : !list.length ? '<li class="state">No open bills.</li>' :
        list.map(function (b) {
          return '<li><button type="button" class="openbill" data-bill="' + b.id + '"><span><strong>' + esc(b.number) + "</strong>" +
            (b.table ? " · Table " + esc(b.table) : "") + (b.mode_label ? " · " + esc(b.mode_label) : "") + (b.customer_name ? " · " + esc(b.customer_name) : "") +
            '</span><span class="td-num">' + rs(b.total) + "</span></button></li>";
        }).join("");
      $("openDlg").showModal();
    });
  }
  function loadBill(id) {
    return api("GET", "/pos/bills/" + id).then(function (r) {
      if (!guard(r)) return;
      closeAll();
      if (!r.ok) { say(why(r)); return; }
      if (!r.data.open) { say(r.data.number + " is not open."); return; }
      S.cart = []; S.bill = r.data; S.discount = 0; say(""); render();
    });
  }
  function voidLine(itemId) {
    var line = S.bill.items.find(function (l) { return l.id === itemId; });
    var pre = line && line.sent_to_kitchen
      ? getApproval("void", "The kitchen already has this item. Removing it needs the owner's or a manager's PIN.").then(function (a) {
          return a ? ask("Remove " + line.description, "Why is it removed?", "") : null; })
      : Promise.resolve("");
    pre.then(function (reason) {
      if (reason === null) return;
      api("POST", "/pos/bills/" + S.bill.id + "/lines/" + itemId + "/remove", { reason: reason, approval: approvals() }).then(function (r) {
        if (!guard(r)) return;
        if (!r.ok) { say(why(r)); return; }
        S.bill = r.data; say("Removed."); render();
      });
    });
  }
  function cancelBill(bill) {
    return getApproval("cancel", "Cancelling a bill needs the owner's or a manager's PIN.").then(function (a) {
      if (!a) return;
      return ask("Cancel " + bill.number, "Why is the bill cancelled?", "").then(function (reason) {
        if (!reason) return;
        return api("POST", "/pos/bills/" + bill.id + "/cancel", { reason: reason, approval: approvals() }).then(function (r) {
          if (!guard(r)) return;
          if (!r.ok) { say(why(r)); return; }
          closeAll(); reset(); say(bill.number + " cancelled."); refreshDay();
        });
      });
    });
  }

  // ---------------------------------------------------------------- bills & refunds
  function billsDlg() {
    if (!$("bDay").value) $("bDay").value = new Date().toLocaleDateString("en-CA");
    $("billDetail").hidden = true; listBills(); $("billsDlg").showModal();
  }
  function listBills() {
    var q = "?day=" + encodeURIComponent($("bDay").value) + "&status=" + encodeURIComponent($("bStatus").value) + "&q=" + encodeURIComponent($("bQ").value);
    api("GET", "/pos/bills" + q).then(function (r) {
      if (!guard(r)) return;
      var list = r.ok ? r.data.bills : [];
      $("billList").innerHTML = !r.ok ? '<li class="state">' + esc(why(r)) + "</li>" : !list.length ? '<li class="state">No bills.</li>' :
        list.map(function (b) {
          return '<li><button type="button" class="openbill" data-show="' + b.id + '"><span><strong>' + esc(b.number) + '</strong> <span class="tag ' + b.state + '">' + b.state +
            "</span>" + (b.table ? " · Table " + esc(b.table) : "") + (b.customer_name ? " · " + esc(b.customer_name) : "") + " · " +
            esc(new Date(b.created_at).toLocaleTimeString("en-PK", { hour: "2-digit", minute: "2-digit" })) + '</span><span class="td-num">' + rs(b.total) +
            (b.refunded ? '<span class="note">−' + rs(b.refunded) + " refunded</span>" : "") + "</span></button></li>";
        }).join("");
    });
  }
  function showBill(id) {
    api("GET", "/pos/bills/" + id).then(function (r) {
      if (!guard(r) || !r.ok) return;
      var b = r.data;
      S.viewing = b;
      $("billDetail").hidden = false;
      $("billDetail").innerHTML = '<div class="row"><strong>' + esc(b.number) + "</strong><span class='td-num'>" + rs(b.total) + "</span></div>" +
        b.items.map(function (l) { return '<div class="row"><span>' + Number(l.quantity) + " × " + esc(l.description) + (l.refunded_quantity ? " (" + l.refunded_quantity + " returned)" : "") + '</span><span class="td-num">' + rs(l.line_total) + "</span></div>"; }).join("") +
        '<div class="actions-wrap">' +
        '<button type="button" class="td-btn td-btn--secondary td-btn--sm" data-act="print">Reprint</button>' +
        (b.open ? '<button type="button" class="td-btn td-btn--sm" data-act="load">Open on till</button><button type="button" class="td-btn td-btn--danger td-btn--sm" data-act="cancel">Cancel bill</button>' : "") +
        (b.udhaar ? '<button type="button" class="td-btn td-btn--sm" data-act="collect">Collect ' + rs(b.balance) + "</button>" : "") +
        (b.recognised && b.refunded < b.total ? '<button type="button" class="td-btn td-btn--danger td-btn--sm" data-act="refund">Refund / return</button>' : "") + "</div>";
    });
  }
  function refundDlg(b) {
    $("refundTitle").textContent = "Refund · " + b.number;
    $("refundLines").innerHTML = b.items.map(function (l) {
      var left = r2(Number(l.quantity) - (l.refunded_quantity || 0));
      return '<div class="payrow"><label for="rq' + l.id + '">' + esc(l.description) + " (" + left + " left)</label>" +
        '<input id="rq' + l.id + '" class="td-input td-num" inputmode="decimal" data-line="' + l.id + '" data-max="' + left + '" value="0"' + (left <= 0 ? " disabled" : "") + "></div>";
    }).join("");
    $("rfReason").value = ""; $("rfMsg").textContent = ""; $("rfMethod").value = "cash";
    $("refundForm").onsubmit = function (e) {
      e.preventDefault();
      var lines = Array.prototype.map.call($("refundLines").querySelectorAll("[data-line]"), function (i) {
        return { order_item_id: i.dataset.line, quantity: parseFloat(i.value) || 0 };
      }).filter(function (l) { return l.quantity > 0; });
      if (!lines.length) { $("rfMsg").textContent = "Enter how many of each item come back."; return; }
      if ($("rfReason").value.trim().length < 3) { $("rfMsg").textContent = "Write the reason."; return; }
      getApproval("refund", "A refund needs the owner's or a manager's PIN.").then(function (a) {
        if (!a) return;
        api("POST", "/pos/bills/" + b.id + "/refund", { register: S.device.register, lines: lines, method: $("rfMethod").value,
          reason: $("rfReason").value, restock: $("rfRestock").checked, approval: approvals() }).then(function (r) {
          if (!guard(r)) return;
          if (!r.ok) { $("rfMsg").textContent = why(r); return; }
          closeAll(); load();
          say(r.data.refund.number + ": " + rs(r.data.refund.amount) + " back in " + r.data.refund.method + ".");
          showReceipt(r.data, { autoPrint: true, cash: r.data.refund.method === "cash" });
        });
      });
    };
    $("refundDlg").showModal();
  }

  // ---------------------------------------------------------------- shift
  function openShiftDlg() {
    var s = S.shift;
    $("shiftMsg").textContent = "";
    if (!s) {
      var regs = (S.settings.registers || ["Till 1"]).map(function (r) { return '<option' + (r === S.device.register ? " selected" : "") + ">" + esc(r) + "</option>"; }).join("");
      $("shiftBody").innerHTML = '<p class="m-0">Count the cash in the drawer and start the shift. Sales on this till go into this shift.</p>' +
        '<div class="td-field"><label class="td-label" for="shReg">Till</label><select id="shReg" class="td-input">' + regs + "</select></div>" +
        '<div class="td-field"><label class="td-label" for="shFloat">Cash in the drawer now (Rs)</label><input id="shFloat" class="td-input td-num" inputmode="decimal" value="0"></div>' +
        '<div class="actions"><button type="button" class="td-btn td-btn--secondary" data-close>Later</button><button type="button" class="td-btn" id="shOpen">Start shift</button></div>';
      $("shOpen").onclick = function () {
        S.device.register = $("shReg").value; HW.saveDevice(S.device);
        api("POST", "/pos/shift/open", { register: S.device.register, opening_float: $("shFloat").value }).then(function (r) {
          if (!guard(r)) return;
          if (!r.ok) { $("shiftMsg").textContent = why(r); return; }
          $("shiftDlg").close(); loadShift();
        });
      };
    } else {
      var f = function (v) { return v == null ? "—" : rs(v); };
      $("shiftBody").innerHTML = '<div class="report">' + [
        ["Shift", s.number + " · " + s.register], ["Started", new Date(s.opened_at).toLocaleString("en-PK")], ["Opening cash", f(s.opening_float)],
        ["Sales", s.sales], ["Open bills", s.open_bills], ["Udhaar bills", s.udhaar_bills], ["Taken (all methods)", f(s.taken_total)],
        ["Refunds", f(s.refunds_total)], ["Cash in", f(s.cash_in)], ["Cash out", f(s.cash_out)], ["Expected cash in drawer", f(s.expected_cash)]
      ].map(function (x) { return '<div class="row"><span>' + esc(x[0]) + '</span><span class="td-num">' + esc(x[1]) + "</span></div>"; }).join("") +
        (s.hidden ? '<p class="note">' + esc(s.hidden) + "</p>" : "") + "</div>" +
        '<details><summary>Cash in / out</summary><div class="grid grid-cols-2 gap-2">' +
        '<select id="cmKind" class="td-input" aria-label="In or out"><option value="out">Cash out</option><option value="in">Cash in</option></select>' +
        '<input id="cmAmt" class="td-input td-num" inputmode="decimal" placeholder="Amount" aria-label="Amount"></div>' +
        '<input id="cmWhy" class="td-input" maxlength="200" placeholder="Why (e.g. bought milk)" aria-label="Reason">' +
        '<button type="button" class="td-btn td-btn--secondary" id="cmGo">Save</button></details>' +
        '<details><summary>Close shift</summary><p class="note">Count the cash in the drawer and type it. The expected figure is shown after.</p>' +
        '<input id="clCash" class="td-input td-num" inputmode="decimal" placeholder="Cash counted" aria-label="Cash counted">' +
        '<input id="clNote" class="td-input" maxlength="500" placeholder="Note (needed if over or short)" aria-label="Note">' +
        '<button type="button" class="td-btn td-btn--danger" id="clGo">Close shift</button></details>' +
        '<div class="actions"><button type="button" class="td-btn td-btn--secondary" id="xPrint">Print X report</button><button type="button" class="td-btn" data-close>Done</button></div>';
      $("cmGo").onclick = function () {
        api("POST", "/pos/shift/cash", { register: S.device.register, kind: $("cmKind").value, amount: $("cmAmt").value, reason: $("cmWhy").value }).then(function (r) {
          if (!guard(r)) return;
          if (!r.ok) { $("shiftMsg").textContent = why(r); return; }
          S.shift = r.data; openShiftDlg(); $("shiftMsg").textContent = "Saved.";
          if (S.device.drawer) HW.openDrawer().catch(function () { /* drawer optional */ });
        });
      };
      $("clGo").onclick = function () {
        api("POST", "/pos/shift/close", { register: S.device.register, counted_cash: $("clCash").value, notes: $("clNote").value }).then(function (r) {
          if (!guard(r)) return;
          if (!r.ok) { $("shiftMsg").textContent = why(r); return; }
          var z = r.data;
          S.shift = null; $("shiftBtn").textContent = S.device.register + " · no shift";
          $("shiftBody").innerHTML = '<div class="report">' + zRows(z).map(function (x) {
            return x[1] === null ? "<hr>" : '<div class="row"><span>' + esc(x[0]) + '</span><span class="td-num">' + esc(x[1]) + "</span></div>"; }).join("") + "</div>" +
            '<div class="actions"><button type="button" class="td-btn td-btn--secondary" id="zPrint">Print Z report</button><button type="button" class="td-btn" data-close>Done</button></div>';
          $("zPrint").onclick = function () { HW.printReport("Z REPORT " + z.number, zRows(z)).catch(function (e) { $("shiftMsg").textContent = e.message; }); };
          refreshDay();
        });
      };
      $("xPrint").onclick = function () { HW.printReport("X REPORT " + s.number, zRows(s)).catch(function (e) { $("shiftMsg").textContent = e.message; }); };
    }
    if (!$("shiftDlg").open) $("shiftDlg").showModal();
  }
  function zRows(z) {
    var f = function (v) { return v == null ? "-" : rs(v); }, rows = [["Till", z.register], ["Opened", new Date(z.opened_at).toLocaleString("en-PK")]];
    if (z.closed_at) rows.push(["Closed", new Date(z.closed_at).toLocaleString("en-PK")]);
    rows.push(["", null], ["Sales", z.sales], ["Bills total (after discount)", f(z.gross_sales)], ["Discounts", f(z.discounts)], ["Tax", f(z.tax)], ["", null]);
    Object.keys(z.taken_by_method || {}).forEach(function (m) { rows.push(["Taken " + m, f(z.taken_by_method[m])]); });
    Object.keys(z.refunds_by_method || {}).forEach(function (m) { rows.push(["Refund " + m, "-" + f(z.refunds_by_method[m])]); });
    rows.push(["Opening cash", f(z.opening_float)], ["Cash in", f(z.cash_in)], ["Cash out", f(z.cash_out)], ["", null], ["Expected cash", f(z.expected_cash)]);
    if (z.counted_cash != null) rows.push(["Counted cash", f(z.counted_cash)], ["Over / short", f(z.variance)]);
    if (z.notes) rows.push(["Note", z.notes]);
    return rows;
  }

  // ---------------------------------------------------------------- setup
  function setup(tab) {
    tab = tab || "device";
    Array.prototype.forEach.call($("setupTabs").children, function (b) { b.setAttribute("aria-selected", b.dataset.tab === tab ? "true" : "false"); });
    $("setupMsg").textContent = "";
    if (tab === "device") deviceTab(); else businessTab();
    if (!$("setupDlg").open) $("setupDlg").showModal();
  }
  function deviceTab() {
    var d = S.device, sup = HW.supported(), opt = function (v, l, cur) { return '<option value="' + v + '"' + (String(cur) === String(v) ? " selected" : "") + ">" + l + "</option>"; };
    var state = HW.connected(d) ? (d.printer === "browser" ? "Uses this computer's printers." : "Connected.") : (d.printer === "none" ? "No printing." : "Not connected yet.");
    $("setupBody").innerHTML =
      '<div class="td-field"><label class="td-label" for="dReg">This till is</label><select id="dReg" class="td-input">' + (S.settings.registers || ["Till 1"]).map(function (r) { return opt(esc(r), esc(r), d.register); }).join("") + "</select></div>" +
      '<div class="td-field"><label class="td-label" for="dPrinter">Receipt printer</label><select id="dPrinter" class="td-input">' +
      opt("browser", "Any printer (print dialog)", d.printer) + opt("usb", "Thermal printer on USB (direct)" + (sup.usb ? "" : " — not in this browser"), d.printer) +
      opt("serial", "Thermal printer on COM / Bluetooth (direct)" + (sup.serial ? "" : " — not in this browser"), d.printer) + opt("none", "No printer", d.printer) + "</select>" +
      '<p class="note" id="dState">' + esc(state) + "</p></div>" +
      '<div class="grid grid-cols-2 gap-2"><div class="td-field"><label class="td-label" for="dPaper">Paper</label><select id="dPaper" class="td-input">' + opt(80, "80 mm", d.paper) + opt(58, "58 mm", d.paper) + "</select></div>" +
      '<div class="td-field"><label class="td-label" for="dBaud">COM speed</label><select id="dBaud" class="td-input">' + [9600, 19200, 38400, 115200].map(function (b) { return opt(b, b, d.baud); }).join("") + "</select></div></div>" +
      '<div class="td-field"><label class="td-label" for="dKitchen">Kitchen tickets</label><select id="dKitchen" class="td-input">' + opt("same", "On the receipt printer", d.kitchen) + opt("browser", "Print dialog (choose the kitchen printer)", d.kitchen) + opt("none", "Do not print here", d.kitchen) + "</select></div>" +
      '<label class="check"><input type="checkbox" id="dAuto"' + (d.autoPrint ? " checked" : "") + "> Print the receipt after every sale</label>" +
      '<label class="check"><input type="checkbox" id="dDrawer"' + (d.drawer ? " checked" : "") + "> Cash drawer is plugged into the printer (opens on cash)</label>" +
      '<div class="actions-wrap"><button type="button" class="td-btn td-btn--secondary td-btn--sm" id="dConnect">Connect printer</button>' +
      '<button type="button" class="td-btn td-btn--secondary td-btn--sm" id="dTest">Test print</button>' +
      '<button type="button" class="td-btn td-btn--secondary td-btn--sm" id="dKick">Open drawer</button>' +
      '<button type="button" class="td-btn td-btn--secondary td-btn--sm" id="dDisplay">Customer screen</button></div>' +
      '<p class="note">Barcode scanner: plug it in (USB or Bluetooth) and scan; nothing to set up. Camera scanning: ' + (sup.camera ? "works in this browser." : "not in this browser (use Chrome on Android, or a scanner).") +
      " For printing with no dialog, start Chrome with --kiosk-printing and set the receipt printer as default.</p>";
    var saveD = function () {
      d.register = $("dReg").value; d.printer = $("dPrinter").value; d.paper = Number($("dPaper").value); d.baud = Number($("dBaud").value);
      d.kitchen = $("dKitchen").value; d.autoPrint = $("dAuto").checked; d.drawer = $("dDrawer").checked; HW.saveDevice(d);
      $("dState").textContent = HW.connected(d) ? "Ready." : d.printer === "none" ? "No printing." : "Not connected yet: press Connect printer.";
    };
    ["dPrinter", "dPaper", "dBaud", "dKitchen", "dAuto", "dDrawer"].forEach(function (k) { $(k).onchange = saveD; });
    $("dReg").onchange = function () { saveD(); loadShift(); };
    var done = function (p, ok) { p.then(function () { $("setupMsg").textContent = ok; deviceTab(); }).catch(function (e) { $("setupMsg").textContent = e.message; }); };
    $("dConnect").onclick = function () {
      saveD();
      if (d.printer === "usb") done(HW.pickUsb(), "USB printer connected.");
      else if (d.printer === "serial") done(HW.pickSerial(d.baud), "COM-port printer connected.");
      else $("setupMsg").textContent = d.printer === "browser" ? "Nothing to connect: the print dialog uses this computer's printers." : "Printing is off.";
    };
    $("dTest").onclick = function () { saveD(); done(HW.testPrint(), "Test sent to the printer."); };
    $("dKick").onclick = function () { done(HW.openDrawer(), "Drawer pulse sent."); };
    $("dDisplay").onclick = function () { window.open("/pos?display=1", "td-pos-display", "popup"); };
  }
  function businessTab() {
    var s = S.settings;
    if (!S.isOwner) { $("setupBody").innerHTML = '<p class="note">Receipt text, tax and till rules are set by the owner.</p>'; return; }
    var chk = function (id, on, label) { return '<label class="check"><input type="checkbox" id="' + id + '"' + (on ? " checked" : "") + "> " + label + "</label>"; };
    $("setupBody").innerHTML =
      '<div class="td-field"><label class="td-label" for="bsHead">Receipt top (address, phone)</label><textarea id="bsHead" class="td-textarea" maxlength="300">' + esc(s.receipt_header) + "</textarea></div>" +
      '<div class="td-field"><label class="td-label" for="bsFoot">Receipt bottom</label><input id="bsFoot" class="td-input" maxlength="200" value="' + esc(s.receipt_footer) + '"></div>' +
      '<div class="td-field"><label class="td-label" for="bsNtn">NTN (printed when set)</label><input id="bsNtn" class="td-input" maxlength="40" value="' + esc(s.ntn) + '"></div>' +
      '<div class="grid grid-cols-3 gap-2"><div class="td-field"><label class="td-label" for="bsTax">Tax</label><select id="bsTax" class="td-input">' +
      [["off", "Off"], ["exclusive", "Added to prices"], ["inclusive", "Included in prices"]].map(function (o) { return '<option value="' + o[0] + '"' + (s.tax_mode === o[0] ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") + "</select></div>" +
      '<div class="td-field"><label class="td-label" for="bsRate">Rate %</label><input id="bsRate" class="td-input td-num" inputmode="decimal" value="' + esc(s.tax_rate) + '"></div>' +
      '<div class="td-field"><label class="td-label" for="bsLabel">Name</label><input id="bsLabel" class="td-input" maxlength="20" value="' + esc(s.tax_label) + '"></div></div>' +
      '<div class="td-field"><label class="td-label" for="bsRegs">Tills (one per line)</label><textarea id="bsRegs" class="td-textarea">' + esc((s.registers || []).join("\n")) + "</textarea></div>" +
      chk("bsShift", s.require_shift, "No sale without an open shift (recommended)") +
      chk("bsCredit", s.allow_staff_credit, "Cashiers may give udhaar without a manager PIN") +
      chk("bsKot", s.kitchen_tickets, "Print kitchen tickets for open bills (restaurants)") +
      '<div class="actions"><button type="button" class="td-btn td-btn--secondary" data-close>Close</button><button type="button" class="td-btn" id="bsSave">Save</button></div>';
    $("bsSave").onclick = function () {
      api("PUT", "/pos/settings", { receipt_header: $("bsHead").value, receipt_footer: $("bsFoot").value, ntn: $("bsNtn").value,
        tax_mode: $("bsTax").value, tax_rate: $("bsRate").value || 0, tax_label: $("bsLabel").value,
        registers: $("bsRegs").value.split("\n"), require_shift: $("bsShift").checked, allow_staff_credit: $("bsCredit").checked,
        kitchen_tickets: $("bsKot").checked }).then(function (r) {
        if (!guard(r)) return;
        if (!r.ok) { $("setupMsg").textContent = why(r); return; }
        S.settings = r.data; $("setupMsg").textContent = "Saved. New bills use these settings."; render();
      });
    };
  }

  // ---------------------------------------------------------------- camera
  function camera() {
    $("camMsg").textContent = "Point the camera at the barcode.";
    $("camDlg").showModal();
    var scan = HW.scanCamera($("camVideo"));
    $("camDlg").onclose = function () { scan.stop && scan.stop(); };
    scan.then(function (code) { $("camDlg").close(); addByCode(code); })
      .catch(function (e) { if (e.message !== "stopped") $("camMsg").textContent = e.message; });
  }

  // ---------------------------------------------------------------- customer display
  function customerScreen() {
    $("till").hidden = true; $("display").hidden = false; document.title = "Customer display";
    HW.onCustomer(function (st) {
      $("custBiz").textContent = st.business || "";
      $("custLines").innerHTML = (st.lines || []).map(function (l) { return '<li><span>' + l.qty + " × " + esc(l.name) + '</span><span class="td-num">' + rs(l.amount) + "</span></li>"; }).join("");
      $("custTotal").textContent = rs(st.total || 0); $("custMsg").textContent = st.msg || (st.lines && st.lines.length ? "" : "Welcome");
    });
  }

  // ---------------------------------------------------------------- Jarvis
  // (8 Nov 2026; docs/JARVIS-API.md, app/jarvis_pos.py) Say or type a bill or a
  // stock question. The bill and its total are read back by the server from the
  // shop's own list; the till only shows them. Yes makes the sale on THIS till
  // (its register goes with every message), then the ordinary receipt prints.
  // Saying "haan" / "nahi" while a bill waits is the same as pressing Yes / No,
  // inside this signed-in till session.
  var JV = { conv: null, token: null, action: null, rec: null, listening: false, busy: false, audio: null };
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var YES = /^\s*(haan|han|haa|ha|ji|jee|ji haan|yes|ok|okay|theek|theek hai|thik hai|kar do|bana do|final|ہاں|جی)\b/i;
  var NO = /^\s*(nahi|nahin|nai|na|no|cancel|rehne do|mat|نہیں)\b/i;
  function jvPref(k, v) {
    try {
      if (v === undefined) return localStorage.getItem("td-pos-jv-" + k);
      localStorage.setItem("td-pos-jv-" + k, v);
    } catch (e) { /* storage blocked: the choice lasts this page view */ }
    return null;
  }
  JV.lang = jvPref("lang") === "en" ? "en" : "ur";
  JV.speak = jvPref("speak") !== "0";
  function jvErr(r) { var d = r.data || {}; return (d.error && d.error.message) || why(r); }
  function jvStopVoice() {
    if (JV.audio) { try { JV.audio.pause(); } catch (e) { /* already stopped */ } JV.audio = null; }
  }
  function jvVoice(text) {
    jvStopVoice();
    if (!JV.speak || !text) return;
    // Jarvis's own voice (Gemini, Urdu or English); silence if it cannot be had, the words are on screen
    fetch("/jarvis/v1/speech", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify({ text: text.slice(0, 600), lang: JV.lang }) })
      .then(function (r) { return r.ok ? r.blob() : null; })
      .then(function (b) {
        if (!b) return;
        var a = new Audio(URL.createObjectURL(b)); JV.audio = a;
        a.play().catch(function () { /* the browser refused sound: the reply is still shown */ });
      }).catch(function () { /* no voice: the reply is still shown */ });
  }
  function jvShow(text, ask) {
    $("jvReply").hidden = !text; $("jvReply").textContent = text || "";
    $("jvReply").classList.toggle("ask", !!ask);
    jvVoice(text);
  }
  function jvBusy(on, label) {
    JV.busy = on;
    ["jvMic", "jvSend", "jvYes", "jvNo"].forEach(function (k) { $(k).disabled = on; });
    $("jvMicLbl").textContent = label || (JV.listening ? "Listening… press to stop" : "Speak");
  }
  function jvPending(on) {
    $("jvConfirm").hidden = !on;
    if (!on) { JV.token = null; JV.action = null; }
  }
  function jvSend(text, spoken) {
    text = String(text || "").trim();
    if (!text || JV.busy) return;
    if (JV.token && YES.test(text)) return jvYes();
    if (JV.token && NO.test(text)) return jvNo();
    jvPending(false);
    $("jvHeard").textContent = "You: " + text; $("jvMsg").textContent = ""; $("jvIn").value = "";
    jvBusy(true, "Thinking…");
    api("POST", "/jarvis/v1/messages", { text: text, conversation_id: JV.conv, voice: !!spoken,
                                         context: { register: S.device.register } }).then(function (r) {
      jvBusy(false);
      if (!guard(r)) return;
      var d = r.data || {};
      if (d.conversation_id) JV.conv = d.conversation_id;
      if (!r.ok) { $("jvMsg").textContent = jvErr(r); return; }
      if (d.kind === "confirm" && d.confirmation) {
        JV.token = d.confirmation.token; JV.action = d.confirmation.action;
        $("jvYes").textContent = JV.action === "pos_bill" ? "Yes, make the bill" : "Yes";
        jvShow(d.reply); jvPending(true); JV.token = d.confirmation.token; $("jvYes").focus();
        return;
      }
      jvShow(d.reply, d.kind === "answer" || (d.action && d.action.ok === false));
    });
  }
  function jvYes() {
    if (!JV.token || JV.busy) return;
    var token = JV.token;
    jvPending(false); jvBusy(true, "Making the bill…");
    api("POST", "/jarvis/v1/confirmations", { token: token, said: "Yes", conversation_id: JV.conv }).then(function (r) {
      jvBusy(false);
      if (!guard(r)) return;
      var d = r.data || {};
      if (!r.ok) { $("jvMsg").textContent = jvErr(r); return; }
      if (d.action && d.action.ok === false) { jvShow(d.reply, true); return; }
      var sale = d.data && d.data.sale;
      if (!sale || !sale.id) { jvShow(d.reply); return; }
      jvShow(d.reply);
      // the sale is saved: show and print its receipt exactly as a sale made by hand
      api("GET", "/pos/bills/" + sale.id).then(function (b) {
        refreshDay(); load();
        if (!b.ok) { $("jvMsg").textContent = "The bill is made" + (sale.number ? " (" + sale.number + ")" : "") + ", but its receipt did not open: " + why(b) + " Find it under Bills."; return; }
        $("jvDlg").close();
        showReceipt(b.data, { autoPrint: true, cash: true });
        HW.showCustomer({ business: S.business, total: b.data.total, lines: [], msg: "Thank you!" });
      });
    });
  }
  function jvNo() {
    jvPending(false);
    jvShow("OK, no bill was made.");
  }
  function jvListen() {
    if (!SR || JV.busy) return;
    if (JV.listening) { try { JV.rec.stop(); } catch (e) { /* already stopped */ } return; }
    jvStopVoice();
    var rec;
    try { rec = new SR(); } catch (e) { $("jvMic").hidden = true; return; }
    JV.rec = rec;
    rec.lang = JV.lang === "en" ? "en-PK" : "ur-PK";
    rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
    var heard = "";
    rec.onresult = function (ev) {
      var t = "", fin = "";
      for (var i = ev.resultIndex; i < ev.results.length; i++) {
        var x = ev.results[i][0] ? ev.results[i][0].transcript : "";
        if (ev.results[i].isFinal) fin += x; else t += x;
      }
      if (fin) heard += fin;
      $("jvHeard").textContent = "Hearing: " + (heard + " " + t).trim();
    };
    rec.onerror = function (ev) {
      var m = { "not-allowed": "The microphone is blocked. Allow it from the lock icon in the address bar, then press Speak.",
                "service-not-allowed": "The microphone is blocked. Allow it from the lock icon in the address bar, then press Speak.",
                "no-speech": "I did not hear anything. Press Speak and try again.",
                "audio-capture": "No microphone was found on this till.",
                "network": "Speech needs the internet. Type it instead." }[ev.error];
      if (m) $("jvMsg").textContent = m;
    };
    rec.onend = function () {
      JV.listening = false; $("jvMic").setAttribute("aria-pressed", "false"); jvBusy(false);
      if (heard.trim()) jvSend(heard, true);
    };
    try {
      rec.start(); JV.listening = true; $("jvMsg").textContent = "";
      $("jvMic").setAttribute("aria-pressed", "true"); jvBusy(false);
    } catch (e) { JV.listening = false; }
  }
  function jvOpen() {
    $("jvLang").value = JV.lang; $("jvSpeak").checked = JV.speak;
    $("jvMic").hidden = !SR;
    $("jvMsg").textContent = SR ? "" : "This browser cannot listen. Type it, or use Chrome for speaking.";
    if (!$("jvDlg").open) $("jvDlg").showModal();
    if (SR) $("jvMic").focus(); else $("jvIn").focus();
  }

  // ---------------------------------------------------------------- wiring
  function wire() {
    $("jvBtn").onclick = jvOpen;
    $("jvMic").onclick = jvListen;
    $("jvForm").onsubmit = function (e) { e.preventDefault(); jvSend($("jvIn").value, false); };
    $("jvYes").onclick = jvYes;
    $("jvNo").onclick = jvNo;
    $("jvLang").onchange = function () { JV.lang = this.value === "en" ? "en" : "ur"; jvPref("lang", JV.lang); };
    $("jvSpeak").onchange = function () { JV.speak = this.checked; jvPref("speak", JV.speak ? "1" : "0"); if (!JV.speak) jvStopVoice(); };
    $("jvDlg").addEventListener("close", function () {
      if (JV.listening && JV.rec) { try { JV.rec.abort(); } catch (e) { /* already stopped */ } }
      jvStopVoice();
    });
    $("grid").onclick = function (e) { var b = e.target.closest(".item"); if (b) add(b.dataset.id); };
    $("cats").onclick = function (e) { var b = e.target.closest("button"); if (!b) return; S.cat = b.dataset.cat; cats(); grid(); };
    $("search").oninput = grid;
    $("search").onkeydown = function (e) { if (e.key === "Enter") { e.preventDefault(); addByCode(this.value); } };
    $("lines").onclick = function (e) {
      var b = e.target.closest("button"); if (!b) return;
      if (b.dataset.void) return voidLine(b.dataset.void);
      if (b.dataset.note != null) {
        var l = S.cart[+b.dataset.note];
        return ask("Note for " + l.name, "e.g. less spicy, no onion", l.note).then(function (v) { if (v !== null) { l.note = v.slice(0, 200); render(); } });
      }
      if (b.dataset.inc != null) S.cart[+b.dataset.inc].qty += 1;
      if (b.dataset.dec != null) { var n = +b.dataset.dec; S.cart[n].qty -= 1; if (S.cart[n].qty <= 0) S.cart.splice(n, 1); }
      render();
    };
    $("modes").onclick = function (e) {
      var b = e.target.closest("[data-mode]"); if (!b) return;
      S.mode = b.dataset.mode;
      Array.prototype.forEach.call(this.children, function (x) { x.setAttribute("aria-checked", x === b ? "true" : "false"); });
      render();
    };
    $("discBtn").onclick = discount;
    $("clearBtn").onclick = reset;
    $("cancelBillBtn").onclick = function () { if (S.bill) cancelBill(S.bill); };
    $("holdBtn").onclick = hold;
    $("payBtn").onclick = function () { openPay(null); };
    $("splitBtn").onclick = function () { payRow("card", ""); change(); };
    $("credit").onchange = change;
    $("quick").onclick = function (e) {
      var b = e.target.closest("[data-cash]"); if (!b) return;
      var rows = $("pays").children, last = rows[rows.length - 1];
      last.querySelector("select").value = "cash"; last.querySelector(".ref").hidden = true;
      last.querySelector("input").value = b.dataset.cash; change();
    };
    $("payForm").onsubmit = function (e) { e.preventDefault(); doPay(); };
    $("openBtn").onclick = openBills;
    $("openList").onclick = function (e) { var b = e.target.closest("[data-bill]"); if (b) loadBill(b.dataset.bill); };
    $("billsBtn").onclick = billsDlg;
    ["bDay", "bStatus"].forEach(function (k) { $(k).onchange = listBills; });
    $("bQ").oninput = function () { clearTimeout(S.bqT); S.bqT = setTimeout(listBills, 300); };
    $("billList").onclick = function (e) { var b = e.target.closest("[data-show]"); if (b) showBill(b.dataset.show); };
    $("billDetail").onclick = function (e) {
      var b = e.target.closest("[data-act]"), v = S.viewing; if (!b || !v) return;
      if (b.dataset.act === "print") HW.printReceipt(v, {}).catch(function (x) { say(x.message); });
      if (b.dataset.act === "load") loadBill(v.id);
      if (b.dataset.act === "cancel") cancelBill(v);
      if (b.dataset.act === "collect") { closeAll(); openPay(v); }
      if (b.dataset.act === "refund") refundDlg(v);
    };
    $("shiftBtn").onclick = openShiftDlg;
    $("setupBtn").onclick = function () { setup("device"); };
    $("setupTabs").onclick = function (e) { var b = e.target.closest("[data-tab]"); if (b) setup(b.dataset.tab); };
    $("camBtn").onclick = camera;
    $("printBtn").onclick = printLast;
    $("nextBtn").onclick = function () { $("rcptDlg").close(); };
    $("rcptDlg").addEventListener("close", function () { $("search").focus(); });
    window.addEventListener("offline", function () { banner("This till is offline. Sales cannot be saved until the internet is back."); });
    window.addEventListener("online", function () { banner(""); load(); });
    document.addEventListener("keydown", function (e) {
      if (document.querySelector("dialog[open]")) return;
      var k = { F2: function () { $("search").focus(); $("search").select(); }, F4: hold, F8: openBills, F9: function () { if (!$("payBtn").disabled) openPay(null); },
                F6: function () { if (!$("jvBtn").hidden) jvOpen(); } }[e.key];
      if (k) { e.preventDefault(); k(); }
    });
    HW.listenScanner(addByCode);
  }

  if (/[?&]display=1\b/.test(location.search)) { customerScreen(); return; }
  wire();
  $("backDash").href = DASH;
  HW.reconnect(S.device).then(load);
})();
