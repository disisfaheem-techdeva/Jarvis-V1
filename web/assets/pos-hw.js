/* TechDeva till hardware (3 Nov 2026): receipt printer, cash drawer, barcode
   scanner, camera scanning. No library, no install: everything here is the
   browser's own.

   Printer, three ways (chosen per machine in Till setup):
     browser  any printer Windows/Android knows (USB, network, Bluetooth). The
              till opens the print dialog; Chrome started with --kiosk-printing
              prints straight to the default printer with no dialog.
     usb      a thermal printer on USB, spoken to directly in ESC/POS (Chrome
              or Edge; on Windows the printer needs the WinUSB driver).
     serial   a thermal printer on a COM port: serial, USB-serial or Bluetooth
              (Chrome or Edge). Also ESC/POS.
   The cash drawer plugs into the printer (RJ11) and opens with an ESC/POS
   pulse, so it works with usb/serial. With "browser" the drawer opens only if
   the printer driver is set to open it on print.

   Scanner: any USB/Bluetooth barcode scanner that types like a keyboard. A
   burst of keys faster than a person types, ending in Enter, is a scan.
   Camera: the browser's BarcodeDetector (Chrome on Android, ChromeOS, macOS).

   What the hardware is on THIS machine is kept in this browser only
   (localStorage "td-pos-device"); the business's receipt text comes from the
   server. */
