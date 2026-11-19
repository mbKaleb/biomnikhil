// ---- first-run tour: dims the page and walks through the groups of
// controls, lighting each group and drawing a line from every control to a
// short label. Shown once, then remembered.
//
// A page of the tour only says what to point at. Where each label goes and how
// its line runs are worked out from the live page every time it is drawn, by
// tour_layout.js: it finds the controls and text that must stay readable and
// places labels and lines around them.
const TOUR_SEEN_KEY = "tour-seen";
const TOUR_ATTACH = { id: "composer-attach", title: "Attach", text: "Add files from your computer or User Files." };
const TOUR_PROMPT = { id: "prompt", title: "Your task", text: "Describe what you want the agent to do." };
const TOUR_RUN = { id: "run", title: "Run", text: "Send the task to the agent." };
const TOUR_MODELS = { id: "model-picker", title: "Models", text: "Choose which model runs the task." };
const TOUR_STAGES = [
  { selector: '[data-feature="context"], #lab-context-edit', title: "Lab notes", text: "Adds your saved protocols to every prompt. The pen edits them." },
  { selector: '[data-feature="preprocess"]', title: "Clean data", text: "Checks attached files and saves cleaned copies first." },
  { selector: '[data-feature="assay"]', title: "Test analysis", text: "Plants known effects to see what the analysis can find." },
  { selector: '[data-feature="review"]', title: "Review answer", text: "Another agent tries to prove the answer wrong." },
];
const TOUR_TABS = [
  { selector: '.tabs button[data-tab="task"]', title: `Task (${tabKeyPrefix}1)`, text: "Your chat with the agent." },
  { selector: '.tabs button[data-tab="attachments"]', title: `Files (${tabKeyPrefix}2)`, text: "Files attached to this chat. Locked once a task is sent." },
  { selector: '.tabs button[data-tab="outputs"]', title: `Outputs (${tabKeyPrefix}3)`, text: "Files the agent made in this chat." },
  { selector: '.tabs button[data-tab="citations"]', title: `Citations (${tabKeyPrefix}4)`, text: "Sources the agent saw or cited." },
  { selector: '.tabs button[data-tab="viewer"]', title: `Viewer (${tabKeyPrefix}5)`, text: "Opens webpages the agent built." },
  { selector: '.tabs button[data-tab="taskspend"]', title: `Spending (${tabKeyPrefix}6)`, text: "What this chat has cost." },
];
const TOUR_HEADER_STEPS = [
  { id: "leads-btn", title: "Leads", text: "Findings to follow up on and track." },
  { id: "files-btn", title: "User files", text: "Upload files the agent can read and use." },
  { id: "integrity-btn", title: "Integrity", text: "Health checks for data and agent." },
  { id: "account-spend-btn", title: "Account spending", text: "AWS costs by day and by service." },
  { id: "user-link", title: "Settings", text: "Version, updates, setup and cleanup." },
];
const TOUR_TRACE = { id: "trace-btn", title: "Agent trace", text: `Watch the agent's steps live as it works (${traceKeyLabel}).` };
const TOUR_MENU = { id: "menu-btn", title: "Chats", text: "Switch or start chats (⌘B)." };
const TOUR_HELP = { id: "help-btn", title: "See this again", text: "Click ? any time to replay this tour." };

// Each page lists what it points at. The order is the order of the plain list
// shown in a window too narrow for lines, and of the lines drawing in.
const TOUR_PAGES = [
  { steps: [TOUR_ATTACH, TOUR_PROMPT, TOUR_MODELS, TOUR_RUN] },
  { steps: TOUR_STAGES },
  { steps: TOUR_TABS },
  { steps: [TOUR_MENU, ...TOUR_HEADER_STEPS, TOUR_TRACE] },
  { steps: [TOUR_HELP] },
];

