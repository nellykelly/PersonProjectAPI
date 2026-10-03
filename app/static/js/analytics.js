// Visitor analytics: PostHog (how often does X happen?) + Microsoft Clarity
// (what did the visitor actually do? -- heatmaps, recordings, rage clicks).
//
// This is the ONLY place analytics is initialised and the ONLY place custom
// events are defined. Page code calls window.siteAnalytics.* and never
// touches posthog/clarity directly. Loaded (deferred) by templates/
// _analytics.html, which renders only when CLARITY_PROJECT_ID and/or
// POSTHOG_API_KEY are set -- with neither set this file is never served and
// window.siteAnalytics doesn't exist, so callers must treat it as optional
// (see `track` in assistant.js). Every public function here swallows its own
// errors: analytics must never break the site.
//
// CONSENT: nothing loads and nothing is recorded until the visitor clicks
// Accept in the banner built below. Decline (or Do-Not-Track) loads nothing.
// Storage blocked = no choice can be saved, so the banner returns each load
// and tracking stays off. The footer's "Cookie settings" button reopens it.
//
// The site is server-rendered (one full page load per navigation), so
// "page_viewed" fires once per load. The history hook below also covers any
// future client-side route change, de-duplicated by path.
//
// PRIVACY: events carry paths (never query strings or fragments), page
// titles, link destinations and coarse categories. Never free text a
// visitor typed, never resume contents, never credentials. The assistant's
// questions are reduced to a category in categorizeQuestion() and the text
// is dropped there. See README, "Analytics" -> "What is intentionally
// excluded".
(function () {
  "use strict";

  var cfg = window.__SITE_ANALYTICS__;
  // Guard against double inclusion / re-execution: init exactly once.
  if (!cfg || window.siteAnalytics) return;

  // ---------- event catalogue (add new event names here) ----------
  var EVENTS = {
    PAGE_VIEWED: "page_viewed",
    EXTERNAL_LINK_CLICKED: "external_link_clicked",
    PROJECT_VIEWED: "project_viewed",
    RESUME_VIEWED: "resume_viewed",
    RESUME_DOWNLOADED: "resume_downloaded",
    SCROLL_DEPTH: "scroll_depth_reached",
    ASSISTANT_OPENED: "assistant_opened",
    ASSISTANT_QUESTION_SUBMITTED: "assistant_question_submitted",
    ASSISTANT_RESPONSE_COMPLETED: "assistant_response_completed",
    ASSISTANT_ERROR: "assistant_error",
  };

  var DNT = navigator.doNotTrack === "1" || window.doNotTrack === "1";

  // ---------- consent state ----------
  // "granted" | "denied" | null (no choice yet). Stored in localStorage.
  var CONSENT_KEY = "siteAnalyticsConsent";
  var started = false; // true once the visitor has accepted this load

  function readConsent() {
    try {
      return window.localStorage.getItem(CONSENT_KEY);
    } catch (e) {
      return null;
    }
  }

  function writeConsent(value) {
    try {
      window.localStorage.setItem(CONSENT_KEY, value);
    } catch (e) {
      /* blocked: the banner comes back next load */
    }
  }

  // ---------- helpers ----------

  function safely(fn) {
    return function () {
      try {
        return fn.apply(this, arguments);
      } catch (e) {
        /* analytics must never throw into the page */
      }
    };
  }

  // Path only: no query string (can hold tokens/emails) and no fragment.
  function cleanPath(url) {
    try {
      var u = new URL(url, location.href);
      return u.pathname;
    } catch (e) {
      return "";
    }
  }

  // Origin + path of a URL, drop query/fragment; "" for an unparsable one.
  function cleanUrl(url) {
    try {
      var u = new URL(url, location.href);
      return u.origin + u.pathname;
    } catch (e) {
      return "";
    }
  }

  function currentReferrer() {
    return document.referrer ? cleanUrl(document.referrer) : "";
  }

  function clip(s, n) {
    s = (s || "").replace(/\s+/g, " ").trim();
    return s.length > n ? s.slice(0, n) : s;
  }

  // ---------- PostHog ----------
  // Official "load array.js, then init" install, from PostHog's assets host.
  // Events fired before the library finishes loading are queued here and
  // flushed on load, so nothing is lost and nothing blocks rendering.

  var queue = [];
  var phReady = false;
  var phFailed = false;

  function flush() {
    var item;
    while ((item = queue.shift())) {
      try {
        window.posthog.capture(item.name, item.props, item.opts);
      } catch (e) {
        /* drop */
      }
    }
  }

  var initPostHog = safely(function () {
    if (!cfg.posthogKey || DNT) return;
    if (window.posthog && window.posthog.__loaded) {
      phReady = true; // something else already loaded it; don't re-init
      return;
    }
    var s = document.createElement("script");
    s.async = true;
    s.crossOrigin = "anonymous";
    s.src = cfg.posthogAssetsHost + "/static/array.js";
    s.onload = safely(function () {
      if (!window.posthog || typeof window.posthog.init !== "function") {
        phFailed = true;
        queue.length = 0;
        return;
      }
      window.posthog.init(cfg.posthogKey, {
        api_host: cfg.posthogHost,
        // We send our own page_viewed (below); PostHog's automatic
        // $pageview would double count.
        capture_pageview: false,
        capture_pageleave: true, // $pageleave -> where sessions end
        // Clicks on links/buttons only: never inputs, textareas or forms,
        // and anything inside .ph-no-capture (the assistant chat) is skipped.
        autocapture: {
          dom_event_allowlist: ["click"],
          element_allowlist: ["a", "button"],
        },
        disable_session_recording: true, // Clarity owns recordings
        respect_dnt: true,
        // Events stay anonymous (no person profile) -- unique visitors are
        // still counted by distinct_id; nobody is identified.
        person_profiles: "identified_only",
        mask_personal_data_properties: true,
        persistence: "localStorage+cookie",
        // Strip query strings/fragments from the URLs PostHog adds itself.
        sanitize_properties: function (props) {
          ["$current_url", "$referrer", "$initial_referrer", "$referring_domain_url"].forEach(function (k) {
            if (typeof props[k] === "string" && props[k].indexOf("http") === 0) props[k] = cleanUrl(props[k]);
          });
          return props;
        },
        loaded: safely(function () {
          phReady = true;
          flush();
        }),
      });
    });
    s.onerror = function () {
      phFailed = true; // blocked by an ad-blocker/network: carry on silently
      queue.length = 0;
    };
    document.head.appendChild(s);
  });

  // ---------- Clarity ----------
  // Microsoft's official install snippet, run only after consent. It queues
  // clarity() calls until the tag loads. The "consent" call is the signal
  // Clarity needs before it records visitors in the EEA, UK and Switzerland.
  var loadClarity = safely(function () {
    if (!cfg.clarityId || DNT) return;
    window.clarity = window.clarity || function () {
      (window.clarity.q = window.clarity.q || []).push(arguments);
    };
    var t = document.createElement("script");
    t.async = true;
    t.src = "https://www.clarity.ms/tag/" + cfg.clarityId;
    document.head.appendChild(t);
    window.clarity("consent");
  });

  // Starts both tools. Runs once, and only after the visitor accepts (or on
  // a load where they already accepted).
  var startTracking = safely(function () {
    if (started || DNT) return;
    started = true;
    initPostHog();
    loadClarity();
    trackPageView();
    if (cfg.project) trackProjectView(cfg.project.slug, cfg.project.name);
    if (document.getElementById("asst-window")) {
      trackAssistantEvent(EVENTS.ASSISTANT_OPENED, { interface: "page" });
    }
  });

  // ---------- public API ----------

  var trackEvent = safely(function (name, props, opts) {
    if (!started || !cfg.posthogKey || DNT || phFailed) return;
    var payload = props || {};
    if (phReady) window.posthog.capture(name, payload, opts);
    else if (queue.length < 50) queue.push({ name: name, props: payload, opts: opts });
  });

  var trackPageView = safely(function () {
    trackEvent(EVENTS.PAGE_VIEWED, {
      path: location.pathname,
      page_title: document.title,
      referrer: currentReferrer(),
    });
  });

  var trackProjectView = safely(function (slug, name) {
    if (!slug) return;
    trackEvent(EVENTS.PROJECT_VIEWED, { project_name: name || slug, project_slug: slug });
  });

  // Where a destination falls, for grouping in PostHog without regexing URLs.
  function destinationType(u) {
    if (u.protocol === "mailto:") return "email";
    var h = u.hostname.replace(/^www\./, "");
    if (h === "linkedin.com") return "linkedin";
    if (h === "github.com") return "github";
    if (h === location.hostname.replace(/^www\./, "")) return "own_site";
    return "other";
  }

  var trackExternalLink = safely(function (anchor) {
    var u = new URL(anchor.href, location.href);
    var isMail = u.protocol === "mailto:";
    if (!isMail && u.protocol !== "http:" && u.protocol !== "https:") return;
    if (!isMail && u.origin === location.origin) return;
    var text = clip(anchor.getAttribute("aria-label") || anchor.textContent || anchor.title, 100);
    trackEvent(
      EVENTS.EXTERNAL_LINK_CLICKED,
      {
        // mailto: keeps the address but drops any ?subject=/body= text.
        destination: isMail ? "mailto:" + u.pathname : u.origin + u.pathname,
        destination_type: destinationType(u),
        link_text: text,
        source_page: location.pathname,
      },
      { transport: "sendBeacon" } // the page is about to unload
    );
  });

  // Question text -> one coarse category; the text itself never leaves
  // this function. First match wins, so order matters.
  var CATEGORY_RULES = [
    ["contact", /\b(contact|e-?mail|reach (him|out)|linkedin|phone|schedule|get in touch|talk to)\b/i],
    ["resume", /\b(resume|résumé|cv|curriculum)\b/i],
    ["job_search", /\b(hire|hiring|job|role|position|opportunit|open to|available|availability|salary|relocat|remote|interview|recruit|employ|looking for work)\w*/i],
    ["projects", /\b(project|built|build|pipeline|redis|trading|scorer|warehouse|timed|jvm|sniffer|assistant|rag|case study|demo)\w*/i],
    ["technical", /\b(python|flask|sql|postgres|docker|stack|architecture|code|skill|language|tech|framework|database|api|cloud|aws|java|javascript)\w*/i],
    ["about_me", /\b(who|about|background|bio|education|school|college|story|hobb|live|from|experience|career)\w*/i],
  ];

  function categorizeQuestion(text) {
    try {
      var t = String(text || "");
      for (var i = 0; i < CATEGORY_RULES.length; i++) {
        if (CATEGORY_RULES[i][1].test(t)) return CATEGORY_RULES[i][0];
      }
    } catch (e) {
      /* fall through */
    }
    return "other";
  }

  function lengthBucket(text) {
    var n = String(text || "").length;
    return n < 40 ? "short" : n < 160 ? "medium" : "long";
  }

  // Assistant events. `props` must already be free of user text; the only
  // supported way to describe a question is { question: <raw text> }, which
  // is converted to question_category/length bucket here and then dropped.
  var trackAssistantEvent = safely(function (name, props) {
    var p = {};
    props = props || {};
    Object.keys(props).forEach(function (k) {
      if (k !== "question") p[k] = props[k];
    });
    if (typeof props.question === "string") {
      p.question_category = categorizeQuestion(props.question);
      p.question_length = lengthBucket(props.question);
    }
    trackEvent(name, p);
  });

  // ---------- automatic instrumentation ----------

  // External links, resume views/downloads: one delegated listener.
  // auxclick catches middle-click (open in new tab).
  function onLinkClick(e) {
    safely(function () {
      var a = e.target && e.target.closest && e.target.closest("a[href]");
      if (!a) return;
      if (cfg.resumePath && cleanPath(a.href) === cfg.resumePath) {
        // `download` attribute = explicit download; otherwise it's opened
        // in the browser's PDF viewer.
        trackEvent(a.hasAttribute("download") ? EVENTS.RESUME_DOWNLOADED : EVENTS.RESUME_VIEWED, {
          source_page: location.pathname,
        }, { transport: "sendBeacon" });
        return;
      }
      trackExternalLink(a);
    })();
  }

  // Scroll depth: 25/50/75/100% of the page, once each per page load.
  // Skipped on pages that barely scroll so "100%" isn't meaningless.
  var scrollSeen = {};
  var scrollTicking = false;
  var onScroll = safely(function () {
    if (scrollTicking) return;
    scrollTicking = true;
    window.requestAnimationFrame(function () {
      scrollTicking = false;
      var doc = document.documentElement;
      var scrollable = doc.scrollHeight - window.innerHeight;
      if (scrollable < 200) return;
      var pct = ((window.pageYOffset || doc.scrollTop) / scrollable) * 100;
      [25, 50, 75, 100].forEach(function (mark) {
        if (pct >= mark - 1 && !scrollSeen[mark]) {
          scrollSeen[mark] = true;
          trackEvent(EVENTS.SCROLL_DEPTH, { depth: mark, path: location.pathname });
        }
      });
    });
  });

  // Client-side route changes (none today -- the site is multi-page -- but
  // this keeps page_viewed correct if one is ever added). De-duplicated by
  // path so replaceState calls / hash changes don't double count.
  // Before consent it only updates lastPath; startTracking() sends the page
  // view for the path the visitor is on when they accept.
  var lastPath = null;
  function maybePageView() {
    if (location.pathname === lastPath) return;
    lastPath = location.pathname;
    scrollSeen = {};
    // Wait a tick so a client router has updated document.title.
    if (started) setTimeout(trackPageView, 0);
  }

  function hookHistory() {
    ["pushState", "replaceState"].forEach(function (m) {
      var orig = history[m];
      if (typeof orig !== "function") return;
      history[m] = function () {
        var r = orig.apply(this, arguments);
        safely(maybePageView)();
        return r;
      };
    });
    window.addEventListener("popstate", safely(maybePageView));
  }

  // Clarity: mask everything a visitor can type, site-wide. (Passwords and
  // similar are always masked by Clarity itself; this covers free text such
  // as the assistant box and the demo forms.) Done on focus so inputs added
  // later are covered before anything is typed.
  function maskField(el) {
    if (el && el.matches && el.matches("input, textarea, select")) {
      el.setAttribute("data-clarity-mask", "True");
      el.classList.add("ph-no-capture");
    }
  }
  function maskInputs() {
    var all = document.querySelectorAll("input, textarea, select");
    for (var i = 0; i < all.length; i++) maskField(all[i]);
  }

  // ---------- consent banner ----------
  // Built here, not in the template, so the markup and its behaviour live in
  // one file. Accept and Decline carry equal weight on purpose: refusing
  // should be as easy as agreeing.

  var bannerEl = null;

  function hideBanner() {
    if (bannerEl && bannerEl.parentNode) bannerEl.parentNode.removeChild(bannerEl);
    bannerEl = null;
  }

  function makeButton(label, className, onPress) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = className;
    b.textContent = label;
    b.addEventListener("click", safely(onPress));
    return b;
  }

  function choose(value) {
    writeConsent(value);
    hideBanner();
    if (value === "granted") {
      startTracking();
    } else if (started) {
      // Already-loaded tags can only be stopped by a reload.
      window.location.reload();
    }
  }

  var showBanner = safely(function () {
    if (bannerEl || DNT) return;
    var box = document.createElement("div");
    box.className = "consent-banner";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", "Analytics choice");

    var text = document.createElement("p");
    text.appendChild(document.createTextNode(
      "This site uses PostHog and Microsoft Clarity to see how it is used: anonymous " +
      "page views and clicks, and session recordings with typed text masked. Nothing " +
      "loads until you choose. "
    ));
    var legal = document.createElement("a");
    legal.href = cfg.legalPath || "/legal";
    legal.textContent = "Privacy and cookies";
    text.appendChild(legal);
    text.appendChild(document.createTextNode("."));

    var actions = document.createElement("div");
    actions.className = "consent-actions";
    actions.appendChild(makeButton("Decline", "consent-button", function () { choose("denied"); }));
    actions.appendChild(makeButton("Accept", "consent-button consent-accept", function () { choose("granted"); }));

    box.appendChild(text);
    box.appendChild(actions);
    document.body.appendChild(box);
    bannerEl = box;
  });

  // ---------- go ----------

  window.siteAnalytics = {
    EVENTS: EVENTS,
    trackEvent: trackEvent,
    trackPageView: trackPageView,
    trackProjectView: trackProjectView,
    trackExternalLink: trackExternalLink,
    trackAssistantEvent: trackAssistantEvent,
    categorizeQuestion: categorizeQuestion, // exposed for tests
    openConsent: showBanner,
  };

  safely(function () {
    hookHistory();
    maskInputs();
    document.addEventListener("focusin", function (e) { safely(maskField)(e.target); }, true);
    document.addEventListener("click", onLinkClick, true);
    document.addEventListener("auxclick", onLinkClick, true);
    document.addEventListener("click", safely(function (e) {
      if (e.target.closest && e.target.closest("[data-cookie-settings]")) showBanner();
    }), true);
    window.addEventListener("scroll", onScroll, { passive: true });

    maybePageView(); // arms the de-dupe; sends only once consent is given
    var choice = readConsent();
    if (choice === "granted") startTracking();
    else if (choice === null) showBanner();
    // "denied": nothing loads, nothing is shown.
  })();
})();
