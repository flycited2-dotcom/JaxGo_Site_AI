/* Article navigation. Analytics runs only when the production counter is set. */
(() => {
  const articleId = document.querySelector('[data-article-id]')?.dataset.articleId;
  const config = window.JAXGO_CONFIG || {};
  if (articleId) {
    const keys = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'];
    const query = new URLSearchParams(location.search);
    const utm = Object.fromEntries(keys.filter(key => query.has(key)).map(key => [key, query.get(key).slice(0, 120)]));
    try { if (Object.keys(utm).length) sessionStorage.setItem('jaxgo_utm', JSON.stringify(utm)); } catch {}
    document.addEventListener('click', event => {
      const link = event.target.closest('a[href]');
      if (!link) return;
      const url = new URL(link.href, location.href);
      let goal;
      if (url.origin === location.origin && url.hash === '#contacts') goal = 'article_cta';
      else if (url.origin === location.origin && url.hash === '#cases') goal = 'article_case_click';
      else if (link.closest('.related')) goal = 'article_related_click';
      if (goal && Number(config.metrikaId) > 0 && typeof window.ym === 'function') {
        window.ym(Number(config.metrikaId), 'reachGoal', goal, { article_id: articleId, target: url.pathname + url.hash });
      }
    });
  }
  const toc = document.querySelector('.toc');
  if (toc && window.matchMedia('(max-width:700px)').matches) toc.open = false;
  const search = document.querySelector('#article-search');
  if (!search) return;
  const grid = document.querySelector('.article-grid');
  const pageCards = [...grid.querySelectorAll('.article-card')];
  const otherCards = [...(document.querySelector('#other-articles')?.content.querySelectorAll('.article-card') || [])];
  const pagination = document.querySelector('.pagination');
  const filters = [...document.querySelectorAll('[data-filter]')];
  let category = 'all';
  const normalize = value => value.toLocaleLowerCase('ru').replaceAll('ё', 'е').trim();
  function update() {
    const query = normalize(search.value);
    const filtering = query !== '' || category !== 'all';
    const visible = filtering ? [...pageCards, ...otherCards].filter(card =>
      (category === 'all' || card.dataset.category === category) && normalize(card.textContent).includes(query)
    ) : pageCards;
    grid.replaceChildren(...visible);
    if (pagination) pagination.hidden = filtering;
    document.querySelector('#result-count').textContent = `Показано статей: ${visible.length}`;
    document.querySelector('#empty-results').hidden = visible.length !== 0;
    for (const filter of filters) {
      const active = filter.dataset.filter === category;
      filter.classList.toggle('active', active);
      filter.setAttribute('aria-pressed', String(active));
    }
  }
  filters.forEach(filter => filter.addEventListener('click', () => {
    category = filter.dataset.filter;
    update();
  }));
  search.addEventListener('input', update);
})();