const TOUR_RADIUS = 12;
const TOUR_STAGGER_SECONDS = 0.1;
// Below this width there is too little room for labels beside their controls,
// so they become a list.
const TOUR_LIST_WIDTH = 900;
// Widths a label may be squeezed to when the room is tight, after its own.
const TOUR_LABEL_WIDTHS = [null, 200, 150];
// A layout whose lines bend more than this, or cross more than once, is a
// tangle, and a plain list reads better.
const TOUR_MAX_BENDS = 3;
const TOUR_MAX_CROSSINGS = 1;
// What a label has to keep clear of: controls, and text.
const TOUR_CONTROL_SELECTOR = 'button, a[href], input, select, textarea, summary, [role="button"], [role="tab"], [role="menuitem"]';
const TOUR_TEXT_SELECTOR = "h1, h2, h3, h4, p, li, pre, blockquote, th, td, label";
const TOUR_MAX_SALIENT = 400;
// Anything bigger than this share of the window is a container, not content.
const TOUR_MAX_AREA_SHARE = 0.35;
// The page has moved when something is further than this from where it was.
const TOUR_MOVED_PIXELS = 3;
// How long layout changes must stop before the tour lays itself out again.
const TOUR_REDRAW_DELAY_MS = 120;
// The layer lit controls are lifted to (.tour-lifted in the page's CSS), just
// above the dimming overlay.
const TOUR_LIFT_Z = 101;

function tourSeen() {
  if (document.body.hasAttribute("data-always-tour")) return false;
  try { return localStorage.getItem(TOUR_SEEN_KEY) === "1"; } catch { return true; }
}

// ---- reading the page ----
function tourPlainRect(box) {
  return { left: box.left, top: box.top, right: box.right, bottom: box.bottom };
}

// The part of an element inside the window, or null when little or none is.
function tourClippedRect(element) {
  const box = element.getBoundingClientRect();
  const left = Math.max(0, box.left);
  const top = Math.max(0, box.top);
  const right = Math.min(window.innerWidth, box.right);
  const bottom = Math.min(window.innerHeight, box.bottom);
  if (right - left < 2 || bottom - top < 2) return null;
  return { left, top, right, bottom };
}

// Not display: none, not hidden, and taking up room. Opacity isn't looked at:
// the empty chat fades its composer in, and the tour starts before that ends.
// (An element with position: fixed has no offsetParent, so that can't tell.)
function tourIsShown(element) {
  if (typeof element.checkVisibility === "function") {
    return element.checkVisibility({ checkVisibilityCSS: true, visibilityProperty: true });
  }
  return element.getClientRects().length > 0;
}

// The page's controls that exist and take up room, wherever they are.
function tourVisibleTargets(step) {
  let candidates;
  if (step.id) candidates = [document.getElementById(step.id)];
  else candidates = [...document.querySelectorAll(step.selector)];
  const visible = [];
  for (const element of candidates) {
    if (!element) continue;
    if (!tourIsShown(element)) continue;
    visible.push(element);
  }
  return visible;
}

function tourTouchesAny(element, litElements) {
  for (const lit of litElements) {
    if (lit === element || lit.contains(element) || element.contains(lit)) return true;
  }
  return false;
}

function tourRectInside(outer, inner) {
  return inner.left >= outer.left && inner.top >= outer.top && inner.right <= outer.right && inner.bottom <= outer.bottom;
}

// Text lying inside something else, or one control inside another, is already
// covered by it; a control inside text is kept, since labels may cover text
// but not controls.
function tourDropNested(items) {
  const kept = [];
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    let nested = false;
    for (let otherIndex = 0; otherIndex < items.length; otherIndex += 1) {
      if (otherIndex === index) continue;
      const other = items[otherIndex];
      if (item.kind === "control" && other.kind !== "control") continue;
      if (!tourRectInside(other.rect, item.rect)) continue;
      const identical = tourRectInside(item.rect, other.rect);
      if (!identical || otherIndex < index) {
        nested = true;
        break;
      }
    }
    if (!nested) kept.push(item);
  }
  return kept;
}

