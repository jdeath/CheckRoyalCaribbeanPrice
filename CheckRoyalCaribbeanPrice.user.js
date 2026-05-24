// ==UserScript==
// @name         Check Royal Caribbean Price
// @namespace    http://tampermonkey.net/
// @version      1.0.3
// @description  Check cruise prices, add-on prices, and watch list directly in-browser
// @author       jdeath / ported to Greasemonkey
// @match        https://www.royalcaribbean.com/*
// @match        https://www.celebritycruises.com/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  /* ============================================================
     Constants
     ============================================================ */
  const APPKEY_WEB = 'hyNNqIPHHzaLzVpcICPdAdbFV8yvTsAm';
  const USER_AGENT = navigator.userAgent;
  const API_BASE = 'https://aws-prd.api.rccl.com';
  const DATE_FMT_OPTIONS = { year: 'numeric', month: 'short', day: 'numeric' };

  /* ============================================================
     HTTP helper (uses fetch since @grant none = no sandbox)
     ============================================================ */
  function httpGet(url, opts) {
    opts = opts || {};
    var method = opts.method || "GET";
    var headers = opts.headers || {};
    var body = opts.data || null;
    var isText = opts.responseType === "text";
    return new Promise(function(resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open(method, url, true);
      xhr.withCredentials = true;
      for (var h in headers) {
        if (headers.hasOwnProperty(h)) xhr.setRequestHeader(h, headers[h]);
      }
      if (isText) xhr.responseText = 'text';
      xhr.onload = function() {
        var data;
        try {
          data = isText ? xhr.responseText : JSON.parse(xhr.responseText);
        } catch(e) {
          reject(new Error('Parse failed for ' + url + ' (HTTP ' + xhr.status + '): ' + e.message + '. Body preview: ' + xhr.responseText.substring(0, 200)));
          return;
        }
        resolve({ ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status, data: data });
      };
      xhr.onerror = function() {
        reject(new Error('Load failed for ' + url + ': XHR error (status ' + xhr.status + ')'));
      };
      xhr.ontimeout = function() {
        reject(new Error('Load timed out for ' + url));
      };
      xhr.timeout = 30000;
      xhr.send(body);
    });
  }

  /* ============================================================
     Settings (persisted via GM_*)
     ============================================================ */
  const DEFAULT_SETTINGS = {
    currencyOverride: '',
    minimumSavingAlert: 0,
    showPromos: false,
    watchList: [],
  };

  function loadSettings() {
    try {
      var raw = localStorage.getItem('_rccl_pricecheck_settings');
      if (raw) return Object.assign({}, DEFAULT_SETTINGS, JSON.parse(raw));
    } catch (e) {}
    return Object.assign({}, DEFAULT_SETTINGS);
  }

  function saveSettings(s) {
    try { localStorage.setItem('_rccl_pricecheck_settings', JSON.stringify(s)); } catch (e) {}
  }

  var settings = loadSettings();

  /* ============================================================
     Auth helpers
     ============================================================ */
  var cachedAuth = null;
  var foundKeys = {};

  function decodeJwtPayload(token) {
    try {
      var parts = token.split('.');
      var payload = parts[1];
      var pad = payload.length % 4;
      if (pad) payload += '=='.substring(pad);
      return JSON.parse(atob(payload));
    } catch (_) {
      return null;
    }
  }

  function isJwt(str) {
    return typeof str === 'string' && str.length > 20 && str.indexOf('.') !== -1 && str.indexOf('eyJ') !== -1;
  }

  function tryToken(tok) {
    if (!tok || !isJwt(tok)) return null;
    var payload = decodeJwtPayload(tok);
    if (payload && payload.sub) return { accessToken: tok, accountId: payload.sub };
    return null;
  }

  // Inject a script into the page to read localStorage from the page context
  // This works around Greasemonkey/Tampermonkey sandboxing
  function injectTokenReader() {
    var script = document.createElement('script');
    script.textContent =
      'var _rcclTokenData = {};\n' +
      '(function() {\n' +
      '  function isJwt(s) { return typeof s === "string" && s.length > 20 && s.indexOf(".") !== -1 && s.indexOf("eyJ") !== -1; }\n' +
      '  function findJwt(obj, depth) {\n' +
      '    depth = depth || 0;\n' +
      '    if (depth > 10 || !obj || typeof obj !== "object") return null;\n' +
      '    if (typeof obj === "string" && isJwt(obj)) return obj;\n' +
      '    var keys = Object.keys(obj);\n' +
      '    for (var i = 0; i < keys.length; i++) {\n' +
      '      var f = findJwt(obj[keys[i]], depth + 1);\n' +
      '      if (f) return f;\n' +
      '    }\n' +
      '    return null;\n' +
      '  }\n' +
      '  try {\n' +
      '    for (var i = 0; i < localStorage.length; i++) {\n' +
      '      var k = localStorage.key(i);\n' +
      '      var v = localStorage.getItem(k);\n' +
      '      if (isJwt(v)) { _rcclTokenData.accessToken = v; _rcclTokenData.key = k; break; }\n' +
      '      try { var p = JSON.parse(v); var f = findJwt(p, 0); if (f) { _rcclTokenData.accessToken = f; _rcclTokenData.key = k + " (nested)"; break; } } catch(e){}\n' +
      '    }\n' +
      '  } catch(e){}\n' +
      '  if (!_rcclTokenData.accessToken) {\n' +
      '    try {\n' +
      '      for (var i = 0; i < sessionStorage.length; i++) {\n' +
      '        var k = sessionStorage.key(i);\n' +
      '        var v = sessionStorage.getItem(k);\n' +
      '        if (isJwt(v)) { _rcclTokenData.accessToken = v; _rcclTokenData.key = k; break; }\n' +
      '      }\n' +
      '    } catch(e){}\n' +
      '  }\n' +
      '  if (!_rcclTokenData.accessToken && window.__INITIAL_STATE__) {\n' +
      '    var f = findJwt(window.__INITIAL_STATE__, 0);\n' +
      '    if (f) { _rcclTokenData.accessToken = f; _rcclTokenData.key = "__INITIAL_STATE__"; }\n' +
      '  }\n' +
      '  if (_rcclTokenData.accessToken) {\n' +
      '    try { var _p = JSON.parse(atob(_rcclTokenData.accessToken.split(".")[1] + "==")); _rcclTokenData.accountId = _p.sub; _rcclTokenData.email = _p.email || _p.preferred_username || ""; } catch(e){}\n' +
      '  }\n' +
      '})();\n';
    document.head.appendChild(script);
    // Remove after execution
    setTimeout(function() { if (script.parentNode) script.parentNode.removeChild(script); }, 100);
  }

  async function _scanIndexedDB() {
    try {
      if (!indexedDB || !indexedDB.databases) return null;
      var dbs = await indexedDB.databases();
      for (var di = 0; di < dbs.length; di++) {
        try {
          var db = await new Promise(function (resolve) {
            var req = indexedDB.open(dbs[di].name);
            req.onsuccess = function () { resolve(req.result); };
            req.onerror = function () { resolve(null); };
            req.onupgradeneeded = function () { req.transaction && req.transaction.abort(); resolve(null); };
          });
          if (!db) continue;
          var stores = db.objectStoreNames;
          for (var si = 0; si < stores.length; si++) {
            try {
              var tx = db.transaction(stores[si], 'readonly');
              var store = tx.objectStore(stores[si]);
              var cursor = await new Promise(function (resolve) {
                var req = store.openCursor();
                req.onsuccess = function () { resolve(req.result); };
                req.onerror = function () { resolve(null); };
              });
              while (cursor) {
                var val = cursor.value;
                var found = null;
                if (typeof val === 'string' && isJwt(val)) {
                  var pl = decodeJwtPayload(val);
                  if (pl && pl.sub) found = val;
                } else if (typeof val === 'object' && val !== null) {
                  var nested = findJwtSync(val);
                  if (nested) {
                    var pl2 = decodeJwtPayload(nested);
                    if (pl2 && pl2.sub) found = nested;
                  }
                }
                if (found) {
                  var payload = decodeJwtPayload(found);
                  cachedAuth = { accessToken: found, accountId: payload.sub, email: payload.email || payload.preferred_username || '' };
                  db.close();
                  return cachedAuth;
                }
                var nextCursor = await new Promise(function (resolve) {
                  var req = cursor.continue();
                  req.onsuccess = function () { resolve(req.result); };
                  req.onerror = function () { resolve(null); };
                });
                cursor = nextCursor;
              }
            } catch (_) {}
          }
          db.close();
        } catch (_) {}
      }
    } catch (_) {}
    return null;
  }

  function findJwtSync(obj, depth) {
    depth = depth || 0;
    if (depth > 10 || !obj || typeof obj !== 'object') return null;
    if (typeof obj === 'string' && isJwt(obj)) return obj;
    var keys = Object.keys(obj);
    for (var i = 0; i < keys.length; i++) {
      var f = findJwtSync(obj[keys[i]], depth + 1);
      if (f) return f;
    }
    return null;
  }

  async function extractAuth() {
    if (cachedAuth) return cachedAuth;

    // First try: read from page-injected data
    injectTokenReader();
    if (window._rcclTokenData && window._rcclTokenData.accessToken) {
      cachedAuth = {
        accessToken: window._rcclTokenData.accessToken,
        accountId: window._rcclTokenData.accountId,
        email: window._rcclTokenData.email || ''
      };
      return cachedAuth;
    }

    // Second try: direct access (works in non-sandboxed mode)
    try {
      var allKeys = [];
      for (var i = 0; i < localStorage.length; i++) {
        allKeys.push({ key: localStorage.key(i), val: localStorage.getItem(localStorage.key(i)) });
      }
      for (var si = 0; si < allKeys.length; si++) {
        var r = tryToken(allKeys[si].val);
        if (r) { cachedAuth = r; return r; }
      }
    } catch (e) {}

    // Third try: cookies
    var cookieStr = document.cookie;
    var pairs = cookieStr.split(';');
    for (var ci = 0; ci < pairs.length; ci++) {
      var eq = pairs[ci].indexOf('=');
      if (eq > 0) {
        var cv = pairs[ci].substring(eq + 1);
        var r2 = tryToken(cv);
        if (r2) { cachedAuth = r2; return r2; }
      }
    }

    // Fourth try: IndexedDB (iOS Safari may store tokens here)
    return await _scanIndexedDB();
  }

  /* ============================================================
     API helper
     ============================================================ */
  async function apiCall(url, options) {
    options = options || {};
    var auth = await extractAuth();
    if (!auth) throw new Error('Not authenticated. Please log in to the website first.');
    var headers = Object.assign({}, options.headers || {}, {
      'Access-Token': auth.accessToken,
      'AppKey': APPKEY_WEB,
      'vds-id': auth.accountId,
      'Account-Id': auth.accountId,
      'Accept': 'application/json',
      'Content-Type': 'application/json',
    });
    var resp = await httpGet(url, { headers: headers });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return resp.data;
  }

  /* ============================================================
     Ship dictionary
     ============================================================ */
  var shipDictionary = {};

  async function loadShipDictionary() {
    if (Object.keys(shipDictionary).length > 0) return;
    try {
      const data = await apiCall(API_BASE + '/en/royal/web/v2/ships?sort=name');
      const ships = data.payload ? data.payload.ships : [];
      for (const s of ships) {
        shipDictionary[s.shipCode] = s.name;
      }
    } catch (_) { /* non-fatal */ }
  }

  /* ============================================================
     Date helpers
     ============================================================ */
  function formatDate(isoStr) {
    if (!isoStr) return '';
    var d = new Date(isoStr.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3'));
    return d.toLocaleDateString(undefined, DATE_FMT_OPTIONS);
  }

  function formatDateTime(isoStr) {
    if (!isoStr) return '';
    return new Date(isoStr).toLocaleString();
  }

  function aboveAgeOnSailDate(birthDate, sailDate, threshold) {
    var b = new Date(birthDate);
    var s = new Date(sailDate.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3'));
    var age = s.getFullYear() - b.getFullYear();
    if ((s.getMonth() * 100 + s.getDate()) < (b.getMonth() * 100 + b.getDate())) age -= 1;
    return age >= threshold;
  }

  function getFinalPaymentDate(numberOfNights, sailDate) {
    var sail = new Date(sailDate.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3'));
    var deadline = 75;
    if (numberOfNights >= 15) deadline = 120;
    else if (numberOfNights >= 5) deadline = 90;
    var d = new Date(sail);
    d.setDate(d.getDate() - deadline);
    return d;
  }

  /* ============================================================
     Profile / Loyalty
     ============================================================ */
  async function getProfile(accountId, accessToken) {
    var data = await apiCall(
      API_BASE + '/en/royal/web/v3/guestAccounts/' + accountId,
      { headers: { 'Access-Token': accessToken, 'AppKey': APPKEY_WEB, 'account-id': accountId } }
    );
    return data.payload || {};
  }

  /* ============================================================
     Voyages / Bookings
     ============================================================ */
  async function getVoyages(accountId, accessToken, brandCode) {
    var data = await apiCall(
      API_BASE + '/v1/profileBookings/enriched/' + accountId + '?brand=' + brandCode + '&includeCheckin=false'
    );
    return data.payload ? data.payload.profileBookings : [];
  }

  /* ============================================================
     Dining & Prices (RSC endpoint)
     ============================================================ */
  async function getDiningAndPrices(amendToken, isRoyal, country) {
    country = country || 'USA';
    var url = isRoyal
      ? 'https://www.royalcaribbean.com/usa/en/booked/overview'
      : 'https://www.celebritycruises.com/usa/en/booked/overview';
    var resp2 = await httpGet(url + '?token=' + amendToken + '&country=' + country, { responseType: 'text' });
    var text = resp2.data;
    return extractJsonFromRsc(text);
  }

  function extractJsonFromRsc(text) {
    var result = { diningSelection: [], prices: [], pricingAddOns: [] };

    // Use regex to find JSON arrays in RSC response
    var diningMatch = text.match(/"diningSelection"\s*:\s*(\[[\s\S]*?\])\s*[,}]/);
    if (diningMatch) { try { result.diningSelection = JSON.parse(diningMatch[1]); } catch (_) {} }

    var pricesMatch = text.match(/"prices"\s*:\s*(\[[\s\S]*?\])\s*[,}]/);
    if (pricesMatch) { try { result.prices = JSON.parse(pricesMatch[1]); } catch (_) {} }

    var addOnsMatch = text.match(/"pricingAddOns"\s*:\s*(\[[\s\S]*?\])\s*[,}]/);
    if (addOnsMatch) { try { result.pricingAddOns = JSON.parse(addOnsMatch[1]); } catch (_) {} }

    return result;
  }

  /* ============================================================
     OBC
     ============================================================ */
  async function getOBC(reservationId, passengerId, shipCode, sailDate, currency) {
    try {
      var params = new URLSearchParams({
        passengerId: passengerId,
        sailingId: shipCode + sailDate,
        currencyIso: currency,
      });
      var data = await apiCall(
        API_BASE + '/en/royal/web/commerce-api/cart/v1/obc/reservations/' + reservationId + '?' + params
      );
      return data.payload || null;
    } catch (_) { return null; }
  }

  /* ============================================================
     Check-in info
     ============================================================ */
  async function getCheckinInfo(shipCode, sailDate) {
    try {
      var data = await apiCall(
        API_BASE + '/en/royal/web/v3/ships/voyages/' + shipCode + sailDate + '/enriched'
      );
      return data.payload ? data.payload.sailingInfo[0] : null;
    } catch (_) { return null; }
  }

  /* ============================================================
     Orders (add-ons)
     ============================================================ */
  async function getOrders(reservationId, passengerId, ship, sailDate, currency) {
    var params = new URLSearchParams({
      reservationId: reservationId,
      passengerId: passengerId,
      sailingId: ship + sailDate,
      currencyIso: currency,
      includeMedia: 'false',
    });
    var data = await apiCall(
      API_BASE + '/en/royal/web/commerce-api/calendar/v1/' + ship + '/orderHistory?' + params
    );
    return data.payload || null;
  }

  /* ============================================================
     Promotions
     ============================================================ */
  async function getPromotions(ship, sailDate, currency) {
    try {
      var params = new URLSearchParams({
        sailingId: ship + sailDate,
        page: 'homepage',
        currencyIso: currency,
      });
      var data = await apiCall(
        API_BASE + '/en/royal/web/commerce-api/catalog/v2/promotions/list?' + params
      );
      return data.payload || [];
    } catch (_) { return []; }
  }

  /* ============================================================
     Add-on price check (catalog API)
     ============================================================ */
  async function getProductPrice(ship, prefix, product, reservationId, sailDate, passengerId, currency) {
    try {
      var auth = await extractAuth();
      if (!auth) return null;
      var params = new URLSearchParams({
        reservationId: reservationId,
        startDate: sailDate,
        currencyIso: currency,
        passengerId: passengerId,
      });
      var headers = {
        'Access-Token': auth.accessToken,
        'AppKey': APPKEY_WEB,
        'vds-id': auth.accountId,
      };
      var url = API_BASE + '/en/royal/web/commerce-api/catalog/v2/' + ship + '/categories/' + prefix + '/products/' + product + '?' + params;
      var resp = await httpGet(url, { headers: headers });
      if (!resp.ok) return null;
      return resp.data ? resp.data.payload || null : null;
    } catch (e) { return null; }
  }

  /* ============================================================
     Room / Cruise price check
     ============================================================ */
  async function getRoomPriceViaCheckoutAPI(
    isRoyal, countryCode, packageId, sailDate, currencyCode,
    stateroomTypeCode, stateroomSubtypeCode, categoryCode,
    loyaltyNumber, stateCode, fireFighter, military, police, senior,
    couponCode, adultCount, childCount
  ) {
    var apiURL = isRoyal
      ? 'https://www.royalcaribbean.com/checkout/api/v1/rooms/checkout'
      : 'https://www.celebritycruises.com/checkout/api/v1/rooms/checkout';

    var body = {
      countryCode: countryCode,
      packageId: packageId,
      sailDate: sailDate,
      currencyCode: currencyCode,
      language: 'en',
      rooms: [{
        stateroomTypeCode: stateroomTypeCode,
        stateroomSubtypeCode: stateroomSubtypeCode,
        categoryCode: categoryCode,
        fareCode: 'BESTRATE',
        accessible: false,
        qualifiers: {
          fireFighter: fireFighter || false,
          military: military || false,
          police: police || false,
          senior: senior || false,
        },
        occupancy: {
          adultCount: adultCount || 2,
          childCount: childCount || 0,
        },
      }],
    };

    if (couponCode) body.rooms[0].couponCode = couponCode;
    if (stateCode) body.rooms[0].qualifiers.stateCode = stateCode;
    if (loyaltyNumber) body.rooms[0].qualifiers.loyaltyNumber = loyaltyNumber;

    var headers = {
      'user-agent': USER_AGENT,
      'appkey': APPKEY_WEB,
      'accept': '*/*',
      'content-type': 'application/json',
    };
    var resp4 = await httpGet(apiURL, {
      method: 'POST',
      headers: headers,
      data: JSON.stringify(body)
    });
    if (!resp4.ok) return null;
    return resp4.data;
  }

  /* ============================================================
     Check room availability (RSC)
     ============================================================ */
  async function checkRoomAvailability(
    isRoyal, countryCode, packageId, sailDate, currencyCode,
    stateroomSubtypeCode, categoryCode, adultCount, childCount
  ) {
    var apiURL = isRoyal
      ? 'https://www.royalcaribbean.com/room-selection/type-and-subtype'
      : 'https://www.celebritycruises.com/room-selection/type-and-subtype';

    var params = new URLSearchParams({
      packageCode: packageId,
      sailDate: sailDate,
      country: countryCode,
      selectedCurrencyCode: currencyCode,
      shipCode: packageId.substring(0, 2),
      cabinClassType: 'INTERIOR',
      roomIndex: '0',
      r0a: adultCount,
      r0c: childCount,
      r0b: 'n', r0r: 'n', r0s: 'n', r0q: 'n', r0t: 'n',
      r0d: 'INTERIOR', r0D: 'y', rgVisited: 'true', r0C: 'y',
    });

    var resp3 = await httpGet(apiURL + '?' + params, {
      headers: {
        'user-agent': USER_AGENT,
        'Accept': 'text/x-component',
        'RSC': '1',
      },
      responseType: 'text'
    });
    var text = resp3.data;
    var rm = text.match(/"rooms"\s*:\s*(\[[\s\S]*?\])\s*\]/);
    if (!rm) return { available: false };

    var rooms;
    try { rooms = JSON.parse(rm[1]); } catch (_) { return { available: false }; }

    var stateroomTypes = rooms[0] ? rooms[0].options ? rooms[0].options.stateroomTypes : null : null;
    if (!stateroomTypes) return { available: false };

    for (var i = 0; i < stateroomTypes.length; i++) {
      var subs = stateroomTypes[i].stateroomSubtypes || [];
      for (var j = 0; j < subs.length; j++) {
        if (subs[j].code === stateroomSubtypeCode && subs[j].categoryCode === categoryCode) {
          return { available: true };
        }
      }
    }
    return { available: false };
  }

  /* ============================================================
     UI
     ============================================================ */
  var panelOpen = false;

  function createUI() {
    if (document.getElementById('rc-price-check-btn')) return;
    if (!document.body) return;
    var btn = document.createElement('div');
    btn.id = 'rc-price-check-btn';
    btn.textContent = 'Price Check';
    btn.style.cssText =
      'position:fixed;bottom:20px;right:20px;z-index:2147483647;border:2px solid #fff;' +
      'background:#003056;color:#fff;padding:10px 16px;border-radius:8px;' +
      'cursor:pointer;font-family:sans-serif;font-size:14px;font-weight:bold;' +
      'box-shadow:0 2px 12px rgba(0,0,0,0.3);transition:background 0.2s;';
    btn.onmouseover = function () { btn.style.background = '#004a80'; };
    btn.onmouseout = function () { btn.style.background = '#003056'; };
    document.body.appendChild(btn);

    var panel = document.createElement('div');
    panel.id = 'rc-price-check-panel';
   panel.style.cssText =
       'display:none;position:fixed;bottom:70px;right:20px;left:20px;z-index:2147483647;' +
       'max-width:720px;margin-right:0;margin-left:auto;max-height:80vh;overflow-y:auto;overflow-x:hidden;' +
       'background:#fff;color:#222;border-radius:10px;' +
       'box-shadow:0 4px 24px rgba(0,0,0,0.25);' +
       'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;' +
       'font-size:13px;line-height:1.5;padding:16px;word-wrap:break-word;';
    document.body.appendChild(panel);
  }

  function togglePanel() {
    var panel = document.getElementById('rc-price-check-panel');
    var btn = document.getElementById('rc-price-check-btn');
    if (!panel || !btn) return;
    panelOpen = !panelOpen;
    if (panelOpen) {
      panel.style.display = 'block';
      btn.textContent = 'Close';
      panel.innerHTML = '<div style="color:#666;">Loading...</div>';
      runPriceCheck().catch(function(err) {
        panel.innerHTML = '<div style="color:#c00;font-weight:bold;">Fatal error: ' + escapeAttr(err.message) + '</div><div style="font-size:11px;color:#888;white-space:pre-wrap;word-wrap:break-word;">' + escapeAttr(err.stack || '') + '</div>';
        console.error(err);
      });
    } else {
      panel.style.display = 'none';
      btn.textContent = 'Price Check';
    }
  }

  // Use event delegation so button clicks survive SPA re-renders
  document.addEventListener('click', function(e) {
    var target = e.target;
    if (target.id === 'rc-price-check-btn' || target.closest('#rc-price-check-btn')) {
      e.preventDefault();
      e.stopPropagation();
      togglePanel();
    }
  }, true);

  function clearPanel() {
    var panel = document.getElementById('rc-price-check-panel');
    if (!panel) createUI();
    panel = document.getElementById('rc-price-check-panel');
    panel.innerHTML = '';
  }

  function appendHTML(html) {
    var panel = document.getElementById('rc-price-check-panel');
    if (!panel) createUI();
    panel = document.getElementById('rc-price-check-panel');
    var div = document.createElement('div');
    div.innerHTML = html;
    panel.appendChild(div);
  }

  /* ============================================================
     Settings UI
     ============================================================ */
  function renderSettings() {
    var s = loadSettings();
    var html =
      '<details id="rc-settings-details">' +
        '<summary style="cursor:pointer;font-weight:bold;font-size:14px;">Settings</summary>' +
        '<div style="margin-top:8px;">' +
          '<div style="display:inline-block;margin-right:16px;">' +
            '<label>Currency Override: ' +
            '<input id="rc-currency" type="text" value="' + escapeAttr(s.currencyOverride || '') + '" placeholder="e.g. EUR" style="width:80px;">' +
            '</label>' +
          '</div>' +
          '<div style="display:inline-block;margin-right:16px;">' +
            '<label>Min Saving Alert ($): ' +
            '<input id="rc-min-saving" type="number" value="' + (s.minimumSavingAlert || 0) + '" min="0" step="0.01" style="width:80px;">' +
            '</label>' +
          '</div>' +
          '<div style="display:block;margin:4px 0;">' +
            '<label><input type="checkbox" id="rc-show-promos" ' + (s.showPromos ? 'checked' : '') + '> Show Promotions</label>' +
          '</div>' +
          '<div style="display:block;margin:8px 0;">' +
            '<strong>Watch List</strong>' +
            '<button id="rc-add-watch" style="margin-left:8px;">+ Add Item</button>' +
            '<div id="rc-watch-list-container" style="margin-top:4px;"></div>' +
          '</div>' +
          '<div style="text-align:right;margin-top:8px;">' +
            '<button id="rc-save-settings" style="background:#003056;color:#fff;border:none;padding:6px 16px;border-radius:4px;cursor:pointer;">Save Settings</button>' +
          '</div>' +
        '</div>' +
      '</details>';
    appendHTML(html);

    // Render watch list items
    var wlContainer = document.getElementById('rc-watch-list-container');
    if (s.watchList) {
      for (var wi = 0; wi < s.watchList.length; wi++) {
        renderWatchItem(wlContainer, s.watchList[wi], wi);
      }
    }

    // Bind add button
    document.getElementById('rc-add-watch').onclick = function () {
      var s2 = loadSettings();
      s2.watchList.push({
        name: '', prefix: '', product: '', price: 0,
        currency: 'USD', enabled: true, reservations: '', guestAgeString: 'adult'
      });
      renderWatchItem(wlContainer, s2.watchList[s2.watchList.length - 1], s2.watchList.length - 1);
    };

    // Bind save button
    document.getElementById('rc-save-settings').onclick = function () {
      var s2 = loadSettings();
      s2.currencyOverride = document.getElementById('rc-currency').value.trim();
      s2.minimumSavingAlert = parseFloat(document.getElementById('rc-min-saving').value) || 0;
      s2.showPromos = document.getElementById('rc-show-promos').checked;

      // Re-read watch list from DOM
      s2.watchList = [];
      var items = wlContainer.querySelectorAll('.rc-watch-item');
      for (var ii = 0; ii < items.length; ii++) {
        var el = items[ii];
        s2.watchList.push({
          name: el.querySelector('.wl-name').value,
          prefix: el.querySelector('.wl-prefix').value,
          product: el.querySelector('.wl-product').value,
          price: parseFloat(el.querySelector('.wl-price').value) || 0,
          currency: el.querySelector('.wl-currency').value || 'USD',
          enabled: el.querySelector('.wl-enabled').checked,
          reservations: el.querySelector('.wl-reservations') ? el.querySelector('.wl-reservations').value : '',
          guestAgeString: el.querySelector('.wl-age') ? el.querySelector('.wl-age').value : 'adult',
        });
      }
      saveSettings(s2);
      settings = s2;
      appendHTML('<div style="color:green;font-weight:bold;margin:4px 0;">Settings saved.</div>');
    };
  }

  function renderWatchItem(container, item, idx) {
    var div = document.createElement('div');
    div.className = 'rc-watch-item';
    div.style.cssText =
      'border:1px solid #ddd;padding:6px;margin-top:4px;border-radius:4px;' +
      'font-size:12px;';

    div.innerHTML =
      '<div style="display:inline-block;vertical-align:top;width:48%;">' +
        '<input class="wl-name" placeholder="Name" value="' + escapeAttr(item.name || '') + '" style="width:100%;margin-bottom:2px;">' +
        '<input class="wl-prefix" placeholder="Prefix" value="' + escapeAttr(item.prefix || '') + '" style="width:48%;margin-right:2px;">' +
        '<input class="wl-product" placeholder="Product ID" value="' + escapeAttr(item.product || '') + '" style="width:48%;">' +
      '</div>' +
      '<div style="display:inline-block;vertical-align:top;width:48%;text-align:right;">' +
        '<input class="wl-price" type="number" placeholder="Price" value="' + (item.price || 0) + '" step="0.01" style="width:60px;">' +
        '<input class="wl-currency" placeholder="Cur" value="' + escapeAttr(item.currency || 'USD') + '" style="width:50px;margin-left:2px;">' +
        '<input class="wl-age" placeholder="Age" value="' + escapeAttr(item.guestAgeString || 'adult') + '" style="width:50px;margin-left:2px;">' +
        '<label title="Enabled" style="margin-left:4px;"><input class="wl-enabled" type="checkbox" ' + (item.enabled !== false ? 'checked' : '') + '></label>' +
        '<button class="wl-remove" data-idx="' + idx + '" style="background:#c00;color:#fff;border:none;border-radius:3px;padding:2px 6px;cursor:pointer;margin-left:4px;">x</button>' +
      '</div>' +
      '<div style="margin-top:2px;"><input class="wl-reservations" placeholder="Reservation IDs (comma sep, blank=all)" value="' + escapeAttr(item.reservations || '') + '" style="width:100%;"></div>';

    div.querySelector('.wl-remove').onclick = function () { div.remove(); };
    container.appendChild(div);
  }

  function escapeAttr(str) {
    return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /* ============================================================
     Main price check flow
     ============================================================ */
  async function runPriceCheck() {
    try {
      clearPanel();
      foundKeys = {};
      renderSettings();

      var auth = await extractAuth();
      if (!auth) {
        var debugInfo = '<div style="color:#c00;font-weight:bold;">Not authenticated. Please log in to the website first, then reload and try again.</div>';
        debugInfo += '<details style="font-size:11px;color:#888;"><summary>Debug info</summary>';
        debugInfo += '<div>User-Agent: ' + escapeAttr(navigator.userAgent) + '</div>';
        debugInfo += '<div>Hostname: ' + escapeAttr(location.hostname) + '</div>';
        debugInfo += '<div>localStorage keys: ' + localStorage.length + '</div>';
        debugInfo += '<div>sessionStorage keys: ' + sessionStorage.length + '</div>';
        debugInfo += '<div>cookies: ' + escapeAttr(document.cookie.substring(0, 200)) + '</div>';
        if (indexedDB && indexedDB.databases) {
          try {
            var dbs = await indexedDB.databases();
            debugInfo += '<div>IndexedDB databases: ' + dbs.map(function(d) { return d.name; }).join(', ') + '</div>';
          } catch(_) {}
        }
        debugInfo += '</details>';
        appendHTML(debugInfo);
        return;
      }

      appendHTML('<div style="font-weight:bold;font-size:15px;margin-bottom:8px;">Price Check Report &mdash; ' + new Date().toLocaleString() + '</div>');

      var isRoyal = location.hostname.indexOf('royal') !== -1;
      var cruiseLineName = isRoyal ? 'royalcaribbean' : 'celebritycruises';
      var friendlyCruiseLine = isRoyal ? 'Royal Caribbean' : 'Celebrity Cruises';
      var brandCode = isRoyal ? 'R' : 'C';
      appendHTML('<div>Cruise Line: ' + friendlyCruiseLine + '</div>');

      await loadShipDictionary();
      appendHTML('<div>Loading profile...</div>');

      var profile = await getProfile(auth.accountId, auth.accessToken);
      var loyalty = profile.loyaltyInformation;
      if (loyalty) {
        var cAndA = loyalty.crownAndAnchorId;
        var cAndALevel = loyalty.crownAndAnchorSocietyLoyaltyTier;
        var cAndAPoints = loyalty.crownAndAnchorSocietyLoyaltyIndividualPoints;
        var cAndAShared = loyalty.crownAndAnchorSocietyLoyaltyRelationshipPoints || 0;
        if (cAndA) {
          appendHTML('<div style="margin:4px 0;">C&A: ' + cAndA + ' ' + cAndALevel + ' &mdash; ' + cAndAShared + ' Shared Points (' + cAndAPoints + ' Individual)</div>');
        }
        var captainsId = loyalty.captainsClubId;
        if (captainsId) {
          appendHTML('<div style="margin:4px 0;">Captain\'s Club: ' + captainsId + ' ' + loyalty.captainsClubLoyaltyTier + ' &mdash; ' + (loyalty.captainsClubLoyaltyRelationshipPoints || 0) + ' Shared, ' + (loyalty.captainsClubLoyaltyIndividualPoints || 0) + ' Individual</div>');
        }
      }

     var contact = profile.contactInformation;
       var email = (contact && contact.email) ? contact.email : auth.accountId;
       appendHTML('<div>Account: ' + email + '</div>');
       var state = null;
       if (contact && contact.address) {
        var addr = contact.address;
        if (addr.residencyCountryCode === 'USA' || addr.residencyCountryCode === 'CAN') {
          state = addr.state;
        }
      }

      var loyaltyNumber = isRoyal ? (loyalty ? loyalty.crownAndAnchorId : null) : (loyalty ? loyalty.captainsClubId : null);
      var dp340 = (loyalty ? loyalty.crownAndAnchorSocietyLoyaltyRelationshipPoints || 0 : 0) >= 340;

      var discountFlags = {
        loyaltyNumber: loyaltyNumber,
        state: state,
        senior: false,
        military: false,
        police: false,
        dp340: dp340,
      };

      appendHTML('<hr style="margin:8px 0;">');
      appendHTML('<div>Loading bookings...</div>');
      var bookings = await getVoyages(auth.accountId, auth.accessToken, brandCode);
      if (!bookings.length) {
        appendHTML('<div>No bookings found.</div>');
        return;
      }

      for (var bi = 0; bi < bookings.length; bi++) {
        await processBooking(bookings[bi], auth, discountFlags, cruiseLineName, isRoyal);
      }
    } catch (err) {
      appendHTML('<div style="color:#c00;font-weight:bold;">Error: ' + escapeAttr(err.message) + '</div>');
      appendHTML('<details style="font-size:11px;color:#888;"><summary>Stack trace</summary><pre style="white-space:pre-wrap;word-wrap:break-word;">' + escapeAttr(err.stack || 'no stack') + '</pre></details>');
      console.error(err);
    }
  }

  async function processBooking(booking, auth, discountFlags, cruiseLineName, isRoyal) {
    var reservationId = booking.bookingId;
    var passengerId = booking.passengerId;
    var sailDate = booking.sailDate;
    var numberOfNights = booking.numberOfNights;
    var shipCode = booking.shipCode;
    var guests = booking.passengersInStateroom || [];
    var packageCode = booking.packageCode;
    var bookingCurrency = booking.bookingCurrency || 'USD';
    var bookingOfficeCountryCode = booking.bookingOfficeCountryCode || 'USA';
    var stateroomNumber = booking.stateroomNumber;
    var amendToken = booking.amendToken;

    var typeMap = { I: 'INTERIOR', O: 'OUTSIDE', B: 'BALCONY', D: 'DELUXE', C: 'CONCIERGE' };
    var stateroomTypeName = typeMap[booking.stateroomType] || 'NONE';

    var shipName = shipDictionary[shipCode] || 'Unknown Ship';
    var sailDateDisplay = formatDate(sailDate);

    appendHTML('<hr style="margin:12px 0 4px;">');
    appendHTML('<div style="font-weight:bold;font-size:14px;">Reservation #' + reservationId + '</div>');
    appendHTML('<div>' + sailDateDisplay + ' ' + shipName + ' Room ' + stateroomNumber + '</div>');

    var passengerNames = [];
    var haveASenior = discountFlags.senior;
    var numberOfAdults = 0, numberOfChildren = 0;
    for (var gi = 0; gi < guests.length; gi++) {
      var guest = guests[gi];
      var fn = capitalizeFirst(guest.firstName || '');
      passengerNames.push(fn);
      if (guest.birthdate && aboveAgeOnSailDate(guest.birthdate, sailDate, 55)) haveASenior = true;
      if (guest.birthdate && aboveAgeOnSailDate(guest.birthdate, sailDate, 12)) numberOfAdults++;
      else numberOfChildren++;
      if (guest.onlineCheckinStatus === 'COMPLETED' && guest.arrivalTime) {
        var bh = guest.arrivalTime.substring(9, 11);
        var bm = guest.arrivalTime.substring(11, 13);
        appendHTML('<div style="color:#0066cc;">' + fn + ' Boarding Time ' + bh + ':' + bm + '</div>');
      }
    }
    appendHTML('<div>In this cabin: ' + passengerNames.join(', ') + '</div>');

    if (!discountFlags.senior && haveASenior) discountFlags.senior = true;

    var checkinInfo = await getCheckinInfo(shipCode, sailDate);
    if (checkinInfo) {
      if (checkinInfo.isCheckinAvailable) {
        appendHTML('<div style="color:#c00;font-weight:bold;">Check In Available and Not Completed</div>');
      } else if (checkinInfo.checkWindowOpenStartDateTime) {
        appendHTML('<div>Check In opens: ' + formatDateTime(checkinInfo.checkWindowOpenStartDateTime) + '</div>');
      }
    }

    // Dining & prices
    if (amendToken) {
      var diningResult = await getDiningAndPrices(amendToken, isRoyal, bookingOfficeCountryCode);
      if (diningResult.diningSelection) {
        for (var di = 0; di < diningResult.diningSelection.length; di++) {
          var sel = diningResult.diningSelection[di];
          if (sel.sittingTime === 'MY TIME') {
            appendHTML('<div>Dining: My Time Open Sitting</div>');
          } else {
            var ds = 'Dining: ' + (sel.sittingType || '') + ' ' + (sel.sittingTime || '');
            if (sel.tableSize && sel.tableSize !== '00') ds += ' Table Size: ' + sel.tableSize;
            appendHTML('<div>' + ds + '</div>');
          }
        }
      }

      if (diningResult.prices) {
        var grossTotals = null;
        var paymentParts = [];
        for (var pi = 0; pi < diningResult.prices.length; pi++) {
          var p = diningResult.prices[pi];
          var code = p.priceTypeCode || '';
          var amt = p.amount;
          if (amt === 0) continue;
          if (code === 'GROSS_TOTALS') grossTotals = amt;
          if (code === 'GRATUITIES') paymentParts.push('Including: ' + amt + ' Gratuities');
          if (code === 'TRIP_INSURANCE') paymentParts.push('Including: ' + amt + ' Insurance');
          if (code === 'BALANCE_DUE') paymentParts.push('You Still Owe: ' + amt);
          if (code.indexOf('ALL_INC') !== -1 || code.indexOf('INCLUDED') !== -1) paymentParts.push('Including: ' + amt + ' All Included');
        }
        if (grossTotals !== null) {
          appendHTML('<div style="font-weight:bold;">Cruise Fare &mdash; Total ' + grossTotals + ' ' + bookingCurrency + ' ' + paymentParts.join(' | ') + '</div>');
        }
      }
    }

    var finalPaymentDate = getFinalPaymentDate(numberOfNights, sailDate);
    var pastFinalPayment = new Date() > finalPaymentDate;

    if (booking.balanceDue === true) {
      appendHTML('<div style="color:#cc0;">Remaining balance: ' + booking.balanceDueAmount + ' due ' + formatDate(finalPaymentDate.toISOString().slice(0, 10)) + '</div>');
    }

    // OBC
    var obcData = await getOBC(reservationId, passengerId, shipCode, sailDate, bookingCurrency);
    if (obcData && obcData.amount && obcData.amount > 0) {
      appendHTML('<div>Onboard Credit: ' + obcData.amount + ' ' + (obcData.currencyIso || bookingCurrency) + '</div>');
    }

    // Promotions
    if (settings.showPromos) {
      var promos = await getPromotions(shipCode, sailDate, bookingCurrency);
      if (promos.length) {
        appendHTML('<div style="font-weight:bold;margin-top:4px;">Promotions:</div>');
        for (var promi = 0; promi < Math.min(promos.length, 10); promi++) {
          var promo = promos[promi];
          var pid = promo.id || '';
          var pstart = (promo.startDate || '').substring(0, 10);
          var pend = (promo.endDate || '').substring(0, 10);
          appendHTML('<div style="color:#cc0;font-size:12px;">[PROMO] ' + pid + ' (Valid ' + pstart + ' to ' + pend + ')</div>');
        }
      }
    }

    // Orders
    await processOrders(reservationId, passengerId, shipCode, sailDate, numberOfNights, bookingCurrency, cruiseLineName, guests);

    // Watch list
    if (settings.watchList && settings.watchList.length) {
      for (var wi = 0; wi < guests.length; wi++) {
        var g = guests[wi];
        var gfn = capitalizeFirst(g.firstName || '');
        var gPid = g.passengerId || passengerId;
        var gRoom = g.stateroomNumber || stateroomNumber;
        await processWatchList(reservationId, shipCode, sailDate, gPid, gfn, gRoom, cruiseLineName);
      }
    }

    appendHTML('<div style="height:8px;"></div>');
  }

  function capitalizeFirst(str) {
    if (!str) return '';
    return str.charAt(0).toUpperCase() + str.slice(1).toLowerCase();
  }

  function guessSubtype(stateroomType, isRoyal) {
    if (isRoyal && stateroomType === 'I') return 'ZI';
    if (!isRoyal && stateroomType === 'B') return 'XC';
    return null;
  }

  function guessCategory(stateroomType, isRoyal) {
    return null;
  }

  async function checkCruisePrice(
    isRoyal, cruiseLineName, packageCode, sailDate, countryCode,
    currency, shipCode, stateroomTypeName, stateroomSubtype, stateroomCategoryCode,
    numAdults, numChildren, discountFlags, reservationId,
    pastFinalPayment, finalPaymentDate
  ) {
    var avail = await checkRoomAvailability(
      isRoyal, countryCode, packageCode, sailDate, currency,
      stateroomSubtype, stateroomCategoryCode, numAdults, numChildren
    );

    if (!avail.available) {
      appendHTML('<div style="color:#cc0;">' + stateroomTypeName + ' ' + (stateroomCategoryCode || '?') + ' &mdash; Not available for sale</div>');
      return;
    }

    var result = await getRoomPriceViaCheckoutAPI(
      isRoyal, countryCode, packageCode, sailDate, currency,
      stateroomTypeName, stateroomSubtype, stateroomCategoryCode,
      discountFlags.loyaltyNumber, discountFlags.state,
      false, discountFlags.military, discountFlags.police, discountFlags.senior,
      null, numAdults, numChildren
    );

    if (!result || !result.rooms || !result.rooms[0]) {
      appendHTML('<div style="color:#cc0;">Could not retrieve cruise price from API</div>');
      return;
    }

    var room = result.rooms[0];
    var baseFare = room.baseFare;
    if (!baseFare) {
      appendHTML('<div style="color:#cc0;">No fare data returned</div>');
      return;
    }

    var price = baseFare.pricing ? baseFare.pricing.amount : 0;
    var obc = baseFare.pricing && baseFare.pricing.invoice ? baseFare.pricing.invoice.onboardCredits || 0 : 0;

    appendHTML('<div style="font-weight:bold;margin-top:4px;">Current Cruise Price:</div>');
    appendHTML('<div>' + stateroomTypeName + ' ' + (stateroomCategoryCode || '?') + ': <strong>' + price + ' ' + currency + '</strong>' + (obc > 0 ? ' (OBC: ' + obc + ')' : '') + '</div>');
  }

  async function processOrders(reservationId, passengerId, ship, sailDate, numberOfNights, currency, cruiseLineName, guests) {
    var ordersData = await getOrders(reservationId, passengerId, ship, sailDate, currency);
    if (!ordersData) return;

    // Build name->passengerId lookup from booking guests
    var passengerLookup = {};
    if (guests) {
      for (var li = 0; li < guests.length; li++) {
        var gname = (guests[li].firstName || '').toLowerCase() + '|' + (guests[li].lastName || '').toLowerCase();
        passengerLookup[gname] = guests[li].passengerId;
      }
    }

    var myOrders = ordersData.myOrders || [];
    var otherOrders = ordersData.ordersOthersHaveBookedForMe || [];
    var allOrders = myOrders.concat(otherOrders);

    if (!allOrders.length) return;

    appendHTML('<div style="font-weight:bold;margin-top:8px;">Add-on Orders:</div>');

    for (var oi = 0; oi < allOrders.length; oi++) {
      var order = allOrders[oi];
      if (!order.orderTotals || order.orderTotals.total <= 0) continue;

      var orderCode = order.orderCode;
      var orderDate = formatDate(order.orderDate);
      var owner = order.owner;

      try {
        var detailParams = new URLSearchParams({
          passengerId: passengerId,
          reservationId: reservationId,
          sailingId: ship + sailDate,
          currencyIso: currency,
          includeMedia: 'false',
        });
        var detailData = await apiCall(
          API_BASE + '/en/royal/web/commerce-api/calendar/v1/' + ship + '/orderHistory/' + orderCode + '?' + detailParams
        );

        if (!detailData || !detailData.payload) continue;

        var detailItems = detailData.payload.orderHistoryDetailItems || [];
        for (var di = 0; di < detailItems.length; di++) {
          var item = detailItems[di];
          var quantity = item.priceDetails ? item.priceDetails.quantity || 0 : 0;
          var title = item.productSummary ? item.productSummary.title || 'Unknown' : 'Unknown';
          var product = null;
          if (item.productSummary && item.productSummary.baseOptions && item.productSummary.baseOptions[0] &&
              item.productSummary.baseOptions[0].selected && item.productSummary.baseOptions[0].selected.code) {
            product = item.productSummary.baseOptions[0].selected.code;
          } else if (item.productSummary) {
            product = item.productSummary.defaultVariantId;
          }
          var prefix = item.productSummary ? item.productSummary.productTypeCategory ? item.productSummary.productTypeCategory.id : null : null;
          var salesUnit = item.productSummary ? item.productSummary.salesUnit : null;

          if (!product || !prefix) continue;

          var itemGuests = item.guests || [];
          for (var igi = 0; igi < itemGuests.length; igi++) {
            var guest = itemGuests[igi];
            if (guest.orderStatus === 'CANCELLED') continue;
            var paidPrice = guest.priceDetails ? guest.priceDetails.subtotal || 0 : 0;
            if (paidPrice === 0) continue;

            var fn = capitalizeFirst(guest.firstName || '');
            var guestType = (guest.guestType || 'adult').toLowerCase();
            var room = guest.stateroomNumber;
            var cur = guest.priceDetails ? guest.priceDetails.currency || currency : currency;

            var gPid = guest.id || guest.passengerId;
            if (!gPid) {
              var lname = (guest.lastName || '').toLowerCase();
              var fkey = fn.toLowerCase() + '|' + lname;
              gPid = passengerLookup[fkey] || passengerId;
            }

            var gReservationId = guest.reservationId || reservationId;

            var key = gPid + gReservationId + prefix + product;
            if (foundKeys[key]) continue;
            foundKeys[key] = true;

            if (salesUnit === 'PER_NIGHT' || salesUnit === 'PER_DAY') {
              paidPrice = Math.round((paidPrice / numberOfNights) * 100) / 100;
            }
            if (quantity > 0) {
              paidPrice = Math.round((paidPrice / quantity) * 100) / 100;
            }

            var prodData = await getProductPrice(ship, prefix, product, gReservationId, sailDate, gPid, cur);

            if (!prodData) {
              appendHTML('<div style="color:#cc0;font-size:12px;">' + fn + ': ' + title + ' &mdash; unavailable</div>');
              continue;
            }

            var newPricePayload = prodData.startingFromPrice;
            if (!newPricePayload) {
              appendHTML('<div style="color:#cc0;font-size:12px;">' + fn + ' (' + room + '): Best price for ' + title + ' at ' + paidPrice + ' ' + cur + ' (No Longer for Sale)</div>');
              continue;
            }

            var perDay = (salesUnit === 'PER_NIGHT' || salesUnit === 'PER_DAY');
            var unitLabel = perDay ? ' per night' : '';

            var currentPrice = newPricePayload[guestType + 'PromotionalPrice'];
            if (!currentPrice) currentPrice = newPricePayload[guestType + 'ShipboardPrice'];
            if (!currentPrice) currentPrice = 0;

            if (currentPrice < paidPrice) {
              var saving = Math.round((paidPrice - currentPrice) * 100) / 100;
              var msg = fn + ': <strong style="color:#c00;">Rebook!</strong> ' + title + unitLabel + ' is lower: <strong>' + currentPrice + ' ' + cur + '</strong> vs paid ' + paidPrice + ' ' + cur;
              if (saving > 0) msg += ' (Save ' + saving + ' ' + cur + ')';
              if (settings.minimumSavingAlert && saving < settings.minimumSavingAlert) {
                msg += ' [below min alert threshold]';
              }
              appendHTML('<div style="font-size:12px;">' + msg + '</div>');
            } else {
              var msg2 = fn + ' (' + room + '): <strong style="color:#0a0;">Best price</strong> for ' + title + ' at ' + paidPrice + ' ' + cur;
              if (currentPrice > paidPrice) msg2 += ' (now ' + currentPrice + ' ' + cur + ')';
              appendHTML('<div style="font-size:12px;color:#0a0;">' + msg2 + '</div>');
            }
          }
        }
      } catch (_) { /* skip */ }
    }
  }

  async function processWatchList(reservationId, shipCode, sailDate, passengerId, passengerName, room, cruiseLineName) {
    if (!settings.watchList || !settings.watchList.length) return;

    appendHTML('<div style="font-weight:bold;margin-top:6px;">Watch List:</div>');

    for (var wi = 0; wi < settings.watchList.length; wi++) {
      var item = settings.watchList[wi];
      if (!item.enabled) continue;
      if (!item.product || !item.prefix || !item.price) continue;

      if (item.reservations) {
        var resList = item.reservations.split(',');
        var match = false;
        for (var ri = 0; ri < resList.length; ri++) {
          if (resList[ri].trim() === String(reservationId)) { match = true; break; }
        }
        if (!match) continue;
      }

      var currency = item.currency || 'USD';
      var guestType = (item.guestAgeString || 'adult').toLowerCase();

      var prodData = await getProductPrice(
        shipCode, item.prefix, item.product,
        reservationId, sailDate, passengerId, currency
      );

      if (!prodData) {
        appendHTML('<div style="color:#cc0;font-size:12px;">[WATCH] ' + passengerName + ' (' + room + '): ' + (item.name || item.product) + ' &mdash; unavailable</div>');
        continue;
      }

      var newPricePayload = prodData.startingFromPrice;
      if (!newPricePayload) {
        appendHTML('<div style="color:#cc0;font-size:12px;">[WATCH] ' + passengerName + ' (' + room + '): Not available or already booked</div>');
        continue;
      }

      var currentPrice = newPricePayload[guestType + 'PromotionalPrice'];
      if (!currentPrice) currentPrice = newPricePayload[guestType + 'ShipboardPrice'];
      if (!currentPrice) currentPrice = 0;

      var title = prodData.title || item.name || item.product;
      var watchPrice = item.price;

      if (currentPrice < watchPrice) {
        var saving = Math.round((watchPrice - currentPrice) * 100) / 100;
        var bookUrl = 'https://www.' + cruiseLineName + '.com/account/cruise-planner/category/' + item.prefix + '/product/' + item.product + '?bookingId=' + reservationId + '&shipCode=' + shipCode + '&sailDate=' + sailDate;
        appendHTML('<div style="font-size:12px;color:#c00;font-weight:bold;">[WATCH] ' + passengerName + ': Book! ' + title + ' price is lower: ' + currentPrice + ' ' + currency + ' vs watch ' + watchPrice + ' ' + currency + ' (Save ' + saving + ')</div>');
        appendHTML('<div style="font-size:11px;"><a href="' + bookUrl + '" target="_blank" style="color:#0066cc;">Book now</a></div>');
      } else {
        var msg = '[WATCH] ' + passengerName + ': <strong style="color:#0a0;">Best price</strong> ' + title + ' at watch ' + watchPrice + ' ' + currency;
        if (currentPrice > watchPrice) msg += ' (now ' + currentPrice + ' ' + currency + ')';
        appendHTML('<div style="font-size:12px;color:#0a0;">' + msg + '</div>');
      }
    }
  }

  /* ============================================================
     Init
     ============================================================ */
  function tryCreateUI() {
    if (document.body) { createUI(); }
    else { setTimeout(tryCreateUI, 200); }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tryCreateUI);
  } else {
    tryCreateUI();
  }
  setTimeout(function() {
    if (!document.getElementById('rc-price-check-btn')) {
      tryCreateUI();
    }
  }, 3000);
})();
