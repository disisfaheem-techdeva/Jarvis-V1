/* TechDeva theme + brand colour, for pages served by FastAPI (web/*.html).
   sync.py inlines this into each page's <head>, so the saved theme applies
   before the first paint. Next.js has its own copy of the theme part
   (techdeva-web/src/components/ui/theme.ts); both use the "td-theme" key.

   TD.toggleHTML()  markup for the light/dark switch, for script-rendered
                    headers. In static markup, <span data-td-theme-slot></span>
                    is replaced with it. Any [data-td-theme-toggle] works,
                    even if rendered later.
   TD.brand(hex)    a business's own accent colour. Works out readable text
                    on it and a readable text shade of it for the current
                    theme, so a light brand colour never gives white-on-white
                    buttons and a dark one never vanishes on the dark canvas. */
(function () {
  var KEY = "td-theme", doc = document.documentElement, brandHex = null;

  function theme() { return doc.dataset.theme === "light" ? "light" : "dark"; }

  function rgb(hex) {
    var h = hex.replace("#", "");
    if (h.length === 3 || h.length === 4) h = h.replace(/./g, "$&$&");
    return [0, 2, 4].map(function (i) { return parseInt(h.substr(i, 2), 16); });
  }
  function lum(c) {
    var v = c.map(function (x) { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  }
  function contrast(a, b) { var x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
  function mix(c, to, t) { return c.map(function (x, i) { return Math.round(x + (to[i] - x) * t); }); }
  function hex(c) { return "#" + c.map(function (x) { return ("0" + x.toString(16)).slice(-2); }).join(""); }

  function applyBrand() {
    var props = ["--accent", "--accent-hover", "--accent-ink", "--accent-tint", "--accent-line", "--fg-on-accent", "--ring"];
    if (!brandHex) { props.forEach(function (p) { doc.style.removeProperty(p); }); return; }
    var c = rgb(brandHex), white = [255, 255, 255], black = [0, 0, 0];
    var light = theme() === "light";
    var bg = light ? white : [10, 10, 15];
    // text on the accent fill: whichever of white or near-black reads better
    var on = contrast(c, white) >= contrast(c, [20, 16, 5]) ? white : [20, 16, 5];
    // the accent as text on the page: step it toward white (dark theme) or
    // black (light theme) until it reaches 4.5:1
    var ink = c, t = 0;
    while (contrast(ink, bg) < 4.5 && t < 1) { t += 0.05; ink = mix(c, light ? black : white, t); }
    var s = doc.style;
    s.setProperty("--accent", hex(c));
    s.setProperty("--accent-hover", hex(mix(c, on === white ? black : white, 0.12)));
    s.setProperty("--fg-on-accent", hex(on));
    s.setProperty("--accent-ink", hex(ink));
    s.setProperty("--ring", hex(ink));
    s.setProperty("--accent-tint", "rgb(" + c.join(" ") + " / " + (light ? 0.1 : 0.16) + ")");
    s.setProperty("--accent-line", "rgb(" + c.join(" ") + " / 0.4)");
  }

  function sync() {
    var light = theme() === "light";
    var btns = document.querySelectorAll("[data-td-theme-toggle]");
    for (var i = 0; i < btns.length; i++) {
      btns[i].setAttribute("aria-label", light ? "Switch to dark theme" : "Switch to light theme");
      btns[i].setAttribute("title", light ? "Dark theme" : "Light theme");
    }
    var meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.content = light ? "light" : "dark";
  }

  function set(next) {
    if (next === "light") doc.dataset.theme = "light"; else delete doc.dataset.theme;
    try { localStorage.setItem(KEY, next); } catch (e) { /* storage blocked: lasts this page view */ }
    applyBrand();
    sync();
  }

  try { if (localStorage.getItem(KEY) === "light") doc.dataset.theme = "light"; } catch (e) { /* default dark */ }

  var SUN = '<svg class="td-i-sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>';
  var MOON = '<svg class="td-i-moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>';

  window.TD = {
    theme: theme,
    setTheme: set,
    brand: function (h) { brandHex = h && /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(h) ? h.slice(0, h.length === 9 ? 7 : h.length) : null; applyBrand(); },
    toggleHTML: function (cls) {
      var light = theme() === "light";
      return '<button type="button" class="td-theme-btn' + (cls ? " " + cls : "") + '" data-td-theme-toggle aria-label="' +
        (light ? "Switch to dark theme" : "Switch to light theme") + '" title="' + (light ? "Dark theme" : "Light theme") + '">' + SUN + MOON + "</button>";
    }
  };

  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-td-theme-toggle]");
    if (b) set(theme() === "light" ? "dark" : "light");
  });
  document.addEventListener("DOMContentLoaded", function () {
    // static pages: <span data-td-theme-slot="extra classes"></span> becomes the switch
    var slots = document.querySelectorAll("[data-td-theme-slot]");
    for (var i = 0; i < slots.length; i++) slots[i].outerHTML = window.TD.toggleHTML(slots[i].getAttribute("data-td-theme-slot"));
    sync();
  });
})();