// Everything visible that isn't one of the lit controls: what the tour has to
// keep readable, found fresh from the page each time.
function tourCollectSalient(litElements) {
  const windowArea = window.innerWidth * window.innerHeight;
  const found = [];
  const groups = [[TOUR_CONTROL_SELECTOR, "control"], [TOUR_TEXT_SELECTOR, "content"]];
  for (const [selector, kind] of groups) {
    for (const element of document.querySelectorAll(selector)) {
      if (found.length >= TOUR_MAX_SALIENT) break;
      if (element.closest("#tour")) continue;
      if (tourTouchesAny(element, litElements)) continue;
      if (!tourIsShown(element)) continue;
      const rect = tourClippedRect(element);
      if (!rect) continue;
      const area = (rect.right - rect.left) * (rect.bottom - rect.top);
      if (area > windowArea * TOUR_MAX_AREA_SHARE) continue;
      found.push({ rect, kind });
    }
  }
  return tourDropNested(found);
}

// What a page looks like right now, without drawing anything.
function tourReadPage(tour, page) {
  const entries = [];
  let offscreen = 0;
  const litElements = [];
  page.steps.forEach((step, order) => {
    const elements = [];
    const anchors = [];
    const rendered = tourVisibleTargets(step);
    for (const element of rendered) {
      const rect = tourClippedRect(element);
      if (!rect) continue; // out of the window: nothing to point at
      elements.push(element);
      anchors.push(rect);
      litElements.push(element);
    }
    if (rendered.length === 0) return; // a control this user doesn't have
    if (elements.length === 0) {
      offscreen += 1; // there, but scrolled out of the window
      return;
    }
    entries.push({ step, order, elements, anchors });
  });
  const salient = tourCollectSalient(litElements);
  const controls = [tourPlainRect(tour.controls.getBoundingClientRect())];
  const viewport = { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };

  // Every number that decides the layout, so a redraw can tell nothing moved.
  const signature = [window.innerWidth, window.innerHeight];
  const rects = [...controls];
  for (const entry of entries) rects.push(...entry.anchors);
  for (const item of salient) rects.push(item.rect);
  for (const rect of rects) signature.push(rect.left, rect.top, rect.right, rect.bottom);
  return { entries, offscreen, salient, controls, viewport, signature };
}

function tourSameSignature(first, second) {
  if (!first || !second || first.length !== second.length) return false;
  for (let index = 0; index < first.length; index += 1) {
    if (Math.abs(first[index] - second[index]) > TOUR_MOVED_PIXELS) return false;
  }
  return true;
}

// ---- labels ----
function tourBuildLabel(step, order) {
  const label = document.createElement("div");
  label.className = "tour-label";
  label.style.animationDelay = `${0.3 + order * TOUR_STAGGER_SECONDS}s`;
  const title = document.createElement("b");
  title.textContent = step.title;
  label.append(title, step.text);
  return label;
}

// Reads the sizes a label can take: its own, then narrower and taller ones for
// when the room is tight. Done in the page, since they depend on the text.
function tourMeasureSizes(tour, label) {
  label.style.left = "0px";
  label.style.top = "0px";
  label.style.visibility = "hidden";
  tour.stage.append(label);
  const sizes = [];
  for (const maxWidth of TOUR_LABEL_WIDTHS) {
    if (maxWidth === null) label.style.removeProperty("max-width");
    else label.style.maxWidth = `${maxWidth}px`;
    const box = label.getBoundingClientRect();
    const previous = sizes[sizes.length - 1];
    const repeats = previous && previous.width === box.width && previous.height === box.height;
    if (!repeats) sizes.push({ width: box.width, height: box.height, maxWidth });
  }
  label.style.removeProperty("max-width");
  label.style.removeProperty("visibility");
  return sizes;
}

