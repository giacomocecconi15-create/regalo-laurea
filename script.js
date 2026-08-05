/* ============================================================
   Regalo di laurea — Sofia
   Vanilla JS. Intersection Observer per far scattare le
   animazioni solo quando servono, un solo loop rAF per gli
   effetti legati allo scroll.
   ============================================================ */

(function () {
  'use strict';

  /* ---------------------------- config ---------------------------- */

  var YT_VIDEO_ID = '45bXmwsr5h8';
  var NTFY_URL    = 'https://ntfy.sh/barca-laurea-9k3xq7';
  var STORE_KEY   = 'regalo-sofia:riscattato:v2';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }

  /* memoria locale: se lo storage è bloccato non deve rompere nulla */
  var store = {
    get: function (k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window.localStorage.setItem(k, v); } catch (e) {} },
    del: function (k) { try { window.localStorage.removeItem(k); } catch (e) {} }
  };

  /* aprendo il sito con ?reset (o #reset) il bottone torna da cliccare:
     serve per provarlo senza dover svuotare i dati del browser */
  if (/(^|[?&#])reset\b/.test(window.location.search + window.location.hash)) {
    store.del(STORE_KEY);
    if (window.history.replaceState) {
      window.history.replaceState(null, '', window.location.pathname);
    }
  }


  /* ---------------------------- 1. hero --------------------------- */

  /* il nome entra una lettera alla volta */
  var nameEl = document.querySelector('[data-split]');
  if (nameEl) {
    var name = nameEl.textContent.trim();
    nameEl.setAttribute('aria-label', name);
    nameEl.textContent = '';
    name.split('').forEach(function (ch, i) {
      var span = document.createElement('span');
      span.className = 'l';
      span.textContent = ch;
      span.setAttribute('aria-hidden', 'true');
      span.style.transitionDelay = (0.35 + i * 0.075) + 's';
      nameEl.appendChild(span);
    });
  }

  requestAnimationFrame(function () {
    document.body.classList.add('is-loaded');
  });

  /* indicazione fissa «scorri verso il basso» */
  var cue      = document.querySelector('[data-scrollcue]');
  var congrats = document.getElementById('congratulazioni');
  var finale   = document.getElementById('finale');

  /* compare al primo scroll e sparisce quando si arriva al regalo */
  if (finale) {
    new IntersectionObserver(function (entries) {
      document.body.classList.toggle('at-end', entries[0].isIntersecting);
    }, { threshold: 0.3 }).observe(finale);
  }

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
  var videoInView = false;
  var fallbackTimer = null;

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
        soundLabel.textContent = 'Tocca di nuovo per silenziare';
      } else {
        player.mute();
        muted = true;
        soundLabel.textContent = "Tocca lo schermo per l'audio";
      }
    });
  }


  /* ------------------ 3. testo che si illumina -------------------- */

  var thanksSection = document.getElementById('grazie');
  var thanksText    = document.querySelector('[data-reveal-text]');
  var words = [];
  var lastOpacity = [];

  if (thanksText) {
    var source = thanksText.textContent.trim().split(/\s+/);
    thanksText.textContent = '';
    source.forEach(function (word, i) {
      var span = document.createElement('span');
      span.className = 'w';
      span.textContent = word;
      thanksText.appendChild(span);
      if (i < source.length - 1) {
        thanksText.appendChild(document.createTextNode(' '));
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

    /* video: la didascalia entra e poi esce seguendo lo scroll */
    if (videoSection && caption) {
      var vr = videoSection.getBoundingClientRect();
      var vp = clamp(-vr.top / (vr.height - vh), 0, 1);
      caption.classList.toggle('is-in', vp > 0.08 && vp < 0.72);
    }

    /* sopra la sezione chiara l'indicazione fissa si inverte */
    if (cue && congrats) {
      var cr = congrats.getBoundingClientRect();
      var cueY = vh - 52;
      cue.classList.toggle('is-light', cr.top < cueY && cr.bottom > cueY);
    }

    /* parole: da grigio scuro a bianco pieno, una alla volta */
    if (thanksSection && words.length && !reduceMotion) {
      var rr = thanksSection.getBoundingClientRect();
      var rp = clamp(-rr.top / (rr.height - vh), 0, 1);

      /* la finestra utile parte dopo un po' e finisce prima della fine */
      var p = clamp((rp - 0.12) / 0.58, 0, 1);
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

  /* Teniamo il conto delle sezioni in vista con uno stato per sezione:
     un contatore condiviso si sfaserebbe alla prima callback "non visibile". */
  function watchForScroll(el) {
    if (!el) return;
    var wasIn = false;
    new IntersectionObserver(function (entries) {
      var isIn = entries[entries.length - 1].isIntersecting;
      if (isIn === wasIn) return;
      wasIn = isIn;
      activeSections += isIn ? 1 : -1;
      if (isIn) onScroll();
    }, { rootMargin: '10% 0px' }).observe(el);
  }

  watchForScroll(videoSection);
  watchForScroll(thanksSection);
  watchForScroll(congrats);

  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll, { passive: true });


  /* --------------- 4. congratulazioni, a scaglioni ---------------- */

  if (congrats) {
    var pieces = congrats.querySelectorAll('[data-stagger], .trip__row');

    new IntersectionObserver(function (entries, obs) {
      if (!entries[0].isIntersecting) return;
      obs.disconnect();
      Array.prototype.forEach.call(pieces, function (el, i) {
        el.style.transitionDelay = (i * 0.13) + 's';
        el.classList.add('is-in');
      });
    }, { threshold: 0.2 }).observe(congrats);
  }


  /* --------------------------- 5. finale -------------------------- */

  var fadeItems = document.querySelectorAll('[data-fade]');
  var giftBox   = document.querySelector('[data-gift]');
  var giftHint  = document.querySelector('[data-gift-hint]');
  var claimBtn  = document.querySelector('[data-claim]');
  var giftText  = document.querySelector('[data-reveal]');
  var giftLine  = document.querySelector('[data-gift-letters]');

  var fadeObserver = new IntersectionObserver(function (entries, obs) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('is-in');
      obs.unobserve(entry.target);
    });
  }, { threshold: 0.25 });

  Array.prototype.forEach.call(fadeItems, function (el) { fadeObserver.observe(el); });

  var redeemed = store.get(STORE_KEY) === '1';

  if (giftBox && claimBtn) {
    new IntersectionObserver(function (entries, obs) {
      if (!entries[0].isIntersecting) return;
      obs.disconnect();
      if (redeemed) return;
      claimBtn.classList.add('is-in');
      if (giftHint) giftHint.classList.add('is-in');
    }, { threshold: 0.35 }).observe(giftBox);
  }


  /* ------------- lettere del regalo: sparse e poi a posto ---------- */

  /* Ogni lettera parte da una posizione casuale vicina e scivola al suo
     posto, una dopo l'altra. Le parole restano intere (.cw) così il testo
     va a capo come si deve. Solo transform e opacity: gira liscio. */
  var letterCount = 0;

  if (giftLine) {
    var line = giftLine.textContent.trim();
    giftLine.textContent = '';
    giftLine.setAttribute('aria-label', line);

    line.split(' ').forEach(function (word, wi, all) {
      var wrap = document.createElement('span');
      wrap.className = 'cw';

      word.split('').forEach(function (ch) {
        var c = document.createElement('span');
        c.className = 'c';
        c.textContent = ch;
        c.setAttribute('aria-hidden', 'true');
        c.style.setProperty('--dx', (Math.random() * 40 - 20).toFixed(1) + 'px');
        c.style.setProperty('--dy', (Math.random() * 34 - 6).toFixed(1) + 'px');
        c.style.setProperty('--r', (Math.random() * 24 - 12).toFixed(1) + 'deg');
        c.style.setProperty('--d', (0.12 + letterCount * 0.028).toFixed(3) + 's');
        c.style.setProperty('--i', letterCount);
        wrap.appendChild(c);
        letterCount++;
      });

      giftLine.appendChild(wrap);
      if (wi < all.length - 1) {
        giftLine.appendChild(document.createTextNode(' '));
      }
    });
  }

  /* l'onda calda parte quando l'ultima lettera è arrivata */
  function waveWhenSettled() {
    if (!giftLine || reduceMotion) return;
    var settled = (0.12 + letterCount * 0.028 + 0.95) * 1000;
    window.setTimeout(function () { giftLine.classList.add('is-wave'); }, settled);
  }


  /* ---------------------- coriandoli (breve) ---------------------- */

  function confetti() {
    if (reduceMotion) return;

    var canvas = document.createElement('canvas');
    canvas.className = 'confetti';
    document.body.appendChild(canvas);

    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var w = canvas.width  = window.innerWidth * dpr;
    var h = canvas.height = window.innerHeight * dpr;
    var ctx = canvas.getContext('2d');

    var colors = ['#ff9a5a', '#f6f0e7', '#ffd0a8', '#ffffff'];
    var bits = [];
    var n = window.innerWidth < 500 ? 60 : 90;

    for (var i = 0; i < n; i++) {
      bits.push({
        x: w * (0.2 + Math.random() * 0.6),
        y: h * (0.55 + Math.random() * 0.15),
        vx: (Math.random() - 0.5) * 9 * dpr,
        vy: (-9 - Math.random() * 9) * dpr,
        s: (4 + Math.random() * 5) * dpr,
        rot: Math.random() * Math.PI,
        vr: (Math.random() - 0.5) * 0.3,
        c: colors[(Math.random() * colors.length) | 0]
      });
    }

    var start = performance.now();

    function frame(now) {
      var life = now - start;
      ctx.clearRect(0, 0, w, h);

      for (var i = 0; i < bits.length; i++) {
        var b = bits[i];
        b.vy += 0.34 * dpr;          // gravità
        b.vx *= 0.995;
        b.x += b.vx;
        b.y += b.vy;
        b.rot += b.vr;

        ctx.save();
        ctx.translate(b.x, b.y);
        ctx.rotate(b.rot);
        ctx.globalAlpha = clamp(1 - (life - 1600) / 1200, 0, 1);
        ctx.fillStyle = b.c;
        ctx.fillRect(-b.s / 2, -b.s / 2, b.s, b.s * 0.6);
        ctx.restore();
      }

      if (life < 2800) {
        requestAnimationFrame(frame);
      } else {
        canvas.remove();
      }
    }

    requestAnimationFrame(frame);
  }


  /* ------------------- 6. riscatto del regalo --------------------- */

  function showGift(animated) {
    giftText.hidden = false;

    if (!animated) {
      claimBtn.hidden = true;
      if (giftHint) giftHint.hidden = true;
      giftText.classList.add('is-instant');
      requestAnimationFrame(function () {
        giftText.classList.add('is-in');
      });
      return;
    }

    claimBtn.disabled = true;
    claimBtn.classList.add('is-out');
    if (giftHint) giftHint.classList.add('is-out');

    window.setTimeout(function () {
      claimBtn.hidden = true;
      if (giftHint) giftHint.hidden = true;
      requestAnimationFrame(function () {
        giftText.classList.add('is-in');
        confetti();
        waveWhenSettled();
      });
    }, reduceMotion ? 0 : 650);
  }

  function notify() {
    /* corpo testuale semplice: niente header custom, niente preflight */
    var body =
      'Sofia ha accettato il regalo 🎁\n' +
      'Giornata in barca — Messina, 1 agosto 2026\n' +
      new Date().toLocaleString('it-IT');

    return fetch(NTFY_URL, { method: 'POST', body: body });
  }

  if (claimBtn && giftText) {
    if (redeemed) showGift(false);

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
