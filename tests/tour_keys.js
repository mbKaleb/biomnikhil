// Runs the real app/static/tour.js against a small stand-in for the page,
// presses one key the given number of times, and prints what the tour showed
// after each press. Driven by tests/test_tour_keys.py.
//
//     node tests/tour_keys.js " " 5
const fs = require("fs");
const path = require("path");
const vm = require("vm");

class FakeElement {
  constructor() {
    this.id = "";
    this.textContent = "";
    this.hidden = false;
    this.offsetParent = {};
    this.removed = false;
    this.style = { removeProperty() {} };
    this.classList = { add() {}, remove() {} };
  }
  append() {}
  replaceChildren() {}
  setAttribute() {}
  addEventListener() {}
  focus() {}
  remove() {
    this.removed = true;
  }
  getBoundingClientRect() {
    return { left: 0, right: 100, top: 0, bottom: 40 };
  }
}

const createdElements = [];
function createElement() {
  const element = new FakeElement();
  createdElements.push(element);
  return element;
}

const pageElements = new Map();
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
  querySelectorAll: () => [new FakeElement()],
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
  window: { innerWidth: 1280, innerHeight: 800 },
  localStorage: { getItem: () => null, setItem() {} },
  ResizeObserver: class {
    observe() {}
    disconnect() {}
  },
  tabKeyPrefix: "⌘",
  closeSidebar() {},
  activateTab() {},
};

const tourPath = path.join(__dirname, "..", "app", "static", "tour.js");
vm.runInNewContext(fs.readFileSync(tourPath, "utf8"), page);

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