function tourLiftTargets(tour, elements, order) {
  for (const element of elements) {
    element.style.animationDelay = `${order * TOUR_STAGGER_SECONDS}s`;
    element.classList.add("tour-lifted");
    tour.lifted.push(element);
  }
}

// A lit control only shows above the dimming when nothing around it traps it
// below: a positioned ancestor with a z-index (a sticky bar, say) is painted
// as one layer, under the overlay, with everything in it. Raises each such
// ancestor above the overlay, undone when the drawing is cleared. Returns the
// regions of those layers, since they now cover whatever lies beneath and a
// label placed there would be hidden. Run after lifting, so that the page's
// own rules for lifted controls (see `header:has(.tour-lifted)`) have applied.
function tourRaiseTrapped(tour, litElements) {
  const windowArea = window.innerWidth * window.innerHeight;
  const handled = new Set();
  const regions = [];
  for (const element of litElements) {
    for (let node = element.parentElement; node && node !== document.body; node = node.parentElement) {
      if (handled.has(node)) continue;
      handled.add(node);
      const style = getComputedStyle(node);
      if (style.position === "static") continue;
      const isLayer = style.zIndex !== "auto" || style.position === "sticky" || style.position === "fixed";
      if (!isLayer) continue;
      const rect = tourClippedRect(node);
      if (!rect) continue;
      const area = (rect.right - rect.left) * (rect.bottom - rect.top);
      if (area > windowArea * TOUR_MAX_AREA_SHARE) continue; // a layer as big as the page isn't ours to raise
      const layer = style.zIndex === "auto" ? 0 : parseInt(style.zIndex, 10);
      if (layer < TOUR_LIFT_Z) {
        tour.raised.push({ node, zIndex: node.style.zIndex });
        node.style.zIndex = String(TOUR_LIFT_Z);
      }
      regions.push(rect);
    }
  }
  return regions;
}

// Lifts every lit control of a page, raises what traps them, and returns the
// regions now covering the page.
function tourLiftEntries(tour, entries) {
  const litElements = [];
  for (const entry of entries) {
    tourLiftTargets(tour, entry.elements, entry.order);
    litElements.push(...entry.elements);
  }
  return tourRaiseTrapped(tour, litElements);
}

function tourAddLine(tour, points, order) {
  const path = document.createElementNS(tour.svgNs, "path");
  path.setAttribute("d", TourLayout.roundedPath(points, TOUR_RADIUS));
  path.setAttribute("pathLength", "1"); // so the draw-in animation fits a line of any length
  path.style.animationDelay = `${order * TOUR_STAGGER_SECONDS}s`;
  const dot = document.createElementNS(tour.svgNs, "circle");
  dot.setAttribute("cx", points[0].x);
  dot.setAttribute("cy", points[0].y);
  dot.setAttribute("r", 2.5);
  tour.svg.append(path, dot);
}

// ---- drawing ----
// Lays the page's labels out and draws them with their lines. Returns false,
// drawing nothing, when they can't be laid out soundly.
function tourDrawDiagram(tour, reading) {
  const byId = new Map();
  const steps = [];
  for (const entry of reading.entries) {
    const id = String(entry.order);
    entry.label = tourBuildLabel(entry.step, entry.order);
    entry.sizes = tourMeasureSizes(tour, entry.label);
    byId.set(id, entry);
    steps.push({ id, anchors: entry.anchors, sizes: entry.sizes });
  }
  const scene = { viewport: reading.viewport, controls: reading.controls, salient: reading.salient, raised: reading.raised, steps };
  const solution = TourLayout.solve(scene);
  if (!solution.ok) {
    const failed = byId.get(solution.failedId);
    console.warn(`tour: no room for the "${failed ? failed.step.title : "?"}" label`);
    return false;
  }
  // The layout is checked again before it is drawn, so a flaw in it shows as
  // a plain list and never as lines through the page.
  const problems = TourLayout.validate(scene, solution);
  if (problems.length > 0) {
    console.warn(`tour: the layout broke a rule, so a list is shown instead: ${problems.join("; ")}`);
    return false;
  }

  const tidiness = TourLayout.quality(solution);
  if (tidiness.mostBends > TOUR_MAX_BENDS || tidiness.crossings > TOUR_MAX_CROSSINGS) {
    console.warn(`tour: the layout is too tangled (${tidiness.mostBends} bends in a line, ${tidiness.crossings} crossings), so a list is shown instead`);
    return false;
  }

  for (const placement of solution.placements) {
    const entry = byId.get(placement.id);
    const size = entry.sizes[placement.sizeIndex];
    entry.label.style.left = `${placement.rect.left}px`;
    entry.label.style.top = `${placement.rect.top}px`;
    if (size.maxWidth === null) entry.label.style.removeProperty("max-width");
    else entry.label.style.maxWidth = `${size.maxWidth}px`;
    tourAddLine(tour, placement.points, entry.order);
  }
  return true;
}

