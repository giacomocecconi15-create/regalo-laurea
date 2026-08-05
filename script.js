/* ============================================================
   Regalo di laurea — Sofia
   Vanilla JS. Intersection Observer per far scattare le
   animazioni solo quando serve, un solo loop rAF per gli
   effetti legati allo scroll.
   ============================================================ */

(function () {
  'use strict';

  /* ---------------------------- config ---------------------------- */

  var YT_VIDEO_ID = '45bXmwsr5h8';
  var NTFY_URL    = 'https://ntfy.sh/barca-laurea-9k3xq7';
  var STORE_KEY   = 'regalo-sofia:riscattato';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }

  /* memoria locale: se lo storage è bloccato non deve rompere nulla */
  var store = {
    get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) {} }
  };


  /* ---------------------------- 1. hero --------------------------- */

  requestAnimationFrame(function () {
    document.body.classList.add('is-loaded');
  });

  var scrolled = false;
  window.addEventListener('scroll', function onFirstScroll() {
    if (scrolled || window.scrollY < 24) return;
    scrolled = true;
    document.body.classList.add('has-scrolled');
    window.removeEventListener('scroll', onFirstScroll);
  }, { passive: true });


  /* --------------------------- 2. video --------------------------- */

  var videoSection = document.getElementById('video');
  var videoFrame   = document.querySelector('.video__frame');
  var caption      = document.querySelector('[data-caption]');
  var soundBtn     = document.querySelector('[data-sound-toggle]');
  var soundLabel   = document.querySelector('[data-sound-label]');
  var fallback     = document.querySelector('[data-fallback]');

  var player = null;
  var playerReady = false;
  var muted = true;
  var apiRequested = false;

  function loadYouTubeApi() {
    if (apiRequested) return;
    apiRequested = true;

    window.onYouTubeIframeAPIReady = createPlayer;

    var s = document.createElement('script');
    s.src = 'https://www.youtube.com/iframe_api';
    s.async = true;
    document.head.appendChild(s);
  }

  function createPlayer() {
    player = new YT.Player('yt-player', {
      videoId: YT_VIDEO_ID,
      playerVars: {
        autoplay: 1,
        mute: 1,
        controls: 0,
        playsinline: 1,
        loop: 1,
        playlist: YT_VIDEO_ID,   // necessario perché loop funzioni su un video singolo
        rel: 0,
        modestbranding: 1,
        disablekb: 1,
        fs: 0,
        iv_load_policy: 3
      },
      events: {
        onReady: function (e) {
          playerReady = true;
          e.target.mute();
          if (videoInView) e.target.playVideo();
        },
        onStateChange: function (e) {
          if (e.data === YT.PlayerState.PLAYING) {
            videoFrame.classList.add('is-playing');
            if (fallback) { fallback.classList.remove('is-in'); fallback.hidden = true; }
          }
        }
      }
    });
  }

  var videoInView = false;
  var fallbackTimer = null;

  if (videoSection && videoFrame) {
    /* prepara il player poco prima che la sezione entri in vista */
    new IntersectionObserver(function (entries, obs) {
      if (entries[0].isIntersecting) {
        loadYouTubeApi();
        obs.disconnect();
      }
    }, { rootMargin: '150% 0px' }).observe(videoSection);

    /* riproduce solo mentre è davvero visibile: batteria e fluidità */
    new IntersectionObserver(function (entries) {
      videoInView = entries[0].isIntersecting;

      /* se l'embed non parte (rete, blocchi del browser) offriamo il link */
      if (videoInView && fallback && !fallbackTimer) {
        fallbackTimer = window.setTimeout(function () {
          if (videoFrame.classList.contains('is-playing')) return;
          fallback.hidden = false;
          requestAnimationFrame(function () { fallback.classList.add('is-in'); });
        }, 6000);
      }

      if (!playerReady) return;
      if (videoInView) { player.playVideo(); }
      else { player.pauseVideo(); }
    }, { threshold: 0.15 }).observe(videoSection);

    soundBtn.addEventListener('click', function () {
      if (!playerReady) return;
      if (muted) {
        player.unMute();
        player.setVolume(100);
        muted = false;
        soundLabel.textContent = 'Tocca per silenziare';
      } else {
        player.mute();
        muted = true;
        soundLabel.textContent = "Tocca per l'audio";
      }
    });
  }


  /* ------------------ 3. testo che si illumina -------------------- */

  var revealSection = document.getElementById('passaggio');
  var revealText    = document.querySelector('[data-reveal-text]');
  var words = [];
  var lastOpacity = [];

  if (revealText) {
    var source = revealText.textContent.trim().split(/\s+/);
    revealText.textContent = '';
    source.forEach(function (word, i) {
      var span = document.createElement('span');
      span.className = 'w';
      span.textContent = word;
      revealText.appendChild(span);
      if (i < source.length - 1) {
        revealText.appendChild(document.createTextNode(' '));
      }
      words.push(span);
      lastOpacity.push(-1);
    });
  }

  /* ---------------- loop di scroll (video + testo) ---------------- */

  var ticking = false;
  var activeSections = 0;

  function onScroll() {
    if (ticking || activeSections === 0) return;
    ticking = true;
    requestAnimationFrame(update);
  }

  function update() {
    ticking = false;
    var vh = window.innerHeight;

    /* didascalia del video: entra e poi esce, seguendo lo scroll */
    if (videoSection && caption) {
      var vr = videoSection.getBoundingClientRect();
      var vp = clamp(-vr.top / (vr.height - vh), 0, 1);
      caption.classList.toggle('is-in', vp > 0.10 && vp < 0.72);
    }

    /* parole: da grigio scuro a bianco pieno, una alla volta */
    if (revealSection && words.length && !reduceMotion) {
      var rr = revealSection.getBoundingClientRect();
      var rp = clamp(-rr.top / (rr.height - vh), 0, 1);

      /* la finestra utile parte dopo un po' e finisce prima della fine */
      var p = clamp((rp - 0.12) / 0.62, 0, 1);
      var span = 2.2;                                  // quante parole sfumano insieme
      var head = p * (words.length + span);

      for (var i = 0; i < words.length; i++) {
        var t = clamp((head - i) / span, 0, 1);
        var o = 0.16 + 0.84 * t;
        /* scriviamo solo quando cambia davvero: meno lavoro per frame */
        if (Math.abs(o - lastOpacity[i]) < 0.004) continue;
        lastOpacity[i] = o;
        words[i].style.opacity = o.toFixed(3);
      }
    }
  }

  function watchForScroll(el) {
    if (!el) return;
    new IntersectionObserver(function (entries) {
      activeSections += entries[0].isIntersecting ? 1 : -1;
      activeSections = Math.max(0, activeSections);
      if (entries[0].isIntersecting) onScroll();
    }, { rootMargin: '10% 0px' }).observe(el);
  }

  watchForScroll(videoSection);
  watchForScroll(revealSection);

  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll, { passive: true });


  /* --------------------------- 4. finale -------------------------- */

  var fadeItems = document.querySelectorAll('[data-fade]');
  var giftBox   = document.querySelector('[data-gift]');
  var claimBtn  = document.querySelector('[data-claim]');
  var giftText  = document.querySelector('[data-reveal]');

  var fadeObserver = new IntersectionObserver(function (entries, obs) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-in');
      obs.unobserve(entry.target);
    });
  }, { threshold: 0.25 });

  fadeItems.forEach(function (el) { fadeObserver.observe(el); });

  if (giftBox && claimBtn) {
    new IntersectionObserver(function (entries, obs) {
      if (!entries[0].isIntersecting) return;
      claimBtn.classList.add('is-in');
      obs.disconnect();
    }, { threshold: 0.4 }).observe(giftBox);
  }


  /* ------------------- 5. riscatto del regalo --------------------- */

  function showGift(animated) {
    giftText.hidden = false;

    if (!animated) {
      claimBtn.hidden = true;
      requestAnimationFrame(function () {
        giftText.classList.add('is-in');
      });
      return;
    }

    claimBtn.disabled = true;
    claimBtn.classList.add('is-out');

    window.setTimeout(function () {
      claimBtn.hidden = true;
      requestAnimationFrame(function () {
        giftText.classList.add('is-in');
      });
    }, reduceMotion ? 0 : 700);
  }

  function notify() {
    /* corpo testuale semplice: niente header custom, niente preflight */
    var body =
      'Sofia ha accettato il regalo\n' +
      'Giornata in barca — Messina, 1 agosto 2026\n' +
      new Date().toLocaleString('it-IT');

    return fetch(NTFY_URL, { method: 'POST', body: body });
  }

  if (claimBtn && giftText) {
    if (store.get(STORE_KEY) === '1') {
      showGift(false);
    }

    claimBtn.addEventListener('click', function () {
      if (claimBtn.disabled) return;

      store.set(STORE_KEY, '1');

      /* la notifica non deve mai bloccare il regalo */
      try {
        notify().catch(function (err) {
          console.warn('Notifica non inviata:', err);
        });
      } catch (err) {
        console.warn('Notifica non inviata:', err);
      }

      showGift(true);
    });
  }

})();
