(() => {
  'use strict';
  document.documentElement.classList.add('js');
  const config = window.JAXGO_CONFIG || {};
  const basePath = String(config.basePath || '').replace(/\/$/, '');
  const assetUrl = path => `${basePath}${path}`;
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const form = $('#contact-form');
  const formStatus = $('#form-status');
  const submitButton = form?.querySelector('[type="submit"]');

  function track(goal, params = {}) {
    window.dispatchEvent(new CustomEvent('jaxgo:goal', { detail: { goal, ...params } }));
    const id = Number(config.metrikaId);
    if (Number.isInteger(id) && id > 0 && typeof window.ym === 'function') {
      window.ym(id, 'reachGoal', goal, params);
    }
  }
  window.jaxgoTrack = track;
  $$('[data-goal]').forEach(el => el.addEventListener('click', () => track(el.dataset.goal)));

  // Keep native anchor scrolling, browser history, and keyboard navigation.
  const menuButton = $('.menu-toggle');
  const menu = $('#menu');
  function closeMenu(returnFocus = false) {
    menu.classList.remove('is-open');
    menuButton.setAttribute('aria-expanded', 'false');
    menuButton.setAttribute('aria-label', 'Открыть меню');
    if (returnFocus) menuButton.focus();
  }
  menuButton.addEventListener('click', () => {
    const open = menu.classList.toggle('is-open');
    menuButton.setAttribute('aria-expanded', String(open));
    menuButton.setAttribute('aria-label', open ? 'Закрыть меню' : 'Открыть меню');
  });
  menu.querySelectorAll('a').forEach(link => link.addEventListener('click', () => closeMenu()));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu.classList.contains('is-open')) closeMenu(true);
  });
  document.addEventListener('click', event => {
    if (menu.classList.contains('is-open') && !event.target.closest('.top')) closeMenu();
  });
  $$('[data-open-service]').forEach(link => link.addEventListener('click', () => {
    const section = document.getElementById(`service-${link.dataset.openService}`);
    if (section) section.open = true;
  }));

  const nav = $$('.nav a[href^="#"]').map(link => ({ link, section: document.getElementById(link.hash.slice(1)) }));
  const steps = $$('.process-step');
  let scrollFrame = 0;
  function updateScrollUI() {
    const height = Math.max(1, document.documentElement.scrollHeight - innerHeight);
    document.documentElement.style.setProperty('--scroll-progress', `${Math.min(100, scrollY / height * 100)}%`);
    let current;
    nav.forEach(item => { if (item.section?.getBoundingClientRect().top < 180) current = item.link; });
    nav.forEach(({link}) => {
      link.classList.toggle('is-current', link === current);
      if (link === current) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
    let currentStep = steps[0];
    steps.forEach(step => { if (step.getBoundingClientRect().top < innerHeight * .58) currentStep = step; });
    steps.forEach(step => step.classList.toggle('is-current', step === currentStep));
    scrollFrame = 0;
  }
  window.addEventListener('scroll', () => {
    if (!scrollFrame) scrollFrame = requestAnimationFrame(updateScrollUI);
  }, { passive: true });
  window.addEventListener('resize', () => {
    if (innerWidth > 720) closeMenu();
    updateScrollUI();
  });
  updateScrollUI();
  if (!motion.matches && 'IntersectionObserver' in window) {
    const reveal = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          reveal.unobserve(entry.target);
        }
      });
    }, { threshold: .01, rootMargin: '60px 0px' });
    $$('.section-heading, .process-step, .proof-strip').forEach(el => {
      el.classList.add('reveal-pending');
      reveal.observe(el);
    });
    motion.addEventListener('change', event => {
      if (event.matches) {
        $$('.reveal-pending').forEach(el => el.classList.add('is-visible'));
        reveal.disconnect();
      }
    });
  }

  // Roving keyboard focus shared by pricing and workflow tabs.
  function tabControls(tabs, select) {
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => select(tab));
      tab.addEventListener('keydown', event => {
        let next;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = tabs.length - 1;
        if (next === undefined) return;
        event.preventDefault();
        select(tabs[next]);
        tabs[next].focus();
      });
    });
  }
  function setSelected(tabs, selected) {
    tabs.forEach(tab => {
      tab.setAttribute('aria-selected', String(tab === selected));
      tab.tabIndex = tab === selected ? 0 : -1;
    });
  }
  const priceTabs = $$('.price-tab');
  function selectPrice(tab) {
    setSelected(priceTabs, tab);
    $$('.price-panel').forEach(panel => { panel.hidden = panel.id !== tab.getAttribute('aria-controls'); });
    updateScrollUI();
  }
  tabControls(priceTabs, tab => {
    selectPrice(tab);
    track('pricing_category', { category: tab.dataset.price });
  });
  if (priceTabs.length) selectPrice(priceTabs[0]);

  const workflows = {
    commerce: { source: 'Прайсы и остатки поставщиков', input: 'цены · наличие · ассортимент', logic: 'Обработка и синхронизация', output: 'обновлённые данные', target: 'Актуальный каталог и заказы', caption: 'Пример логики: данные поставщиков связываются с процессом продаж.' },
    reports: { source: 'Визиты и данные менеджеров', input: 'контакты · посещения · задачи', logic: 'Сбор в единую структуру', output: 'общая база и сводка', target: 'Отчёт для руководителя', caption: 'Пример из кейса полевого менеджера: данные заносятся через бота и попадают в общую таблицу.' },
    content: { source: 'Идея и исходные материалы', input: 'фото · товар · предложение', logic: 'Подготовка и согласование', output: 'карточка · текст · подпись', target: 'Публикация в выбранных каналах', caption: 'Пример из контент-завода: публикация проходит через согласование подготовленных материалов.' },
  };
  const flowTabs = $$('.workflow-tabs [role="tab"]');
  tabControls(flowTabs, tab => {
    const flow = workflows[tab.dataset.flow];
    setSelected(flowTabs, tab);
    Object.entries(flow).forEach(([key, value]) => { document.getElementById(`flow-${key}`).textContent = value; });
    $('#flow-panel').setAttribute('aria-labelledby', tab.id);
    track('workflow_switch', { workflow: tab.dataset.flow });
  });

  const caseFilters = $$('.filter-button');
  const caseCards = $$('.case-card');
  const caseTrack = $('.case-grid');
  const carouselPrevious = $('.carousel-prev');
  const carouselNext = $('.carousel-next');
  const carouselStatus = $('.carousel-status');
  const mobileCases = window.matchMedia('(max-width: 720px)');
  let carouselFrame = 0;
  function visibleCases() { return caseCards.filter(card => !card.hidden); }
  function updateCarousel() {
    const cards = visibleCases();
    const edge = caseTrack.getBoundingClientRect().left;
    let current = 0;
    let nearest = Infinity;
    cards.forEach((card, index) => {
      const distance = Math.abs(card.getBoundingClientRect().left - edge);
      if (distance < nearest) { nearest = distance; current = index; }
    });
    carouselStatus.textContent = `${current + 1} / ${cards.length}`;
    carouselPrevious.disabled = current === 0;
    carouselNext.disabled = current >= cards.length - 1;
    carouselFrame = 0;
  }
  function moveCarousel(direction) {
    const cards = visibleCases();
    const edge = caseTrack.getBoundingClientRect().left;
    const current = cards.reduce((best, card, index) =>
      Math.abs(card.getBoundingClientRect().left - edge) <
      Math.abs(cards[best].getBoundingClientRect().left - edge) ? index : best, 0);
    const next = Math.max(0, Math.min(cards.length - 1, current + direction));
    if (next !== current) {
      const offset = cards[next].getBoundingClientRect().left - edge;
      caseTrack.scrollBy({ left: offset, behavior: motion.matches ? 'instant' : 'smooth' });
      track('case_carousel', { direction: direction > 0 ? 'next' : 'previous' });
    }
  }
  carouselPrevious.addEventListener('click', () => moveCarousel(-1));
  carouselNext.addEventListener('click', () => moveCarousel(1));
  caseTrack.addEventListener('scroll', () => {
    if (!carouselFrame) carouselFrame = requestAnimationFrame(updateCarousel);
  }, { passive: true });
  caseTrack.addEventListener('keydown', event => {
    if (!mobileCases.matches || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    moveCarousel(event.key === 'ArrowRight' ? 1 : -1);
  });
  window.addEventListener('resize', updateCarousel);
  updateCarousel();
  caseFilters.forEach(button => button.addEventListener('click', () => {
    const category = button.dataset.filter;
    let visible = 0;
    caseCards.forEach(card => {
      card.hidden = category !== 'all' && card.dataset.category !== category;
      if (!card.hidden) visible++;
    });
    caseFilters.forEach(filter => {
      filter.classList.toggle('is-active', filter === button);
      filter.setAttribute('aria-pressed', String(filter === button));
    });
    caseTrack.classList.toggle('is-filtered', category !== 'all');
    caseTrack.scrollTo({ left: 0, behavior: 'instant' });
    updateCarousel();
    $('#filter-status').textContent = `Показано кейсов: ${visible}`;
    track('case_filter', { category });
    updateScrollUI();
  }));

  // Case detail is a native modal: focus stays inside; Escape closes it.
  let casesCache;
  async function getCases() {
    if (casesCache) return casesCache;
    const response = await fetch(assetUrl('/cases.json?v=20260928a'));
    if (!response.ok) throw new Error('Cases unavailable');
    const data = await response.json();
    if (!Array.isArray(data) || !data.every(item => item.id && item.title)) throw new Error('Invalid cases');
    casesCache = data;
    return data;
  }
  const dialog = $('#case-dialog');
  let dialogRequest = 0;
  let previousOverflow = '';
  function text(id, value) { document.getElementById(id).textContent = value || ''; }
  async function openCase(id) {
    const request = ++dialogRequest;
    text('dialog-title', 'Загружаем кейс…');
    text('dialog-intro', '');
    $('#dialog-media').replaceChildren();
    $$('.dialog-facts, .dialog-result, .dialog-note, #dialog-cta, #dialog-media').forEach(el => { el.hidden = true; });
    if (!dialog.open) {
      previousOverflow = document.documentElement.style.overflow;
      dialog.showModal();
      document.documentElement.style.overflow = 'hidden';
    }
    dialog.scrollTop = 0;
    try {
      const cases = await getCases();
      if (request !== dialogRequest || !dialog.open) return;
      const item = cases.find(entry => entry.id === id);
      if (!item) throw new Error('Case unavailable');
      text('dialog-title', item.title);
      text('dialog-label', `${item.label}${item.status === 'Прототип' ? ' · ПРОТОТИП' : ''}`);
      text('dialog-intro', item.description);
      text('dialog-problem', item.problem);
      text('dialog-solution', item.solution);
      text('dialog-result', item.result);
      text('dialog-caption', item.caption);
      $('.dialog-note').textContent = item.note || 'Собственный рабочий проект. Результат относится к показанному процессу.';
      $('#dialog-points').replaceChildren(...item.points.map(point => {
        const li = document.createElement('li');
        li.textContent = point;
        return li;
      }));
      const media = $('#dialog-media');
      if (item.photo) {
        const image = document.createElement('img');
        image.src = assetUrl(item.photo);
        image.alt = item.alt;
        image.width = item.width;
        image.height = item.height;
        const fullSize = document.createElement('a');
        fullSize.href = assetUrl(item.photo);
        fullSize.target = '_blank';
        fullSize.rel = 'noopener';
        fullSize.textContent = 'Увеличить реальный интерфейс ↗';
        media.append(image, fullSize);
      } else {
        const flow = document.createElement('div');
        flow.className = 'diagram-flow';
        const labels = item.diagram || (item.id === 'price' ? ['Прайсы поставщиков', 'Правила и наценка', 'Фирменный прайс'] : ['Визит менеджера', 'Telegram-бот', 'Таблица и сводка']);
        labels.forEach((label, index) => {
          if (index) {
            const arrow = document.createElement('i');
            arrow.textContent = '→';
            arrow.setAttribute('aria-hidden', 'true');
            flow.append(arrow);
          }
          const node = document.createElement('span');
          node.textContent = label;
          flow.append(node);
        });
        media.append(flow);
      }
      $('#dialog-cta').dataset.service = `Задача по кейсу «${item.title}»`;
      $$('.dialog-facts, .dialog-result, .dialog-note, #dialog-cta, #dialog-media').forEach(el => { el.hidden = false; });
      track('case_open', { case: item.id });
    } catch (_) {
      if (!dialog.open || request !== dialogRequest) return;
      text('dialog-title', 'Не удалось загрузить подробности');
      text('dialog-intro', 'Закройте окно и попробуйте снова. Также можно написать нам на go@jaxgo.ru.');
    }
  }
  $$('[data-case]').forEach(button => button.addEventListener('click', () => openCase(button.dataset.case)));
  $('.dialog-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  dialog.addEventListener('close', () => {
    dialogRequest++;
    document.documentElement.style.overflow = previousOverflow;
    updateScrollUI();
  });
  $('#dialog-cta').addEventListener('click', event => {
    selectService(event.currentTarget.dataset.service);
    dialog.close();
    track('case_cta');
  });

  const selectedServices = new Set();
  const choiceButtons = $$('[data-choice]');
  function renderServices() {
    const value = [...selectedServices].join(', ');
    choiceButtons.forEach(button => button.setAttribute('aria-pressed', String(selectedServices.has(button.dataset.choice))));
    const status = $('#selected-service');
    status.hidden = !value;
    status.textContent = value ? `Выбрано: ${value}` : '';
    $('#clear-selection').hidden = !value;
  }
  function selectService(value) {
    selectedServices.clear();
    if (value) selectedServices.add(value);
    renderServices();
  }
  $('#clear-selection').addEventListener('click', () => selectService(''));
  choiceButtons.forEach(button => button.addEventListener('click', () => {
    const value = button.dataset.choice;
    if (selectedServices.has(value)) selectedServices.delete(value);
    else selectedServices.add(value);
    renderServices();
  }));
  $$('[data-service]').forEach(link => link.addEventListener('click', () => {
    if (link.dataset.service) selectService(link.dataset.service);
  }));

  const contactChannels = {
    telegram: /^https:\/\/t\.me\/[A-Za-z0-9_+/?=&-]+$/,
    max: /^https:\/\/(?:max\.ru|web\.max\.ru)\/[A-Za-z0-9_+/?=&%-]+$/,
  };
  $$('[data-contact-channel]').forEach(link => {
    const channel = link.dataset.contactChannel;
    const url = config[`${channel}Url`] || '';
    if (contactChannels[channel]?.test(url)) {
      link.href = url;
      link.hidden = false;
    }
  });
  const utmKeys = ['utm_source','utm_medium','utm_campaign','utm_content','utm_term'];
  const search = new URLSearchParams(location.search);
  const freshUtm = Object.fromEntries(utmKeys.filter(key => search.has(key)).map(key => [key, search.get(key).slice(0,120)]));
  try { if (Object.keys(freshUtm).length) sessionStorage.setItem('jaxgo_utm', JSON.stringify(freshUtm)); } catch (_) {}
  function getUtm() {
    try { return JSON.parse(sessionStorage.getItem('jaxgo_utm') || '{}'); } catch (_) { return freshUtm; }
  }
  function status(message, state = 'info') {
    formStatus.textContent = message;
    formStatus.dataset.state = state;
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (submitButton.disabled || !form.reportValidity()) return;
    const fields = new FormData(form);
    const name = String(fields.get('name') || '').trim();
    const contact = String(fields.get('contact') || '').trim();
    const message = String(fields.get('message') || '').trim();
    if (name.length < 2 || contact.length < 3) {
      status('Укажите имя и контакт, по которому с вами можно связаться.', 'error');
      return;
    }
    if (!selectedServices.size && message.length < 10) {
      status('Выберите хотя бы одно направление выше или опишите задачу — от 10 символов.', 'error');
      return;
    }
    const selected = [...selectedServices].join(', ');
    const body = { name, contact, message: `${selected ? `Направления: ${selected}.\n` : ''}${message || 'Хочу обсудить подходящее решение.'}`, consent: fields.get('consent') === 'yes', website: String(fields.get('website') || ''), utm: getUtm(), page: location.pathname };
    submitButton.disabled = true;
    submitButton.setAttribute('aria-busy','true');
    submitButton.querySelector('span').textContent = 'Отправляем…';
    status('Доставляем заявку. Пожалуйста, подождите.');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(assetUrl('/api/lead.php'), { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body), signal:controller.signal });
      const result = await response.json();
      if (!response.ok || result.ok !== true) throw new Error('Delivery failed');
      form.reset();
      selectService('');
      status('Заявка отправлена. Свяжемся с вами по указанному контакту.', 'success');
      track('form_success');
    } catch (_) {
      status('Не удалось отправить заявку. Ваш текст сохранён — попробуйте ещё раз или напишите на go@jaxgo.ru.', 'error');
      track('form_error');
    } finally {
      clearTimeout(timeout);
      submitButton.disabled = false;
      submitButton.removeAttribute('aria-busy');
      submitButton.querySelector('span').textContent = 'Отправить заявку';
    }
  });
})();