function tourDrawList(tour, page) {
  const list = document.createElement("div");
  list.className = "tour-list";
  let lowestTargetBottom = 0;
  const litElements = [];
  page.steps.forEach((step, order) => {
    const elements = tourVisibleTargets(step);
    if (elements.length === 0) return;
    tourLiftTargets(tour, elements, order);
    litElements.push(...elements);
    list.append(tourBuildLabel(step, order));
    for (const element of elements) {
      lowestTargetBottom = Math.max(lowestTargetBottom, element.getBoundingClientRect().bottom);
    }
  });
  tourRaiseTrapped(tour, litElements);
  tour.stage.append(list);
  // Controls near the top of the page would otherwise sit on the first labels.
  const listBox = list.getBoundingClientRect();
  const targetsAreAboveMiddle = lowestTargetBottom < window.innerHeight / 2;
  if (targetsAreAboveMiddle && lowestTargetBottom > listBox.top) {
    list.style.top = `${lowestTargetBottom + 12}px`;
  }
}

// A page whose controls are all hidden has nothing to show, so it's skipped.
function tourPageHasTargets(page) {
  for (const step of page.steps) {
    if (tourVisibleTargets(step).length) return true;
  }
  return false;
}

function startTour() {
  const overlay = document.createElement("div");
  overlay.id = "tour";
  const svgNs = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNs, "svg");
  const stage = document.createElement("div");
  overlay.append(svg, stage);
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", "Tour of the app");
  const tour = { svgNs, svg, stage, lifted: [], raised: [], shown: null };
  const pages = TOUR_PAGES.filter(tourPageHasTargets);
  let pageIndex = 0;

  const controls = document.createElement("div");
  controls.id = "tour-controls";
  const progress = document.createElement("span");
  progress.id = "tour-progress";
  const back = document.createElement("button");
  back.id = "tour-back";
  back.textContent = "Back";
  const next = document.createElement("button");
  next.id = "tour-done";
  controls.append(progress, back, next);
  overlay.append(controls);
  tour.controls = controls;

  const clearDrawing = () => {
    svg.replaceChildren();
    stage.replaceChildren();
    for (const element of tour.lifted) {
      element.classList.remove("tour-lifted");
      element.style.removeProperty("animation-delay");
    }
    tour.lifted.length = 0;
    for (const { node, zIndex } of tour.raised) node.style.zIndex = zIndex;
    tour.raised.length = 0;
  };

  const draw = () => {
    const page = pages[pageIndex];
    progress.textContent = `${pageIndex + 1} of ${pages.length}`;
    back.hidden = pageIndex === 0;
    next.textContent = pageIndex === pages.length - 1 ? "Got it" : "Next";

    // Drawn again for the same page only because the layout moved, so what is
    // already on screen shouldn't animate in a second time.
    const redrawing = tour.shown !== null && tour.shown.pageIndex === pageIndex;
    overlay.classList.toggle("settled", redrawing);

    let drew = false;
    let signature = [window.innerWidth, window.innerHeight];
    if (window.innerWidth >= TOUR_LIST_WIDTH) {
      const before = tourReadPage(tour, page);
      signature = before.signature;
      // Lines can't point at a control that is out of the window, so the page
      // is listed instead.
      if (before.offscreen > 0) before.entries.length = 0;
      if (redrawing && tour.shown.diagram && tourSameSignature(tour.shown.signature, signature)) return;
      // Lift first, so that what the lifting raises is known, and measure with
      // the lift's pop-in paused: it scales the controls while it runs.
      clearDrawing();
      overlay.classList.add("measuring");
      const raised = tourLiftEntries(tour, before.entries);
      const reading = tourReadPage(tour, page);
      reading.raised = raised;
      drew = before.entries.length > 0 && tourDrawDiagram(tour, reading);
      overlay.classList.remove("measuring");
      if (!drew) clearDrawing();
    } else if (redrawing && !tour.shown.diagram && tourSameSignature(tour.shown.signature, signature)) {
      return;
    }
    // Too narrow, or a label had nowhere to go: a list always fits.
    if (!drew) {
      clearDrawing();
      tourDrawList(tour, page);
    }
    tour.shown = { pageIndex, diagram: drew, signature };
  };

  // Layout changes arrive in bursts (a window being dragged, an animation
  // moving the composer), and laying out takes a moment, so the redraw waits
  // until they stop.
  let redrawTimer = null;
  const queueRedraw = () => {
    if (redrawTimer !== null) clearTimeout(redrawTimer);
    redrawTimer = setTimeout(() => {
      redrawTimer = null;
      draw();
    }, TOUR_REDRAW_DELAY_MS);
  };

  // The empty-chat screen renders after this script runs and moves the
  // composer, so the drawing follows the layout instead of measuring once.
  const layoutObserver = new ResizeObserver(queueRedraw);

  const finish = () => {
    try { localStorage.setItem(TOUR_SEEN_KEY, "1"); } catch {}
    layoutObserver.disconnect();
    if (redrawTimer !== null) clearTimeout(redrawTimer);
    window.removeEventListener("resize", queueRedraw);
    window.removeEventListener("scroll", queueRedraw, true);
    clearDrawing();
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    document.getElementById("help-btn")?.focus();
  };
  const goForward = () => {
    if (pageIndex === pages.length - 1) {
      finish();
      return;
    }
    pageIndex += 1;
    draw();
  };
  const goBack = () => {
    if (pageIndex === 0) return;
    pageIndex -= 1;
    draw();
    next.focus();
  };
  const onKey = (event) => {
    if (event.key === "Escape") {
      finish();
    } else if (event.key === "Enter" || event.key === " " || event.key === "ArrowRight") {
      // Stops the key from also clicking the focused button and skipping a step.
      event.preventDefault();
      goForward();
    } else if (event.key === "ArrowLeft") {
      goBack();
    }
  };
  next.addEventListener("click", goForward);
  back.addEventListener("click", goBack);
  document.addEventListener("keydown", onKey);

  if (pages.length === 0) return;
  // In the page first, so labels can be measured as they're placed.
  document.body.append(overlay);
  draw();
  layoutObserver.observe(document.body);
  layoutObserver.observe(document.getElementById("messages"));
  layoutObserver.observe(document.getElementById("composer-dock"));
  window.addEventListener("resize", queueRedraw);
  // Scrolling inside the page moves the controls under the overlay.
  window.addEventListener("scroll", queueRedraw, true);
  // Late fonts change how big the labels are.
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(queueRedraw);
  next.focus();
}

// The tour points at the task screen, so a replay returns there first. The
// page sits at its end so the composer is where the tour expects it.
function replayTour() {
  closeSidebar();
  activateTab("task");
  window.scrollTo(0, document.documentElement.scrollHeight);
  startTour();
}

document.getElementById("help-btn").addEventListener("click", replayTour);

if (!tourSeen()) startTour();