(function () {
  "use strict";
  var KEY = "td-pos-device";
  var DEFAULTS = { register: "Till 1", printer: "browser", paper: 80, autoPrint: true, drawer: false,
                   kitchen: "same", baud: 9600, display: false };

  function load() {
    var d = {};
    try { d = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { d = {}; }
    var out = {};
    for (var k in DEFAULTS) out[k] = d[k] !== undefined ? d[k] : DEFAULTS[k];
    return out;
  }
  function save(d) {
    try { localStorage.setItem(KEY, JSON.stringify(d)); } catch (e) { /* storage blocked: lasts this page view */ }
  }

  // ------------------------------------------------------------ ESC/POS
  function Esc(cols) { this.cols = cols; this.b = [0x1b, 0x40]; }   // ESC @ : reset
  Esc.prototype.raw = function (a) { for (var i = 0; i < a.length; i++) this.b.push(a[i]); return this; };
  Esc.prototype.text = function (s) {
    // printers speak one code page; anything outside ASCII prints as "?"
    s = String(s == null ? "" : s).replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/[–—−]/g, "-").replace(/×/g, "x");
    for (var i = 0; i < s.length; i++) { var c = s.charCodeAt(i); this.b.push(c === 10 ? 10 : c < 32 || c > 126 ? 63 : c); }
    return this;
  };
  Esc.prototype.ln = function (s) { return this.text(s || "").raw([10]); };
  Esc.prototype.align = function (n) { return this.raw([0x1b, 0x61, n]); };              // 0 left 1 centre 2 right
  Esc.prototype.bold = function (on) { return this.raw([0x1b, 0x45, on ? 1 : 0]); };
  Esc.prototype.big = function (on) { return this.raw([0x1d, 0x21, on ? 0x11 : 0]); };   // double width + height
  Esc.prototype.rule = function () { return this.ln(new Array(this.cols + 1).join("-")); };
  Esc.prototype.row = function (left, right, cols) {
    cols = cols || this.cols; left = String(left); right = String(right);
    var room = cols - right.length - 1;
    while (left.length > room) { this.ln(left.slice(0, room)); left = "  " + left.slice(room); }
    return this.ln(left + new Array(cols - left.length - right.length + 1).join(" ") + right);
  };
  Esc.prototype.wrap = function (s) {
    var words = String(s || "").split(/\s+/), cur = "";
    for (var i = 0; i < words.length; i++) {
      if ((cur + " " + words[i]).trim().length > this.cols) { this.ln(cur); cur = words[i]; } else cur = (cur + " " + words[i]).trim();
    }
    if (cur) this.ln(cur);
    return this;
  };
  Esc.prototype.cut = function () { return this.raw([10, 10, 10, 0x1d, 0x56, 0x42, 0]); };   // feed, partial cut
  Esc.prototype.kick = function () { return this.raw([0x1b, 0x70, 0, 25, 250]); };           // drawer pin 2
  Esc.prototype.bytes = function () { return new Uint8Array(this.b); };

  function money(n) { return n == null ? "-" : "Rs " + Number(n).toLocaleString("en-PK", { maximumFractionDigits: 2 }); }
  function cols(paper) { return Number(paper) === 58 ? 32 : 48; }

  function receiptBytes(b, opts) {
    var e = new Esc(cols(opts.paper)), r = b.receipt || {};
    e.align(1).bold(true).big(true).ln(b.business || "").big(false).bold(false);
    if (r.header) String(r.header).split(/\n/).forEach(function (l) { e.wrap(l); });
    if (r.ntn) e.ln("NTN: " + r.ntn);
    e.align(0).rule();
    e.ln((b.open ? "BILL (NOT PAID) " : b.udhaar ? "UDHAAR " : "RECEIPT ") + b.number);
    e.ln(new Date(b.recognised_at || b.delivered_at || b.created_at).toLocaleString("en-PK"));
    if (b.mode_label || b.table) e.ln((b.mode_label || "") + (b.table ? "  Table " + b.table : ""));
    if (b.cashier) e.ln("Cashier: " + b.cashier + (b.register ? "  (" + b.register + ")" : ""));
    if (b.customer_name) e.ln("Customer: " + b.customer_name + (b.customer_phone ? " " + b.customer_phone : ""));
    e.rule();
    (b.items || []).forEach(function (it) {
      e.row(Number(it.quantity) + " x " + it.description, money(it.line_total));
      if (it.note) e.ln("   * " + it.note);
    });
    e.rule().row("Subtotal", money(b.subtotal));
    if (b.discount) e.row("Discount", "-" + money(b.discount));
    if (b.tax && b.tax_mode === "exclusive") e.row(b.tax_label + " " + b.tax_rate + "%", money(b.tax));
    e.bold(true).big(true).row("TOTAL", money(b.total), Math.floor(e.cols / 2)).big(false).bold(false);
    if (b.tax && b.tax_mode === "inclusive") e.row("Incl. " + b.tax_label + " " + b.tax_rate + "%", money(b.tax));
    (b.payments || []).forEach(function (p) { e.row("Paid " + p.method + (p.reference ? " (" + p.reference + ")" : ""), money(p.amount)); });
    if (b.cash_tendered != null) { e.row("Cash given", money(b.cash_tendered)); e.row("Change", money(b.change_given)); }
    (b.refunds || []).forEach(function (f) { e.row("Refund " + f.number + " " + f.method, "-" + money(f.amount)); });
    if (b.balance > 0 && !b.open) e.bold(true).row("STILL OWED", money(b.balance)).bold(false);
    e.rule().align(1);
    if (r.footer) e.wrap(r.footer);
    e.align(0);
    if (opts.kick) e.kick();
    return e.cut().bytes();
  }

  function kitchenBytes(k, opts) {
    var e = new Esc(cols(opts.paper));
    e.align(1).bold(true).big(true).ln("KITCHEN").big(false).ln(k.number + (k.table ? "  TABLE " + k.table : "")).bold(false);
    e.ln((k.mode_label || "") + "  " + new Date().toLocaleTimeString("en-PK")).align(0).rule();
    k.lines.forEach(function (l) {
      e.bold(true).big(true).ln(l.quantity + " x " + l.description).big(false).bold(false);
      if (l.note) e.ln("   * " + l.note);
    });
    return e.cut().bytes();
  }

  function testBytes(opts) {
    var e = new Esc(cols(opts.paper));
    e.align(1).bold(true).ln("PRINTER TEST").bold(false).ln(new Date().toLocaleString("en-PK")).align(0).rule();
    e.row("Paper", opts.paper + " mm").row("Characters per line", String(e.cols)).row("Left", "Right").rule();
    if (opts.kick) e.kick();
    return e.cut().bytes();
  }

  // ------------------------------------------------------------ transports
  var usbDev = null, serialPort = null;

  function supported() {
    return { usb: !!navigator.usb, serial: !!navigator.serial, camera: "BarcodeDetector" in window,
             display: "BroadcastChannel" in window };
  }

  function usbOut(dev) {
    var cfg = dev.configuration || dev.configurations[0];
    for (var i = 0; i < cfg.interfaces.length; i++) {
      var alts = cfg.interfaces[i].alternates || [cfg.interfaces[i].alternate];
      for (var j = 0; j < alts.length; j++) {
        var eps = alts[j].endpoints || [];
        for (var k = 0; k < eps.length; k++) if (eps[k].direction === "out" && eps[k].type === "bulk")
          return { iface: cfg.interfaces[i].interfaceNumber, ep: eps[k].endpointNumber };
      }
    }
    return null;
  }
  function openUsb(dev) {
    var p = dev.opened ? Promise.resolve() : dev.open();
    return p.then(function () { return dev.configuration ? null : dev.selectConfiguration(1); })
      .then(function () {
        var o = usbOut(dev);
        if (!o) throw new Error("This USB device has no printer output. Is it a receipt printer?");
        return dev.claimInterface(o.iface).catch(function () { /* already ours */ }).then(function () { usbDev = { dev: dev, ep: o.ep }; return dev; });
      });
  }
  function pickUsb() {
    if (!navigator.usb) return Promise.reject(new Error("This browser cannot talk to USB printers. Use Chrome or Edge, or choose Browser printing."));
    return navigator.usb.requestDevice({ filters: [] }).then(openUsb);
  }
  function pickSerial(baud) {
    if (!navigator.serial) return Promise.reject(new Error("This browser cannot use COM ports. Use Chrome or Edge, or choose Browser printing."));
    return navigator.serial.requestPort().then(function (port) { return openSerial(port, baud); });
  }
  function openSerial(port, baud) {
    var p = port.readable || port.writable ? Promise.resolve() : port.open({ baudRate: Number(baud) || 9600 });
    return p.then(function () { serialPort = port; return port; });
  }
  // a printer chosen earlier: the browser remembers the permission
  function reconnect(dev) {
    if (dev.printer === "usb" && navigator.usb && !usbDev) {
      return navigator.usb.getDevices().then(function (ds) { return ds.length ? openUsb(ds[0]).then(function () { return true; }) : false; })
        .catch(function () { return false; });
    }
    if (dev.printer === "serial" && navigator.serial && !serialPort) {
      return navigator.serial.getPorts().then(function (ps) { return ps.length ? openSerial(ps[0], dev.baud).then(function () { return true; }) : false; })
        .catch(function () { return false; });
    }
    return Promise.resolve(dev.printer === "browser" || dev.printer === "none" || !!usbDev || !!serialPort);
  }
  function connected(dev) {
    if (dev.printer === "usb") return !!usbDev;
    if (dev.printer === "serial") return !!serialPort;
    return dev.printer === "browser";
  }

  function send(dev, bytes) {
    if (dev.printer === "usb") {
      if (!usbDev) return Promise.reject(new Error("The USB printer is not connected. Open Till setup and connect it."));
      return usbDev.dev.transferOut(usbDev.ep, bytes).then(function (r) {
        if (r.status !== "ok") throw new Error("The printer did not take the receipt (" + r.status + ").");
      });
    }
    if (dev.printer === "serial") {
      if (!serialPort || !serialPort.writable) return Promise.reject(new Error("The COM-port printer is not connected. Open Till setup and connect it."));
      var w = serialPort.writable.getWriter();
      return w.write(bytes).finally(function () { w.releaseLock(); });
    }
    return Promise.reject(new Error("No direct printer on this till."));
  }

  // ------------------------------------------------------------ browser printing
  function printHTML(html, paper) {
    return new Promise(function (resolve) {
      var f = document.createElement("iframe");
      f.setAttribute("aria-hidden", "true");
      f.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0";
      document.body.appendChild(f);
      var w = Number(paper) === 58 ? "56mm" : "76mm";
      var doc = f.contentWindow.document;
      doc.open();
      doc.write("<!doctype html><html><head><meta charset='utf-8'><title>Receipt</title><style>" +
        "@page{size:" + w + " auto;margin:2mm}body{margin:0;width:" + w + ";font:12px/1.35 ui-monospace,Menlo,Consolas,monospace;color:#000}" +
        "h1{font-size:16px;margin:0 0 2px;text-align:center}.c{text-align:center}.r{display:flex;justify-content:space-between;gap:6px}" +
        ".r span:last-child{white-space:nowrap}.b{font-weight:700}.big{font-size:16px;font-weight:700}hr{border:0;border-top:1px dashed #000;margin:4px 0}" +
        ".n{padding-left:12px;font-size:11px}.k{font-size:18px;font-weight:700}</style></head><body>" + html + "</body></html>");
      doc.close();
      setTimeout(function () {
        f.contentWindow.focus();
        f.contentWindow.print();
        setTimeout(function () { f.remove(); resolve(); }, 1000);
      }, 150);
    });
  }
  function esc(v) { return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function row(l, r, cls) { return "<div class='r" + (cls ? " " + cls : "") + "'><span>" + esc(l) + "</span><span>" + esc(r) + "</span></div>"; }

  function receiptHTML(b) {
    var r = b.receipt || {}, h = "<h1>" + esc(b.business || "") + "</h1>";
    if (r.header) h += "<div class='c'>" + esc(r.header).replace(/\n/g, "<br>") + "</div>";
    if (r.ntn) h += "<div class='c'>NTN: " + esc(r.ntn) + "</div>";
    h += "<hr><div class='b'>" + (b.open ? "BILL (NOT PAID) " : b.udhaar ? "UDHAAR " : "RECEIPT ") + esc(b.number) + "</div>";
    h += "<div>" + esc(new Date(b.recognised_at || b.delivered_at || b.created_at).toLocaleString("en-PK")) + "</div>";
    if (b.mode_label || b.table) h += "<div>" + esc((b.mode_label || "") + (b.table ? "  Table " + b.table : "")) + "</div>";
    if (b.cashier) h += "<div>Cashier: " + esc(b.cashier) + (b.register ? " (" + esc(b.register) + ")" : "") + "</div>";
    if (b.customer_name) h += "<div>Customer: " + esc(b.customer_name + (b.customer_phone ? " " + b.customer_phone : "")) + "</div>";
    h += "<hr>";
    (b.items || []).forEach(function (it) {
      h += row(Number(it.quantity) + " x " + it.description, money(it.line_total));
      if (it.note) h += "<div class='n'>* " + esc(it.note) + "</div>";
    });
    h += "<hr>" + row("Subtotal", money(b.subtotal));
    if (b.discount) h += row("Discount", "-" + money(b.discount));
    if (b.tax && b.tax_mode === "exclusive") h += row(b.tax_label + " " + b.tax_rate + "%", money(b.tax));
    h += row("TOTAL", money(b.total), "big");
    if (b.tax && b.tax_mode === "inclusive") h += row("Incl. " + b.tax_label + " " + b.tax_rate + "%", money(b.tax));
    (b.payments || []).forEach(function (p) { h += row("Paid " + p.method + (p.reference ? " (" + p.reference + ")" : ""), money(p.amount)); });
    if (b.cash_tendered != null) h += row("Cash given", money(b.cash_tendered)) + row("Change", money(b.change_given));
    (b.refunds || []).forEach(function (f) { h += row("Refund " + f.number + " " + f.method, "-" + money(f.amount)); });
    if (b.balance > 0 && !b.open) h += row("STILL OWED", money(b.balance), "b");
    if (r.footer) h += "<hr><div class='c'>" + esc(r.footer) + "</div>";
    return h;
  }
  function kitchenHTML(k) {
    var h = "<h1>KITCHEN</h1><div class='c b'>" + esc(k.number + (k.table ? "  TABLE " + k.table : "")) + "</div><div class='c'>" +
      esc((k.mode_label || "") + "  " + new Date().toLocaleTimeString("en-PK")) + "</div><hr>";
    k.lines.forEach(function (l) { h += "<div class='k'>" + esc(l.quantity + " x " + l.description) + "</div>" + (l.note ? "<div class='n'>* " + esc(l.note) + "</div>" : ""); });
    return h;
  }

  // ------------------------------------------------------------ public: print
  function printReceipt(bill, opt) {
    var dev = load(), kick = !!(opt && opt.cash) && dev.drawer;
    if (dev.printer === "none") return Promise.resolve("skipped");
    if (dev.printer === "browser") return printHTML(receiptHTML(bill), dev.paper).then(function () { return "printed"; });
    return send(dev, receiptBytes(bill, { paper: dev.paper, kick: kick })).then(function () { return "printed"; });
  }
  function printKitchen(ticket) {
    var dev = load();
    if (!ticket.lines || !ticket.lines.length || dev.kitchen === "none" || dev.printer === "none") return Promise.resolve("skipped");
    if (dev.kitchen === "browser" || dev.printer === "browser") return printHTML(kitchenHTML(ticket), dev.paper).then(function () { return "printed"; });
    return send(dev, kitchenBytes(ticket, { paper: dev.paper })).then(function () { return "printed"; });
  }
  function openDrawer() {
    var dev = load();
    if (dev.printer !== "usb" && dev.printer !== "serial")
      return Promise.reject(new Error("The drawer opens through a direct (USB or COM-port) receipt printer."));
    return send(dev, new Esc(cols(dev.paper)).kick().bytes());
  }
  function testPrint() {
    var dev = load();
    if (dev.printer === "browser") return printHTML("<h1>PRINTER TEST</h1><div class='c'>" + esc(new Date().toLocaleString("en-PK")) +
      "</div><hr>" + row("Paper", dev.paper + " mm") + row("Left", "Right"), dev.paper);
    return send(dev, testBytes({ paper: dev.paper, kick: dev.drawer }));
  }

  // a plain report (shift X/Z): title, then [label, value] rows; null value = a rule
  function printReport(title, rows) {
    var dev = load();
    if (dev.printer === "none") return Promise.resolve("skipped");
    if (dev.printer === "browser") {
      return printHTML("<h1>" + esc(title) + "</h1><hr>" + rows.map(function (r) {
        return r[1] === null ? "<hr>" : row(r[0], r[1]);
      }).join(""), dev.paper);
    }
    var e = new Esc(cols(dev.paper));
    e.align(1).bold(true).ln(title).bold(false).align(0).rule();
    rows.forEach(function (r) { if (r[1] === null) e.rule(); else e.row(r[0], String(r[1])); });
    return send(dev, e.cut().bytes());
  }

  // ------------------------------------------------------------ scanner (keyboard wedge)
  var GAP_MS = 35, MIN_LEN = 4;
  function listenScanner(onScan) {
    var buf = "", last = 0, fast = 0;
    document.addEventListener("keydown", function (e) {
      var t = e.target, tag = t && t.tagName;
      // fields handle their own typing; the search box handles scans itself
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (t && t.isContentEditable)) { buf = ""; return; }
      if (document.querySelector("dialog[open]")) return;
      var now = Date.now();
      if (now - last > 300) { buf = ""; fast = 0; }
      if (e.key === "Enter") {
        if (buf.length >= MIN_LEN && fast >= buf.length - 1) { e.preventDefault(); onScan(buf); }
        buf = ""; fast = 0; last = now; return;
      }
      if (e.key && e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {
        if (buf && now - last <= GAP_MS) fast += 1;
        buf += e.key;
      }
      last = now;
    }, true);
  }

  // ------------------------------------------------------------ camera
  var FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "qr_code", "itf", "codabar"];
  function scanCamera(video) {
    if (!("BarcodeDetector" in window)) return Promise.reject(new Error("This browser cannot read barcodes from the camera. Use Chrome on Android, or a barcode scanner."));
    var stream, stopped = false, det;
    var p = (window.BarcodeDetector.getSupportedFormats ? window.BarcodeDetector.getSupportedFormats() : Promise.resolve(FORMATS))
      .then(function (f) { det = new window.BarcodeDetector({ formats: FORMATS.filter(function (x) { return f.indexOf(x) >= 0; }) }); })
      .then(function () { return navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false }); })
      .then(function (s) {
        stream = s; video.srcObject = s; return video.play();
      })
      .then(function () {
        return new Promise(function (resolve, reject) {
          (function tick() {
            if (stopped) return reject(new Error("stopped"));
            det.detect(video).then(function (codes) {
              if (codes && codes.length) resolve(codes[0].rawValue); else setTimeout(tick, 200);
            }).catch(function () { setTimeout(tick, 400); });
          })();
        });
      });
    var stop = function () { stopped = true; if (stream) stream.getTracks().forEach(function (t) { t.stop(); }); video.srcObject = null; };
    p.then(stop, stop);
    p.stop = stop;
    return p;
  }

  // ------------------------------------------------------------ customer display
  var chan = "BroadcastChannel" in window ? new BroadcastChannel("td-pos-display") : null;
  function showCustomer(state) { if (chan) try { chan.postMessage(state); } catch (e) { /* display closed */ } }
  function onCustomer(fn) { if (chan) chan.onmessage = function (m) { fn(m.data); }; }

  window.PosHW = {
    device: load, saveDevice: save, supported: supported, reconnect: reconnect, connected: connected,
    pickUsb: pickUsb, pickSerial: pickSerial, printReceipt: printReceipt, printKitchen: printKitchen,
    openDrawer: openDrawer, testPrint: testPrint, printReport: printReport, listenScanner: listenScanner, scanCamera: scanCamera,
    showCustomer: showCustomer, onCustomer: onCustomer,
    // exposed for tests and for anyone checking what goes to the printer
    _receiptBytes: receiptBytes, _receiptHTML: receiptHTML
  };
})();
