/* Apply before styles paint; keep phone layout through rotation and keyboard resize. */
(() => {
  const narrow = window.matchMedia('(max-width: 760px)');
  const compactTouch = window.matchMedia('(max-width: 1100px) and (pointer: coarse)');
  const phone = navigator.userAgentData?.mobile === true ||
    /iPhone|iPod|Android.*Mobile|Windows Phone/i.test(navigator.userAgent);
  function update() {
    document.documentElement.dataset.ui = phone || narrow.matches || compactTouch.matches ? 'mobile' : 'desktop';
  }
  update();
  for (const query of [narrow, compactTouch]) {
    if (query.addEventListener) query.addEventListener('change', update);
    else query.addListener(update);
  }
})();
