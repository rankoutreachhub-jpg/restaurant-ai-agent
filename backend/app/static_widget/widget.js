/*!
 * Restaurant AI Agent — embeddable customer widget (Stage 4 Phase E).
 *
 * Embed with:
 *   <script src=".../static/widget/widget.js" data-widget-key="wgt_..." async></script>
 *
 * Consumes exactly two existing public endpoints — GET /widget/{key}/config
 * and POST /widget/{key}/chat — and nothing else. No framework, no build
 * step, no external requests beyond those two and the one script load
 * itself.
 *
 * Isolation model: each <script data-widget-key> tag is its own,
 * independent execution of this whole file (that's how the browser runs
 * classic scripts — one full run per tag, even for the same cached URL),
 * so multiple different widget_keys on one page just work with no extra
 * code. The ONE exception, where instances must coordinate, is preventing
 * an accidental duplicate embed of the SAME widget_key twice — that needs
 * one shared, deliberately namespaced global (window.__raiWidgetInstances)
 * to detect. There is no other global anywhere in this file.
 *
 * document.currentScript is read as the very first line of this IIFE's
 * top-level execution. That is what makes it reliable even with the
 * `async` attribute: per spec, a classic script's currentScript is valid
 * for its entire initial (synchronous) run regardless of async/defer, and
 * only becomes null once that run finishes (e.g. inside a later
 * fetch().then callback) — so it must never be read lazily.
 */
