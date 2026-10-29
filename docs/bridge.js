/* Can Radar – iPhone-Version
 *
 * Diese Datei ersetzt die Android-Teile (Java, ML Kit, JobScheduler) und gibt der App dieselbe
 * Schnittstelle wie vorher: window.CanRadarNative. Der Rest der App (index.html) läuft so fast unverändert.
 *
 *   Abruf der Angebote ........ fetch() statt HttpURLConnection
 *   Text aus Dosen-Foto ....... Tesseract.js (läuft im Browser, das Foto wird nicht hochgeladen)
 *   Barcode (Foto und live) ... ZXing (läuft im Browser, das Foto wird nicht hochgeladen)
 *   Meldungen bei neuen Angeboten ... kostenlose App "ntfy" (Absender: GitHub, siehe notify.yml)
 *   Statusleiste .............. theme-color + farbiger Streifen
 */
(function () {
  'use strict';
  if (window.CanRadarNative) return;

  /* ===================== Einstellungen (nur hier ändern) ===================== */
  var VERSION = '1.9.0';
  /* Adresse der deals.json – genau dieselbe wie in der Android-App (DealsSync.java). */
  var DATA_URL = 'https://raw.githubusercontent.com/dudummerhure-rgb/canradar/main/deals.json';
  var NTFY_URL = 'https://ntfy.sh';
  var NTFY_STORE = 'https://apps.apple.com/app/ntfy/id1625396347';
  var LIBS = {
    tesseract: [
      'https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.min.js',
      'https://unpkg.com/tesseract.js@5.1.1/dist/tesseract.min.js',
      'https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js'
    ],
    zxing: [
      'https://cdn.jsdelivr.net/npm/@zxing/library@0.21.3/umd/index.min.js',
      'https://unpkg.com/@zxing/library@0.21.3/umd/index.min.js'
    ]
  };
  var MAX_SIDE = 2400;   // längste Bildkante für Barcodes
  var OCR_SIDE = 2000;   // längste Bildkante für die Texterkennung (schneller)

  /* ===================== kleine Helfer ===================== */
  var LS = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { } },
    del: function (k) { try { localStorage.removeItem(k); } catch (e) { } }
  };
  function tick() { return new Promise(function (r) { setTimeout(r, 0); }); }
  function msg(e) { return String((e && e.message) || e || ''); }

  /* ===================== Fehlerprotokoll (ersetzt den Absturz-Bildschirm der Android-App) ===================== */
  var ERR_KEY = 'canradar_errlog';
  function logErr(where, text) {
    try {
      text = String(text || '');
      if (!text || /ResizeObserver|^Script error\.?$/i.test(text)) return;
      var arr = JSON.parse(LS.get(ERR_KEY) || '[]');
      arr.push({ t: new Date().toISOString(), w: where, m: text.slice(0, 400) });
      LS.set(ERR_KEY, JSON.stringify(arr.slice(-5)));
    } catch (e) { }
  }
  window.addEventListener('error', function (e) {
    logErr('Fehler', (e.message || '') + (e.filename ? ' @' + String(e.filename).split('/').pop() + ':' + e.lineno : ''));
  });
  window.addEventListener('unhandledrejection', function (e) { logErr('Promise', msg(e.reason)); });

  /* ===================== Nachladen der Bibliotheken ===================== */
  var loading = {};
  function loadScript(urls) {
    return new Promise(function (resolve, reject) {
      var i = 0;
      (function next() {
        if (i >= urls.length) { reject(new Error('lib')); return; }
        var s = document.createElement('script');
        var done = false;
        var to = setTimeout(function () { if (!done) { done = true; s.remove(); next(); } }, 25000);
        s.src = urls[i++];
        s.crossOrigin = 'anonymous';
        s.onload = function () { if (done) return; done = true; clearTimeout(to); resolve(); };
        s.onerror = function () { if (done) return; done = true; clearTimeout(to); s.remove(); next(); };
        document.head.appendChild(s);
      })();
    });
  }
  function lib(name, glob) {
    if (window[glob]) return Promise.resolve();
    if (!loading[name]) {
      loading[name] = loadScript(LIBS[name]).then(function () {
        if (!window[glob]) throw new Error('lib');
      }).catch(function (e) { loading[name] = null; throw e; });
    }
    return loading[name];
  }

  /* ===================== Fortschritt / Antwort an die App ===================== */
  var lastProg = 0;
  function prog(t, force) {
    var n = Date.now();
    if (!force && n - lastProg < 250) return;
    lastProg = n;
    try { if (window.__scanProg) window.__scanProg(t); } catch (e) { }
  }
  function reply(o) { try { if (window.__scanCb) window.__scanCb(o); } catch (e) { logErr('scanCb', msg(e)); } }

  /* ===================== Bild laden und bearbeiten ===================== */
  function loadImage(file) {
    return new Promise(function (res, rej) {
      var url = URL.createObjectURL(file);
      var im = new Image();
      im.onload = function () { URL.revokeObjectURL(url); res(im); };
      im.onerror = function () { URL.revokeObjectURL(url); rej(new Error('decode')); };
      im.src = url;
    });
  }
  function toCanvas(im, maxSide) {
    var w = im.naturalWidth || im.width, h = im.naturalHeight || im.height;
    if (!w || !h) throw new Error('decode');
    var f = Math.min(1, maxSide / Math.max(w, h));
    var c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(w * f));
    c.height = Math.max(1, Math.round(h * f));
    var x = c.getContext('2d');
    x.imageSmoothingEnabled = true;
    x.imageSmoothingQuality = 'high';
    x.fillStyle = '#fff';
    x.fillRect(0, 0, c.width, c.height);
    x.drawImage(im, 0, 0, c.width, c.height);
    return c;
  }
  function scaled(cv, f) {
    var c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(cv.width * f));
    c.height = Math.max(1, Math.round(cv.height * f));
    var x = c.getContext('2d');
    x.imageSmoothingQuality = 'high';
    x.drawImage(cv, 0, 0, c.width, c.height);
    return c;
  }
  function crop(cv, x, y, w, h) {
    var c = document.createElement('canvas');
    c.width = Math.max(1, w); c.height = Math.max(1, h);
    c.getContext('2d').drawImage(cv, x, y, w, h, 0, 0, c.width, c.height);
    return c;
  }
  /* Graustufen mit kräftigerem Kontrast (wie "enhance" in der Android-App): hilft bei Spiegelung und blassem Druck. */
  function enhance(cv) {
    var o = document.createElement('canvas');
    o.width = cv.width; o.height = cv.height;
    var c = o.getContext('2d', { willReadFrequently: true });
    c.drawImage(cv, 0, 0);
    var id = c.getImageData(0, 0, o.width, o.height), d = id.data;
    var k = 1.7, t = (-0.5 * k + 0.5) * 255;
    for (var i = 0; i < d.length; i += 4) {
      var g = 0.213 * d[i] + 0.715 * d[i + 1] + 0.072 * d[i + 2];
      var v = k * g + t;
      v = v < 0 ? 0 : v > 255 ? 255 : v;
      d[i] = d[i + 1] = d[i + 2] = v;
    }
    c.putImageData(id, 0, 0);
    return o;
  }

  /* ===================== Texterkennung (Tesseract.js) ===================== */
  var worker = null;
  function ocrLog(m) {
    var p = Math.round((m && m.progress || 0) * 100), s = (m && m.status) || '';
    if (/loading tesseract core/.test(s)) prog('Texterkennung wird geladen (nur beim ersten Mal) …');
    else if (/initializing/.test(s)) prog('Texterkennung startet …');
    else if (/loading language/.test(s)) prog('Sprachdaten werden geladen (nur beim ersten Mal) … ' + p + ' %');
    else if (/recogniz/.test(s)) prog('Text wird gelesen … ' + p + ' %');
  }
  function getWorker() {
    if (!worker) {
      worker = lib('tesseract', 'Tesseract').then(function () {
        return window.Tesseract.createWorker('deu', 1, { logger: ocrLog });
      }).then(function (w) {
        return Promise.resolve(w.setParameters({ preserve_interword_spaces: '1' })).then(function () { return w; });
      }).catch(function (e) { worker = null; throw e; });
    }
    return worker;
  }
  /* Wie viele Schlüsselwörter der Nährwerttabelle im Text stehen (Maß für "gut gelesen"). */
  function score(t) {
    if (!t) return 0;
    var s = String(t).toLowerCase(), n = 0;
    var keys = ['kcal', 'kj', 'zucker', 'sugar', 'koffein', 'coffein', 'caffein', 'mg', '100 ml', '100ml', 'nährwert', 'energie'];
    keys.forEach(function (k) {
      var i = s.indexOf(k);
      while (i >= 0) { n++; i = s.indexOf(k, i + k.length); }
    });
    return n;
  }
  /* Baut aus den erkannten Zeilen wieder Tabellenzeilen: Zeilen auf gleicher Höhe werden von links nach rechts
     zusammengesetzt ("Zucker" + "11 g" + "55 g" -> "Zucker 11 g 55 g"). */
  function rowsFrom(lines) {
    var ls = [];
    (lines || []).forEach(function (l) {
      var b = l && l.bbox, t = l && String(l.text || '').replace(/\s*\n\s*/g, ' ').trim();
      if (!b || !t) return;
      ls.push({ t: t, x0: b.x0, cy: (b.y0 + b.y1) / 2, h: Math.max(1, b.y1 - b.y0) });
    });
    if (!ls.length) return '';
    ls.sort(function (a, b) { return a.cy - b.cy; });
    var out = [], row = [], rowY = 0, rowH = 0;
    function flush() {
      if (!row.length) return;
      row.sort(function (a, b) { return a.x0 - b.x0; });
      out.push(row.map(function (r) { return r.t; }).join(' '));
      row = [];
    }
    ls.forEach(function (l) {
      if (row.length && Math.abs(l.cy - rowY) > Math.max(rowH, l.h) * 0.55) flush();
      if (!row.length) { rowY = l.cy; rowH = l.h; }
      row.push(l);
    });
    flush();
    return out.join('\n') + '\n';
  }
  function linesOf(data) {
    if (data.lines && data.lines.length) return data.lines;
    var out = [];
    (data.blocks || []).forEach(function (b) {
      (b.paragraphs || []).forEach(function (p) { (p.lines || []).forEach(function (l) { out.push(l); }); });
    });
    return out;
  }
  async function recognize(w, cv, psm) {
    await w.setParameters({ tessedit_pageseg_mode: String(psm) });
    var r = await w.recognize(cv);
    var d = r.data || {};
    return { text: d.text || '', rows: rowsFrom(linesOf(d)) };
  }
  /* Tabellen liest Tesseract im Modus "einzelner Block" (6) am besten, der Automatikmodus (3) verliert oft die
     Zahlenspalten. Reihenfolge: Original (6) -> mehr Kontrast (6) -> Original (3). Es zählt der beste Versuch. */
  async function readTable(cv) {
    prog('Texterkennung wird vorbereitet …', true);
    var w = await getWorker();
    var small = scaled(cv, Math.min(1, OCR_SIDE / Math.max(cv.width, cv.height)));
    var sc = function (r) { return score(r.text) + score(r.rows); };
    var best = await recognize(w, small, 6);
    if (sc(best) < 12) {
      prog('Zweiter Versuch mit mehr Kontrast …', true);
      var r2 = await recognize(w, enhance(small), 6);
      if (sc(r2) > sc(best)) best = r2;
    }
    if (sc(best) < 12) {
      prog('Dritter Versuch …', true);
      var r3 = await recognize(w, small, 3);
      if (sc(r3) > sc(best)) best = r3;
    }
    return best;
  }

  /* ===================== Barcode (ZXing) ===================== */
  var reader = null;
  function zxReader() {
    if (reader) return reader;
    var Z = window.ZXing, hints = new Map();
    hints.set(Z.DecodeHintType.POSSIBLE_FORMATS,
      [Z.BarcodeFormat.EAN_13, Z.BarcodeFormat.EAN_8, Z.BarcodeFormat.UPC_A, Z.BarcodeFormat.UPC_E]);
    hints.set(Z.DecodeHintType.TRY_HARDER, true);
    reader = new Z.MultiFormatReader();
    reader.setHints(hints);
    return reader;
  }
  /* Prüfziffer kontrollieren, damit falsch gelesene Barcodes nicht durchrutschen. */
  function validCode(v, isUpcE) {
    if (!v) return false;
    var n = v.length;
    if (n !== 8 && n !== 12 && n !== 13) return false;
    if (!/^\d+$/.test(v)) return false;
    if (isUpcE) return true;
    var sum = 0;
    for (var i = 0; i < n - 1; i++) {
      var d = v.charCodeAt(i) - 48, pos = n - 1 - i;
      sum += (pos % 2 === 1) ? d * 3 : d;
    }
    return ((10 - (sum % 10)) % 10) === (v.charCodeAt(n - 1) - 48);
  }
  function zxDecode(cv) {
    try {
      var Z = window.ZXing;
      var bmp = new Z.BinaryBitmap(new Z.HybridBinarizer(new Z.HTMLCanvasElementLuminanceSource(cv)));
      var r = zxReader().decodeWithState(bmp); // nutzt die vorher gesetzten Hinweise (nur EAN/UPC, gründlich)
      var t = r && r.getText && r.getText();
      var upcE = r.getBarcodeFormat && r.getBarcodeFormat() === Z.BarcodeFormat.UPC_E;
      return validCode(t, upcE) ? t : null;
    } catch (e) { return null; }
  }
  /* Sucht Barcodes: erst das ganze Bild, dann mit Kontrast, dann verkleinert, dann in Ausschnitten. */
  async function findBarcodes(cv, thorough) {
    var found = [];
    function tryIt(c) { var v = zxDecode(c); if (v && found.indexOf(v) < 0) found.push(v); return found.length > 0; }
    if (tryIt(cv)) return found;
    await tick();
    var en = enhance(cv);
    if (tryIt(en)) return found;
    if (!thorough) return found;
    await tick();
    if (tryIt(scaled(cv, 0.5)) || tryIt(scaled(en, 0.5))) return found;
    var w = cv.width, h = cv.height;
    var regs = [[.15, .15, .85, .85], [0, 0, .65, .65], [.35, 0, 1, .65], [0, .35, .65, 1], [.35, .35, 1, 1], [0, .25, 1, .75]];
    for (var pass = 0; pass < 2; pass++) {
      var src = pass === 0 ? cv : en;
      for (var i = 0; i < regs.length; i++) {
        var q = regs[i], x = Math.floor(q[0] * w), y = Math.floor(q[1] * h);
        var c = crop(src, x, y, Math.floor(q[2] * w) - x, Math.floor(q[3] * h) - y);
        if (tryIt(c)) return found;
        await tick();
      }
    }
    return found;
  }

  /* ===================== Foto aufnehmen / auswählen ===================== */
  /* Wichtig: input.click() muss direkt im Tippen-Ereignis passieren, sonst blockt iOS die Kamera. */
  function pickFile(gallery, onFile, onCancel) {
    var inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = 'image/*';
    if (!gallery) inp.setAttribute('capture', 'environment');
    inp.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0;width:1px;height:1px';
    document.body.appendChild(inp);
    var got = false, cancelled = false, t = 0;
    function cleanup() { window.removeEventListener('focus', arm); document.removeEventListener('visibilitychange', onVis); }
    function cancel() { if (got || cancelled) return; cancelled = true; cleanup(); onCancel(); }
    function arm() { clearTimeout(t); t = setTimeout(function () { if (!(inp.files && inp.files.length)) cancel(); }, 3000); }
    function onVis() { if (!document.hidden) arm(); }
    inp.addEventListener('change', function () {
      clearTimeout(t); cleanup();
      var f = inp.files && inp.files[0];
      if (!f) { cancel(); return; }
      got = true;
      onFile(f);
      setTimeout(function () { inp.remove(); }, 120000);
    });
    inp.addEventListener('cancel', cancel);
    window.addEventListener('focus', arm);
    document.addEventListener('visibilitychange', onVis);
    inp.click();
  }

  async function analyze(file, kind) {
    prog('Foto wird geladen …', true);
    var cv;
    try { cv = toCanvas(await loadImage(file), MAX_SIDE); }
    catch (e) { return { error: 'Bild konnte nicht gelesen werden' }; }
    var barcodeOnly = kind === 'barcode';
    var out = { kind: barcodeOnly ? 'barcode' : 'table', text: '', rows: '', codes: [] };
    if (!barcodeOnly) {
      try {
        var t = await readTable(cv);
        out.text = t.text; out.rows = t.rows;
      } catch (e) {
        return { error: 'Texterkennung konnte nicht geladen werden (Internet nötig, nur beim ersten Mal)' };
      }
    }
    try {
      await lib('zxing', 'ZXing');
      prog('Barcode wird gesucht …', true);
      out.codes = await findBarcodes(cv, barcodeOnly);
    } catch (e) {
      if (barcodeOnly) return { error: 'Barcode-Scanner konnte nicht geladen werden (Internet nötig, nur beim ersten Mal)' };
    }
    return out;
  }

  function scanLabel(mode) {
    var m = mode == null ? '' : String(mode);
    var kind = /:barcode$/.test(m) ? 'barcode' : 'table';
    var gallery = m.indexOf('gallery') === 0;
    try {
      pickFile(gallery, function (file) {
        analyze(file, kind).then(reply).catch(function (e) {
          reply({ error: /memory|alloc/i.test(msg(e)) ? 'Das Foto ist zu groß für den Speicher' : 'Bild konnte nicht gelesen werden' });
        });
      }, function () { reply({ cancel: true }); });
    } catch (e) {
      reply({ error: gallery ? 'Keine Bildauswahl gefunden' : 'Keine Kamera-App gefunden' });
    }
  }

  /* ===================== Barcode live mit der Kamera ===================== */
  var liveState = null;
  function liveStop() {
    var s = liveState; liveState = null;
    if (!s) return;
    s.alive = false;
    clearTimeout(s.timer);
    try { s.stream && s.stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) { }
    try { s.video.srcObject = null; } catch (e) { }
    try { s.root.remove(); } catch (e) { }
  }
  function scanBarcodeLive() {
    if (liveState) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { reply({ liveFail: true }); return; }
    var root = document.createElement('div');
    root.id = 'crLive';
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-label', 'Barcode scannen');
    root.innerHTML =
      '<video playsinline muted autoplay></video>' +
      '<div class="crFrame"><i></i></div>' +
      '<div class="crTop"><b>Barcode in den Rahmen halten</b><span id="crLiveSub">Kamera wird gestartet …</span></div>' +
      '<div class="crBot"><button type="button" id="crLiveFoto">Foto statt live</button><button type="button" id="crLiveX">Abbrechen</button></div>';
    document.body.appendChild(root);
    var video = root.querySelector('video');
    var S = liveState = { alive: true, root: root, video: video, stream: null, timer: 0, n: 0 };
    var sub = root.querySelector('#crLiveSub');
    var work = document.createElement('canvas');
    var wctx = work.getContext('2d', { willReadFrequently: true });

    root.querySelector('#crLiveX').onclick = function () { liveStop(); reply({ cancel: true }); };
    root.querySelector('#crLiveFoto').onclick = function () { liveStop(); scanLabel('camera:barcode'); };

    lib('zxing', 'ZXing').then(function () {
      return navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false
      });
    }).then(function (stream) {
      if (!S.alive) { stream.getTracks().forEach(function (t) { t.stop(); }); return; }
      S.stream = stream;
      video.srcObject = stream;
      return video.play().catch(function () { }).then(function () {
        sub.textContent = 'Ruhig halten, Strichcode waagerecht, nicht zu nah.';
        loop();
      });
    }).catch(function (e) {
      var denied = /NotAllowed|Permission|denied/i.test(String(e && e.name) + msg(e));
      liveStop();
      reply({ liveFail: true, reason: denied ? 'denied' : 'other' });
    });

    function loop() {
      if (!S.alive) return;
      try {
        var vw = video.videoWidth, vh = video.videoHeight;
        if (vw && vh && video.readyState >= 2) {
          var cw = Math.round(vw * 0.92), ch = Math.round(vh * 0.5);
          var cx = Math.round((vw - cw) / 2), cy = Math.round((vh - ch) / 2);
          var f = Math.min(1, 1500 / cw);
          work.width = Math.max(40, Math.round(cw * f));
          work.height = Math.max(40, Math.round(ch * f));
          wctx.drawImage(video, cx, cy, cw, ch, 0, 0, work.width, work.height);
          S.n++;
          var v = zxDecode(work);
          if (!v && S.n % 3 === 0) {
            var id = wctx.getImageData(0, 0, work.width, work.height), d = id.data, k = 1.7, t = (-0.5 * k + 0.5) * 255;
            for (var i = 0; i < d.length; i += 4) {
              var g = 0.213 * d[i] + 0.715 * d[i + 1] + 0.072 * d[i + 2], q = k * g + t;
              d[i] = d[i + 1] = d[i + 2] = q < 0 ? 0 : q > 255 ? 255 : q;
            }
            wctx.putImageData(id, 0, 0);
            v = zxDecode(work);
          }
          if (v) {
            liveStop();
            reply({ kind: 'barcode', live: true, text: '', rows: '', codes: [v] });
            return;
          }
        }
      } catch (e) { logErr('live', msg(e)); }
      S.timer = setTimeout(loop, 110);
    }
  }

  /* ===================== Angebote abrufen (wie DealsSync.fetch) ===================== */
  function fetchDeals() {
    var ok = false, payload = '';
    var ac = ('AbortController' in window) ? new AbortController() : null;
    var to = setTimeout(function () { if (ac) ac.abort(); }, 15000);
    fetch(DATA_URL + '?t=' + Date.now(), { cache: 'no-store', signal: ac ? ac.signal : undefined })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      })
      .then(function (t) {
        if (t.length > 1000000) throw new Error('Datei zu gross');
        ok = true; payload = t;
      })
      .catch(function (e) {
        ok = false;
        payload = (e && e.name === 'AbortError') ? 'Zeitüberschreitung'
          : (e && e.name === 'TypeError') ? 'Kein Internet' : msg(e);
      })
      .then(function () {
        clearTimeout(to);
        try { if (window.__dealsCb) window.__dealsCb(ok, payload); } catch (e) { logErr('dealsCb', msg(e)); }
      });
  }

  /* ===================== Meldungen über ntfy ===================== */
  var NT_KEY = 'canradar_ntfy';
  var topicOk = function (t) { return /^[A-Za-z0-9_-]{8,64}$/.test(t || ''); };
  function appUrl() { return location.origin + location.pathname.replace(/index\.html$/, ''); }
  function ntfyTest() {
    var t = LS.get(NT_KEY);
    if (!topicOk(t)) return Promise.resolve({ ok: false, msg: 'Kein gültiges Thema eingetragen.' });
    var q = new URLSearchParams({
      title: 'Test: Monster ist im Angebot',
      message: 'Benachrichtigungen funktionieren.\n\nSo sieht eine Meldung aus:\nNetto: 0,88 € (statt 1,59 €) · bis 31.10.\n\n' +
        'Du bekommst sie, sobald neue Monster- oder Red-Bull-Angebote erscheinen.',
      tags: 'zap',
      click: appUrl()
    });
    return fetch(NTFY_URL + '/' + encodeURIComponent(t) + '/publish?' + q.toString(), { cache: 'no-store' })
      .then(function (r) { return r.ok ? { ok: true } : { ok: false, msg: 'ntfy antwortet mit Fehler ' + r.status + '.' }; })
      .catch(function () { return { ok: false, msg: 'Keine Verbindung zu ntfy (Internet?).' }; });
  }
  function randomTopic() {
    var abc = 'abcdefghijkmnpqrstuvwxyz23456789', a = new Uint8Array(10), s = 'canradar-';
    (window.crypto || window.msCrypto).getRandomValues(a);
    for (var i = 0; i < a.length; i++) s += abc.charAt(a[i] % abc.length);
    return s;
  }

  /* ===================== Statusleiste ===================== */
  /* iPhone-Web-Apps zeigen die Uhrzeit oben in Weiß. Bei hellem Hintergrund wäre sie unsichtbar,
     deshalb bekommt der Streifen oben dann eine dunkle Farbe. */
  function setBars(top, bottom, light) {
    var bar = light ? '#14183a' : top;
    try {
      var m = document.querySelector('meta[name="theme-color"]');
      if (!m) { m = document.createElement('meta'); m.name = 'theme-color'; document.head.appendChild(m); }
      m.setAttribute('content', bar);
      document.documentElement.style.setProperty('--sbar', bar);
    } catch (e) { }
  }

  /* ===================== die Schnittstelle, wie sie die App kennt ===================== */
  window.CanRadarNative = {
    version: VERSION,
    fetchDeals: fetchDeals,
    scanLabel: scanLabel,
    scanBarcodeLive: scanBarcodeLive,
    setBars: setBars,
    /* Meldungen */
    notifState: function () { return topicOk(LS.get(NT_KEY)) ? 'on' : 'ask'; },
    ntfyGet: function () { return LS.get(NT_KEY) || ''; },
    ntfyValid: topicOk,
    ntfySet: function (t) { t = String(t || '').trim(); if (topicOk(t)) LS.set(NT_KEY, t); else if (!t) LS.del(NT_KEY); return topicOk(t); },
    ntfyTest: ntfyTest,
    ntfyRandom: randomTopic,
    openNtfyStore: function () { try { window.open(NTFY_STORE, '_blank'); } catch (e) { location.href = NTFY_STORE; } },
    /* Fehlerprotokoll */
    errLog: function () { try { return JSON.parse(LS.get(ERR_KEY) || '[]'); } catch (e) { return []; } },
    errClear: function () { LS.del(ERR_KEY); },
    logErr: logErr
  };

  /* ===================== Hinweis zum Installieren (nur iPhone/iPad, nur im Safari-Tab) ===================== */
  function isIOS() { return /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1); }
  function isStandalone() {
    return navigator.standalone === true || (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);
  }
  document.addEventListener('DOMContentLoaded', function () {
    try {
      if (!isIOS() || isStandalone() || LS.get('canradar_hint')) return;
      var el = document.createElement('div');
      el.className = 'sheet';
      el.id = 'instPop';
      el.setAttribute('role', 'dialog');
      el.innerHTML =
        '<div class="panel"><div class="ph"><h2>Als App installieren</h2><button class="x" id="instX" aria-label="Schließen">✕</button></div>' +
        '<div class="card"><h3>So kommt Can Radar auf deinen Home-Bildschirm</h3>' +
        '<ol class="steps"><li>Tippe unten in Safari auf <b>Teilen</b> (Quadrat mit Pfeil nach oben). Bei neueren iPhones erst auf die drei Punkte <b>···</b>.</li>' +
        '<li>Wähle <b>Zum Home-Bildschirm</b> (ggf. etwas nach unten wischen).</li>' +
        '<li>Tippe oben rechts auf <b>Hinzufügen</b>.</li>' +
        '<li>Ab jetzt startest du Can Radar über das lila Blitz-Symbol, nicht mehr über Safari.</li></ol></div>' +
        '<button class="btn" id="instOk">Verstanden</button></div>';
      document.body.appendChild(el);
      var close = function () { LS.set('canradar_hint', '1'); el.remove(); };
      el.querySelector('#instX').onclick = close;
      el.querySelector('#instOk').onclick = close;
    } catch (e) { }
  });

  /* ===================== Service Worker (Offline-Start, schneller Start) ===================== */
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('sw.js').catch(function (e) { logErr('sw', msg(e)); });
    });
  }
})();
