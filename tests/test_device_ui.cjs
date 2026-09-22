const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../petpark/webstatic/device-ui.js'), 'utf8');
function session({width = 1440, coarse = false, ua = 'Desktop', mobile, legacy = false} = {}) {
  const queries = [];
  const document = {documentElement: {dataset: {}}};
  const context = {document, navigator: {userAgent: ua, userAgentData: mobile === undefined ? undefined : {mobile}},
    window: {matchMedia(query) {
      const q = {matches: width <= (query.includes('1100') ? 1100 : 760) && (!query.includes('coarse') || coarse)};
      q[legacy ? 'addListener' : 'addEventListener'] = (...args) => {q.update = args.at(-1);};
      queries.push(q);
      return q;
    }}};
  vm.runInNewContext(source, context);
  return {mode: () => document.documentElement.dataset.ui, resize(w) {
    queries[0].matches = w <= 760;
    queries[1].matches = w <= 1100 && coarse;
    queries.forEach(q => q.update());
  }};
}
assert.equal(session().mode(), 'desktop');
assert.equal(session({width:390}).mode(), 'mobile');
assert.equal(session({width:844,ua:'Mozilla iPhone'}).mode(), 'mobile');
assert.equal(session({width:915,ua:'Mozilla Android 14 Mobile'}).mode(), 'mobile');
assert.equal(session({width:1000,mobile:true}).mode(), 'mobile');
assert.equal(session({width:1024,coarse:true}).mode(), 'mobile');
assert.equal(session({width:1440,coarse:true}).mode(), 'desktop');
const desktop = session(); desktop.resize(390); assert.equal(desktop.mode(),'mobile'); desktop.resize(1440); assert.equal(desktop.mode(),'desktop');
const phone = session({width:390,ua:'iPhone',legacy:true}); phone.resize(844); assert.equal(phone.mode(),'mobile');
console.log('9 device detection and resize checks passed');