(function () {
  "use strict";

  var scriptEl = document.currentScript;

  if (!scriptEl) {
    // Fallback for the rare case a host page's own code strips
    // currentScript before we can read it (e.g. a dynamically
    // re-inserted tag). Only ever consider a tag not already claimed by
    // a previous run, and claim it immediately so a second fallback
    // run elsewhere on the page can't grab the same tag twice.
    var candidates = document.querySelectorAll(
      "script[data-widget-key]:not([data-rai-initialized])"
    );
    scriptEl = candidates.length ? candidates[0] : null;
  }

  if (!scriptEl) {
    return;
  }
  scriptEl.setAttribute("data-rai-initialized", "true");

  var widgetKey = scriptEl.getAttribute("data-widget-key");
  if (!widgetKey) {
    return;
  }

  // The one deliberate global in this file: a page-wide registry so a
  // second <script> tag embedding the SAME widget_key (an accidental
  // double-embed) is a silent no-op rather than two competing instances
  // racing over the same localStorage token. It also hands out a small
  // incrementing index (read-then-increment, safe because each script
  // tag's top-level code runs to completion before any other script's
  // does — there's no interleaving to race) so two DIFFERENT widget_keys
  // embedded on the same page don't render on top of one another at the
  // exact same fixed viewport position.
  window.__raiWidgetInstances = window.__raiWidgetInstances || {};
  if (window.__raiWidgetInstances[widgetKey]) {
    return;
  }
  window.__raiWidgetInstances[widgetKey] = true;
  var instanceIndex = window.__raiWidgetInstances.__count || 0;
  window.__raiWidgetInstances.__count = instanceIndex + 1;

  var apiBase = scriptEl.getAttribute("data-api-base");
  if (!apiBase) {
    try {
      apiBase = new URL(scriptEl.src, document.baseURI).origin;
    } catch (e) {
      return;
    }
  }

  var DEFAULT_ACCENT = "#2563eb";

  // Platform-wide Privacy Policy link shown in every widget instance,
  // regardless of restaurant (there is no per-restaurant policy URL in
  // this version — see frontend/privacy-policy.html), hosted at its
  // actual public location.
  var PRIVACY_POLICY_URL = "https://restaurant-ai-agent-eight.vercel.app/privacy-policy.html";

  var LAUNCHER_ICON_SVG =
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
    '<path fill="currentColor" d="M12 3C6.5 3 2 6.6 2 11c0 2.4 1.3 4.6 3.4 6.1-.1.9-.5 2.2-1.4 3.4 1.7-.1 3.3-.7 4.5-1.5.8.2 1.6.3 2.5.3 5.5 0 10-3.6 10-8s-4.5-8-10-8z"/>' +
    "</svg>";

  var CSS_TEXT =
    ":host{all:initial;}" +
    ".raiw-root{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1a1a1a;font-size:14px;line-height:1.4;}" +
    ".raiw-launcher{position:fixed;bottom:20px;right:calc(20px + var(--raiw-offset,0px));width:56px;height:56px;border-radius:50%;background:var(--raiw-accent," +
    DEFAULT_ACCENT +
    ");color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 4px 12px rgba(0,0,0,0.25);z-index:2147483000;border:none;padding:0;}" +
    ".raiw-launcher svg{width:28px;height:28px;}" +
    ".raiw-launcher:focus-visible{outline:2px solid #fff;outline-offset:2px;}" +
    ".raiw-panel{position:fixed;bottom:88px;right:calc(20px + var(--raiw-offset,0px));width:360px;max-width:calc(100vw - 40px);height:520px;max-height:calc(100vh - 120px);background:#fff;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,0.3);display:flex;flex-direction:column;overflow:hidden;z-index:2147483000;}" +
    ".raiw-panel[hidden]{display:none;}" +
    ".raiw-header{background:var(--raiw-accent," +
    DEFAULT_ACCENT +
    ");color:#fff;padding:14px 16px;display:flex;align-items:center;gap:10px;flex-shrink:0;}" +
    ".raiw-logo{width:28px;height:28px;border-radius:50%;object-fit:cover;flex-shrink:0;}" +
    ".raiw-title{font-weight:600;font-size:15px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}" +
    ".raiw-close{background:transparent;border:none;color:#fff;font-size:20px;cursor:pointer;padding:2px 6px;line-height:1;flex-shrink:0;}" +
    ".raiw-close:focus-visible,.raiw-chip:focus-visible,.raiw-send:focus-visible,.raiw-input:focus-visible{outline:2px solid " +
    "#2563eb;outline-offset:1px;}" +
    ".raiw-messages{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:8px;}" +
    ".raiw-msg{max-width:80%;padding:8px 12px;border-radius:12px;font-size:14px;white-space:pre-wrap;word-break:break-word;}" +
    ".raiw-msg-user{align-self:flex-end;background:var(--raiw-accent," +
    DEFAULT_ACCENT +
    ");color:#fff;}" +
    ".raiw-msg-assistant{align-self:flex-start;background:#f0f0f0;color:#1a1a1a;}" +
    ".raiw-msg-error{align-self:center;background:#fdecea;color:#b3261e;font-size:13px;text-align:center;}" +
    ".raiw-quickreplies{padding:0 14px 8px;flex-shrink:0;}" +
    ".raiw-chip{background:#fff;border:1px solid var(--raiw-accent," +
    DEFAULT_ACCENT +
    ");color:var(--raiw-accent," +
    DEFAULT_ACCENT +
    ");border-radius:16px;padding:6px 12px;font-size:13px;cursor:pointer;}" +
    ".raiw-inputrow{border-top:1px solid #eee;padding:10px 14px;flex-shrink:0;}" +
    ".raiw-input-label{display:block;font-size:12px;color:#666;margin-bottom:4px;}" +
    ".raiw-input-wrap{display:flex;gap:8px;align-items:flex-end;}" +
    ".raiw-input{flex:1;resize:none;border:1px solid #ccc;border-radius:8px;padding:8px 10px;font-size:14px;font-family:inherit;max-height:90px;}" +
    ".raiw-send{background:var(--raiw-accent," +
    DEFAULT_ACCENT +
    ");color:#fff;border:none;border-radius:8px;padding:8px 14px;font-size:14px;cursor:pointer;flex-shrink:0;}" +
    ".raiw-send:disabled,.raiw-input:disabled{opacity:0.6;cursor:not-allowed;}" +
    ".raiw-footer{flex-shrink:0;padding:6px 14px 10px;text-align:center;}" +
    ".raiw-footer-link{font-size:11px;color:#888;text-decoration:none;}" +
    ".raiw-footer-link:hover,.raiw-footer-link:focus-visible{text-decoration:underline;}" +
    "@media (max-width:480px){.raiw-panel{width:calc(100vw - 24px);height:calc(100vh - 100px);right:12px;bottom:80px;}.raiw-launcher{right:16px;bottom:16px;}}";

  function isValidHexColor(v) {
    return typeof v === "string" && /^#[0-9a-fA-F]{6}$/.test(v);
  }

  function isValidHttpUrl(v) {
    if (typeof v !== "string" || !v) {
      return false;
    }
    try {
      var u = new URL(v);
      return u.protocol === "http:" || u.protocol === "https:";
    } catch (e) {
      return false;
    }
  }

  function tokenStorageKey(key) {
    return "raiw:" + key + ":token";
  }

  function mountWhenReady(fn) {
    if (document.body) {
      fn();
    } else {
      document.addEventListener("DOMContentLoaded", fn);
    }
  }

  function fetchJson(url) {
    return fetch(url, { method: "GET" }).then(function (response) {
      if (!response.ok) {
        throw new Error("request failed");
      }
      return response.json();
    });
  }

  function init(widgetKey, apiBase, instanceIndex) {
    var configUrl = apiBase + "/widget/" + encodeURIComponent(widgetKey) + "/config";
    var chatUrl = apiBase + "/widget/" + encodeURIComponent(widgetKey) + "/chat";

    fetchJson(configUrl)
      .then(function (config) {
        mountWhenReady(function () {
          buildWidget(widgetKey, chatUrl, config, instanceIndex);
        });
      })
      .catch(function () {
        // Unknown/inactive widget_key, a network failure, or a
        // cross-origin request this restaurant hasn't allowed yet
        // (Phase D) — indistinguishable to fetch() by design, and none
        // of them have anything useful to show, so: no launcher at all,
        // rather than one that renders and then immediately errors.
      });
  }

  function buildWidget(widgetKey, chatUrl, config, instanceIndex) {
    var instanceId = "raiw-" + Math.random().toString(36).slice(2, 10);
    var memoryToken = null;

    function getStoredToken() {
      try {
        return window.localStorage.getItem(tokenStorageKey(widgetKey));
      } catch (e) {
        return memoryToken;
      }
    }

    function setStoredToken(token) {
      try {
        window.localStorage.setItem(tokenStorageKey(widgetKey), token);
      } catch (e) {
        memoryToken = token;
      }
    }

    var restaurantName =
      typeof config.restaurant_name === "string" && config.restaurant_name
        ? config.restaurant_name
        : "Chat";
    var welcomeMessage = typeof config.welcome_message === "string" ? config.welcome_message : "";
    var accent = isValidHexColor(config.accent_color) ? config.accent_color : DEFAULT_ACCENT;
    var logo = isValidHttpUrl(config.logo_url) ? config.logo_url : null;
    var bookingEnabled = config.booking_enabled === true;

    var container = document.createElement("div");
    container.id = instanceId + "-container";
    container.style.all = "initial";
    container.style.position = "static";
    var shadowRoot = container.attachShadow({ mode: "open" });

    var styleEl = document.createElement("style");
    styleEl.textContent = CSS_TEXT;
    shadowRoot.appendChild(styleEl);

    var rootEl = document.createElement("div");
    rootEl.className = "raiw-root";
    rootEl.style.setProperty("--raiw-accent", accent);
    rootEl.style.setProperty("--raiw-offset", instanceIndex * 76 + "px");
    shadowRoot.appendChild(rootEl);

    var launcherEl = document.createElement("button");
    launcherEl.type = "button";
    launcherEl.className = "raiw-launcher";
    launcherEl.setAttribute("aria-label", "Chat with " + restaurantName);
    launcherEl.setAttribute("aria-expanded", "false");
    launcherEl.innerHTML = LAUNCHER_ICON_SVG; // fixed literal authored above, never dynamic
    rootEl.appendChild(launcherEl);

    var panelEl = document.createElement("div");
    panelEl.className = "raiw-panel";
    panelEl.setAttribute("role", "dialog");
    panelEl.setAttribute("aria-modal", "true");
    panelEl.setAttribute("aria-labelledby", instanceId + "-title");
    panelEl.hidden = true;
    rootEl.appendChild(panelEl);

    var headerEl = document.createElement("div");
    headerEl.className = "raiw-header";
    panelEl.appendChild(headerEl);

    if (logo) {
      var logoEl = document.createElement("img");
      logoEl.className = "raiw-logo";
      logoEl.src = logo;
      logoEl.alt = "";
      headerEl.appendChild(logoEl);
    }

    var titleEl = document.createElement("span");
    titleEl.className = "raiw-title";
    titleEl.id = instanceId + "-title";
    titleEl.textContent = restaurantName;
    headerEl.appendChild(titleEl);

    var closeEl = document.createElement("button");
    closeEl.type = "button";
    closeEl.className = "raiw-close";
    closeEl.setAttribute("aria-label", "Close chat");
    closeEl.textContent = "×";
    headerEl.appendChild(closeEl);

    var messagesEl = document.createElement("div");
    messagesEl.className = "raiw-messages";
    messagesEl.setAttribute("role", "log");
    messagesEl.setAttribute("aria-live", "polite");
    panelEl.appendChild(messagesEl);

    var chipEl = null;
    if (bookingEnabled) {
      var chipRow = document.createElement("div");
      chipRow.className = "raiw-quickreplies";
      chipEl = document.createElement("button");
      chipEl.type = "button";
      chipEl.className = "raiw-chip";
      chipEl.textContent = "📅 Book a table";
      chipRow.appendChild(chipEl);
      panelEl.appendChild(chipRow);
    }

    var inputRow = document.createElement("div");
    inputRow.className = "raiw-inputrow";
    panelEl.appendChild(inputRow);

    var labelEl = document.createElement("label");
    labelEl.className = "raiw-input-label";
    labelEl.setAttribute("for", instanceId + "-input");
    labelEl.textContent = "Message";
    inputRow.appendChild(labelEl);

    var inputWrap = document.createElement("div");
    inputWrap.className = "raiw-input-wrap";
    inputRow.appendChild(inputWrap);

    var inputEl = document.createElement("textarea");
    inputEl.id = instanceId + "-input";
    inputEl.className = "raiw-input";
    inputEl.rows = 1;
    inputWrap.appendChild(inputEl);

    var sendEl = document.createElement("button");
    sendEl.type = "button";
    sendEl.className = "raiw-send";
    sendEl.textContent = "Send";
    inputWrap.appendChild(sendEl);

    // Small, unobtrusive Privacy Policy link — included in the existing
    // focus-trap's own element query (trapFocus below) via tabindex="0",
    // so keyboard Tab-cycling within the panel continues to work
    // correctly without any change to that logic.
    var footerEl = document.createElement("div");
    footerEl.className = "raiw-footer";
    var privacyLinkEl = document.createElement("a");
    privacyLinkEl.className = "raiw-footer-link";
    privacyLinkEl.href = PRIVACY_POLICY_URL;
    privacyLinkEl.target = "_blank";
    privacyLinkEl.rel = "noopener noreferrer";
    privacyLinkEl.tabIndex = 0;
    privacyLinkEl.textContent = "Privacy Policy";
    footerEl.appendChild(privacyLinkEl);
    panelEl.appendChild(footerEl);

    document.body.appendChild(container);

    function addMessage(kind, text) {
      var el = document.createElement("div");
      el.className = "raiw-msg raiw-msg-" + kind;
      el.textContent = text; // never innerHTML — text may be server/AI/customer content
      messagesEl.appendChild(el);
      messagesEl.scrollTop = messagesEl.scrollHeight;
      return el;
    }

    function addTyping() {
      var el = addMessage("assistant", "...");
      el.classList.add("raiw-typing");
      return el;
    }

    function removeTyping(el) {
      if (el && el.parentNode) {
        el.parentNode.removeChild(el);
      }
    }

    if (welcomeMessage) {
      addMessage("assistant", welcomeMessage);
    }

    var isOpen = false;
    var isSending = false;
    var isDisabledPermanently = false;

    function setInputDisabled(disabled) {
      inputEl.disabled = disabled;
      sendEl.disabled = disabled;
      if (chipEl) {
        chipEl.disabled = disabled;
      }
    }

    function openPanel() {
      isOpen = true;
      panelEl.hidden = false;
      launcherEl.setAttribute("aria-expanded", "true");
      inputEl.focus();
    }

    function closePanel() {
      isOpen = false;
      panelEl.hidden = true;
      launcherEl.setAttribute("aria-expanded", "false");
      launcherEl.focus();
    }

    launcherEl.addEventListener("click", function () {
      if (isOpen) {
        closePanel();
      } else {
        openPanel();
      }
    });
    closeEl.addEventListener("click", closePanel);

    function trapFocus(e) {
      var focusable = panelEl.querySelectorAll(
        'button:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      if (!focusable.length) {
        return;
      }
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      var active = shadowRoot.activeElement;
      if (e.shiftKey) {
        if (active === first || active === panelEl) {
          e.preventDefault();
          last.focus();
        }
      } else if (active === last) {
        e.preventDefault();
        first.focus();
      }
    }

    shadowRoot.addEventListener("keydown", function (e) {
      if (!isOpen) {
        return;
      }
      if (e.key === "Escape") {
        closePanel();
        return;
      }
      if (e.key === "Tab") {
        trapFocus(e);
      }
    });

    if (chipEl) {
      chipEl.addEventListener("click", function () {
        inputEl.value = "I'd like to book a table, please.";
        inputEl.focus();
      });
    }

    function handleSend() {
      if (isSending || isDisabledPermanently) {
        return;
      }
      var text = inputEl.value.trim();
      if (!text) {
        return;
      }
      inputEl.value = "";
      isSending = true;
      setInputDisabled(true);
      addMessage("user", text);
      var typingEl = addTyping();

      var headers = { "Content-Type": "application/json" };
      var token = getStoredToken();
      if (token) {
        headers["X-Conversation-Token"] = token;
      }

      fetch(chatUrl, {
        method: "POST",
        headers: headers,
        body: JSON.stringify({ message: text }),
      })
        .then(function (response) {
          removeTyping(typingEl);

          // Always persist whatever token comes back — the server
          // silently starts a fresh conversation (and returns a new,
          // still-valid token) for an unknown/expired/invalid one
          // instead of erroring, so there is no separate "token
          // rejected" case for the client to detect; just always store
          // the latest one.
          var newToken = response.headers.get("X-Conversation-Token");
          if (newToken) {
            setStoredToken(newToken);
          }

          if (response.ok) {
            return response.json().then(function (data) {
              addMessage("assistant", typeof data.reply === "string" ? data.reply : "");
            });
          }
          if (response.status === 429) {
            addMessage(
              "error",
              "You're sending messages a little too fast — please wait a moment and try again."
            );
            return;
          }
          if (response.status === 404) {
            addMessage("error", "This chat is currently unavailable.");
            isDisabledPermanently = true;
            return;
          }
          addMessage("error", "Something went wrong on our end. Please try again.");
        })
        .catch(function () {
          // Covers both a genuine network failure and a cross-origin
          // request rejected at the CORS layer (Phase D) — fetch()
          // gives no way to tell those apart, and none of this ever
          // includes internal backend detail or the message/token.
          removeTyping(typingEl);
          addMessage("error", "Something went wrong. Please check your connection and try again.");
        })
        .then(function () {
          isSending = false;
          setInputDisabled(isDisabledPermanently);
          if (!isDisabledPermanently) {
            inputEl.focus();
          }
        });
    }

    sendEl.addEventListener("click", handleSend);
    inputEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    });
  }

  init(widgetKey, apiBase, instanceIndex);
})();
