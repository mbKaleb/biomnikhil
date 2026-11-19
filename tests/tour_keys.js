// Runs the real app/static/tour_layout.js and tour.js against a small stand-in for the page,
// presses one key the given number of times, and prints what the tour showed
// after each press. Driven by tests/test_tour_keys.py.
//
//     node tests/tour_keys.js " " 5
const fs = require("fs");
const path = require("path");
const vm = require("vm");

// Every element the tour looks up gets its own place on a grid, so the
// layout has distinct controls to point at.
let placedElements = 0;
function nextBox() {
  const index = placedElements;
  placedElements += 1;
  const left = 40 + (index % 10) * 120;
  const top = 100 + Math.floor(index / 10) * 90;
  return { left, top, right: left + 100, bottom: top + 40 };
}

class FakeElement {
  constructor() {
    this.id = "";
    this.textContent = "";
    this.hidden = false;
    this.offsetParent = {};
    this.removed = false;
    this.box = nextBox();
    this.style = { removeProperty() {} };
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  append() {}
  replaceChildren() {}
  setAttribute() {}
  addEventListener() {}
  focus() {}
  contains() {
    return false;
  }
  closest() {
    return null;
  }
  getClientRects() {
    return [this.box];
  }
  remove() {
    this.removed = true;
  }
  getBoundingClientRect() {
    return this.box;
  }
}

const createdElements = [];
function createElement() {
  const element = new FakeElement();
  createdElements.push(element);
  return element;
}

const pageElements = new Map();
const queriedElements = new Map();
function getElementById(id) {
  if (!pageElements.has(id)) pageElements.set(id, new FakeElement());
  return pageElements.get(id);
}

function findCreated(id) {
  for (const element of createdElements) {
    if (element.id === id) return element;
  }
  return null;
}

const keydownListeners = new Set();
const fakeDocument = {
  body: Object.assign(new FakeElement(), { hasAttribute: () => false }),
  createElement,
  createElementNS: createElement,
  getElementById,
  querySelectorAll(selector) {
    if (!queriedElements.has(selector)) queriedElements.set(selector, new FakeElement());
    return [queriedElements.get(selector)];
  },
  querySelector(selector) {
    if (!queriedElements.has(selector)) queriedElements.set(selector, new FakeElement());
    return queriedElements.get(selector);
  },
  documentElement: { scrollHeight: 900 },
  addEventListener(type, listener) {
    if (type === "keydown") keydownListeners.add(listener);
  },
  removeEventListener(type, listener) {
    if (type === "keydown") keydownListeners.delete(listener);
  },
};

function pressKey(key) {
  const event = {
    key,
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
  };
  for (const listener of [...keydownListeners]) listener(event);
  return event;
}

const page = {
  document: fakeDocument,
  window: {
    innerWidth: 1280,
    innerHeight: 800,
    addEventListener() {},
    removeEventListener() {},
    scrollTo() {},
  },
  console,
  getComputedStyle: () => ({ position: "static", zIndex: "auto" }),
  setTimeout: () => 0,
  clearTimeout() {},
  localStorage: { getItem: () => null, setItem() {} },
  ResizeObserver: class {
    observe() {}
    disconnect() {}
  },
  tabKeyPrefix: "⌘",
  traceKeyLabel: "⌘J",
  closeSidebar() {},
  activateTab() {},
};

// The page loads these two scripts in this order, into one shared scope.
const context = vm.createContext(page);
for (const name of ["tour_layout.js", "tour.js"]) {
  const scriptPath = path.join(__dirname, "..", "app", "static", name);
  vm.runInContext(fs.readFileSync(scriptPath, "utf8"), context, { filename: name });
}

const key = process.argv[2];
const pressCount = Number(process.argv[3]);
const presses = [];
for (let pressNumber = 0; pressNumber < pressCount; pressNumber += 1) {
  const event = pressKey(key);
  presses.push({
    progress: findCreated("tour-progress").textContent,
    defaultPrevented: event.defaultPrevented,
    tourOpen: !findCreated("tour").removed,
  });
}
console.log(JSON.stringify(presses));
