/* ==========================================================================
   Aida's Beauty Salon: интерактив сайта.
   Без этого файла сайт остаётся рабочим: формы отправляются обычным образом,
   а все блоки видны сразу.
   ========================================================================== */
(function () {
  'use strict';

  var html = document.documentElement;
  html.classList.remove('no-js');
  html.classList.add('js');

  var tokenInput = document.querySelector('input[name=_csrf]');
  var token = tokenInput ? tokenInput.value : '';
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ----- Короткое уведомление --------------------------------------------- */
  var toast = document.getElementById('toast');
  var toastTimer = null;
  function showToast(text) {
    if (!toast) return;
    toast.textContent = text;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.hidden = true; }, 3200);
  }

  /* ----- Мобильное меню ---------------------------------------------------- */
  var toggle = document.querySelector('.nav-toggle');
  var nav = document.getElementById('nav');
  function closeMenu() {
    if (!nav || !toggle) return;
    nav.classList.remove('open');
    toggle.setAttribute('aria-expanded', 'false');
  }
  if (toggle && nav) {
    toggle.addEventListener('click', function () {
      var open = nav.classList.toggle('open');
      toggle.setAttribute('aria-expanded', String(open));
    });
    nav.addEventListener('click', function (e) { if (e.target.closest('a')) closeMenu(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeMenu(); });
    document.addEventListener('click', function (e) {
      if (!nav.contains(e.target) && !toggle.contains(e.target)) closeMenu();
    });
  }

  /* ----- Плавное появление блоков ------------------------------------------ */
  var revealItems = document.querySelectorAll('.reveal');
  if (revealItems.length) {
    if ('IntersectionObserver' in window && !reduceMotion) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add('in');
            io.unobserve(entry.target);
          }
        });
      }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });
      revealItems.forEach(function (el, i) {
        el.style.transitionDelay = ((i % 3) * 80) + 'ms';
        io.observe(el);
      });
    } else {
      revealItems.forEach(function (el) { el.classList.add('in'); });
    }
  }

  /* ----- Каталог услуг: подсветка текущего раздела ------------------------- */
  var chips = document.querySelectorAll('.chip[data-spy]');
  var blocks = document.querySelectorAll('.price-block');
  if (chips.length && blocks.length && 'IntersectionObserver' in window) {
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        chips.forEach(function (chip) {
          var on = chip.getAttribute('data-spy') === entry.target.id;
          chip.classList.toggle('active', on);
          if (on && chip.scrollIntoView && !reduceMotion) {
            chip.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
          }
        });
      });
    }, { rootMargin: '-35% 0px -55% 0px' });
    blocks.forEach(function (b) { spy.observe(b); });
  }

  /* ----- Подтверждение действий -------------------------------------------- */
  document.querySelectorAll('[data-confirm]').forEach(function (el) {
    el.addEventListener('click', function (e) {
      if (!window.confirm(el.getAttribute('data-confirm'))) e.preventDefault();
    });
  });

  /* ----- Телефон: оставляем только допустимые символы ---------------------- */
  document.querySelectorAll('input[type=tel]').forEach(function (input) {
    input.addEventListener('input', function () {
      var clean = input.value.replace(/[^\d+\s\-()]/g, '');
      if (clean !== input.value) input.value = clean;
    });
  });

  /* ----- Автоскрытие сообщений об успехе ----------------------------------- */
  document.querySelectorAll('.flash.ok').forEach(function (el) {
    setTimeout(function () { el.style.display = 'none'; }, 7000);
  });

  /* ======================================================================
     Мастер записи
     ====================================================================== */
  var form = document.getElementById('booking-form');
  if (!form) return;

  var slotsBox = document.getElementById('slots');
  var dayInput = document.getElementById('day');
  var slotsUrl = form.getAttribute('data-slots-url');
  var msg = function (name) { return form.getAttribute('data-msg-' + name) || ''; };
  var requestId = 0;

  function checked(name) { return form.querySelector('input[name=' + name + ']:checked'); }

  function updateSummary() {
    var svc = checked('service');
    var master = checked('master');
    var time = checked('time');
    var title = document.getElementById('sum-service');
    var meta = document.getElementById('sum-meta');
    var price = document.getElementById('sum-price');
    if (!svc) {
      title.textContent = msg('none');
      meta.textContent = '';
      price.textContent = '';
      return;
    }
    title.textContent = svc.getAttribute('data-name');
    var parts = [svc.getAttribute('data-duration') + ' ' + form.getAttribute('data-min')];
    if (master) parts.push(master.getAttribute('data-name'));
    if (dayInput.value) parts.push(dayInput.value.split('-').reverse().join('.'));
    if (time) parts.push(time.value);
    meta.textContent = parts.join(', ');
    price.textContent = svc.getAttribute('data-price');
  }

  // Показываем только тех мастеров, кто выполняет выбранную услугу
  function filterMasters() {
    var svc = checked('service');
    var category = svc ? svc.getAttribute('data-category') : null;
    form.querySelectorAll('.choice.master').forEach(function (label) {
      var show = !category || label.getAttribute('data-category') === category;
      label.classList.toggle('hidden', !show);
    });
    var current = checked('master');
    if (current && current.value !== '0' && current.closest('.choice').classList.contains('hidden')) {
      form.querySelector('input[name=master][value="0"]').checked = true;
    }
  }

  function renderNote(text) {
    slotsBox.textContent = '';
    var p = document.createElement('p');
    p.className = 'muted slots-note';
    p.textContent = text;
    slotsBox.appendChild(p);
  }

  function renderSlots(list, keep) {
    slotsBox.textContent = '';
    if (!list.length) { renderNote(msg('empty')); return; }
    list.forEach(function (time) {
      var label = document.createElement('label');
      label.className = 'slot';
      var input = document.createElement('input');
      input.type = 'radio';
      input.name = 'time';
      input.value = time;
      if (time === keep) input.checked = true;
      var span = document.createElement('span');
      span.textContent = time;
      label.appendChild(input);
      label.appendChild(span);
      slotsBox.appendChild(label);
    });
  }

  function loadSlots() {
    var svc = checked('service');
    var master = checked('master');
    var day = dayInput.value;
    if (!svc || !day) {
      renderNote(msg('hint'));
      updateSummary();
      return;
    }
    var keep = (checked('time') || {}).value;
    var id = ++requestId;
    renderNote(msg('search'));
    var url = slotsUrl + '?service=' + encodeURIComponent(svc.value) +
      '&master=' + encodeURIComponent(master ? master.value : 0) + '&day=' + encodeURIComponent(day);
    fetch(url, { headers: { 'X-CSRF-Token': token } })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (res) {
        if (id !== requestId) return; // пришёл устаревший ответ
        if (!res.ok || !res.data.ok) renderNote(res.data.error || msg('net'));
        else renderSlots(res.data.slots, keep);
        updateSummary();
      })
      .catch(function () {
        if (id !== requestId) return;
        renderNote(msg('net'));
        showToast(msg('net'));
      });
  }

  form.addEventListener('change', function (e) {
    var name = e.target.name;
    if (name === 'service') {
      filterMasters();
      if (!dayInput.value && dayInput.min) dayInput.value = dayInput.min;
      loadSlots();
      // на телефоне после выбора услуги прокручиваем к выбору времени
      var next = document.getElementById('masters');
      if (next && window.innerWidth < 920 && next.scrollIntoView) {
        next.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' });
      }
    } else if (name === 'master' || name === 'day') {
      loadSlots();
    } else {
      updateSummary();
    }
  });

  form.addEventListener('submit', function (e) {
    var problem = '';
    if (!checked('service')) problem = msg('service');
    else if (!dayInput.value) problem = msg('date');
    else if (!checked('time')) problem = msg('time');
    if (problem) {
      e.preventDefault();
      showToast(problem);
    }
  });

  filterMasters();
  updateSummary();
  if (checked('service') && dayInput.value && !checked('time')) loadSlots();
})();
