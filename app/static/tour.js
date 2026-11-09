// ---- first-run tour: dims the page and walks through the groups of
// controls, lighting each group and drawing lines to a short label for every
// control. Shown once, then remembered.
const TOUR_SEEN_KEY = "tour-seen";
const TOUR_ATTACH = { id: "composer-attach", title: "Attach", text: "Add files from your computer or User Files." };
const TOUR_PROMPT = { id: "prompt", title: "Your task", text: "Describe what you want the agent to do." };
const TOUR_RUN = { id: "run", title: "Run", text: "Send the task to the agent.", labelOnLeft: true };
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
  { id: "files-btn", title: "User files", text: "Upload files the agent can read and use." },
  { id: "integrity-btn", title: "Integrity", text: "Health checks for data and agent." },
  { id: "account-spend-btn", title: "Account spending", text: "AWS costs by day and by service." },
  { id: "user-link", title: "Settings", text: "Version, updates, setup and cleanup." },
];
const TOUR_TRACE = { id: "trace-btn", title: "Agent trace", text: `Watch the agent's steps live as it works (${traceKeyLabel}).` };
const TOUR_MENU = { id: "menu-btn", title: "Chats", text: "Switch or start chats (⌘B)." };
const TOUR_HELP = { id: "help-btn", title: "See this again", text: "Click ? any time to replay this tour." };
const TOUR_RADIUS = 12;
const TOUR_ROW_GAP = 56;
const TOUR_STAIR_GAP = 60;
const TOUR_STAGGER_SECONDS = 0.1;
// Below this width the lines and labels can't fit, so labels become a list.
const TOUR_LIST_WIDTH = 900;

function tourSeen() {
  if (document.body.hasAttribute("data-always-tour")) return false;
  try { return localStorage.getItem(TOUR_SEEN_KEY) === "1"; } catch { return true; }
}

function tourVisibleTargets(step) {
  const candidates = step.id
    ? [document.getElementById(step.id)]
    : [...document.querySelectorAll(step.selector)];
  const visible = [];
  for (const element of candidates) {
    if (element && element.offsetParent !== null) visible.push(element);
  }
  return visible;
}

function tourCombinedBox(elements) {
  const boxes = elements.map((element) => element.getBoundingClientRect());
  const left = Math.min(...boxes.map((box) => box.left));
  const right = Math.max(...boxes.map((box) => box.right));
  const top = Math.min(...boxes.map((box) => box.top));
  const bottom = Math.max(...boxes.map((box) => box.bottom));
  return { left, right, top, bottom, centerX: (left + right) / 2, centerY: (top + bottom) / 2 };
}

function tourLiftTargets(tour, step, order) {
  const targets = tourVisibleTargets(step);
  for (const target of targets) {
    target.style.animationDelay = `${order * TOUR_STAGGER_SECONDS}s`;
    target.classList.add("tour-lifted");
    tour.lifted.push(target);
  }
  return targets;
}

function tourAddLine(tour, pathData, order) {
  const path = document.createElementNS(tour.svgNs, "path");
  path.setAttribute("d", pathData);
  path.style.animationDelay = `${order * TOUR_STAGGER_SECONDS}s`;
  tour.svg.append(path);
}

function tourBuildLabel(step, order) {
  const label = document.createElement("div");
  label.className = "tour-label";
  label.style.animationDelay = `${0.3 + order * TOUR_STAGGER_SECONDS}s`;
  const title = document.createElement("b");
  title.textContent = step.title;
  label.append(title, step.text);
  return label;
}

function tourAddLabel(tour, step, order, position) {
  const label = tourBuildLabel(step, order);
  if (position.narrow) label.classList.add("narrow");
  label.style.top = `${position.top}px`;
  if (position.right !== undefined) label.style.right = `${position.right}px`;
  else label.style.left = `${position.left}px`;
  tour.stage.append(label);
}

function tourElbowPath(startX, startY, endX, endY) {
  // Down from the button, round the corner, then sideways to the label.
  const radius = Math.min(TOUR_RADIUS, endY - startY, Math.abs(endX - startX));
  const direction = endX > startX ? 1 : -1;
  return `M ${startX} ${startY} V ${endY - radius} Q ${startX} ${endY} ${startX + direction * radius} ${endY} H ${endX}`;
}

function tourStairPath(startX, startY, endX, endY) {
  // Up from the control, round the corner, then sideways to the label.
  const direction = endX > startX ? 1 : -1;
  return `M ${startX} ${startY} V ${endY + TOUR_RADIUS} Q ${startX} ${endY} ${startX + direction * TOUR_RADIUS} ${endY} H ${endX}`;
}

// Stacks labels above the controls, rightmost control lowest, so that no line
// runs through a label. Steps are given rightmost first.
function tourDrawStairs(tour, steps, baseY) {
  let row = 0;
  for (const step of steps) {
    const targets = tourLiftTargets(tour, step, row);
    if (targets.length === 0) continue;
    const box = tourCombinedBox(targets);
    const labelTop = baseY - TOUR_STAIR_GAP * (row + 1);
    const lineY = labelTop + 26;
    const labelInset = TOUR_RADIUS + 8;
    if (step.labelOnLeft) {
      tourAddLine(tour, tourStairPath(box.centerX, box.top - 4, box.centerX - labelInset, lineY), row);
      tourAddLabel(tour, step, row, { top: labelTop, right: window.innerWidth - box.centerX + labelInset, narrow: true });
    } else {
      tourAddLine(tour, tourStairPath(box.centerX, box.top - 4, box.centerX + labelInset, lineY), row);
      tourAddLabel(tour, step, row, { top: labelTop, left: box.centerX + labelInset, narrow: true });
    }
    row += 1;
  }
}

function tourDrawComposerStep(tour) {
  const features = document.getElementById("composer-features").getBoundingClientRect();
  tourDrawStairs(tour, [TOUR_RUN, TOUR_PROMPT, TOUR_ATTACH], features.top - 8);

  const targets = tourLiftTargets(tour, TOUR_MODELS, 3);
  if (targets.length === 0) return;
  const box = tourCombinedBox(targets);
  tourAddLine(tour, `M ${box.right + 4} ${box.centerY} H ${box.right + 20}`, 3);
  tourAddLabel(tour, TOUR_MODELS, 3, { top: box.centerY - 26, left: box.right + 20, narrow: true });
}

function tourDrawStagesStep(tour) {
  const features = document.getElementById("composer-features").getBoundingClientRect();
  const rightmostFirst = [...TOUR_STAGES].reverse();
  tourDrawStairs(tour, rightmostFirst, features.top - 8);
}

// Stacks labels below the tabs, rightmost tab highest, so that no line runs
// through a label.
function tourDrawTabsStep(tour) {
  const rightmostFirst = [...TOUR_TABS].reverse();
  const labelInset = TOUR_RADIUS + 8;
  rightmostFirst.forEach((step, row) => {
    const targets = tourLiftTargets(tour, step, row);
    if (targets.length === 0) return;
    const box = tourCombinedBox(targets);
    const lineY = box.bottom + TOUR_STAIR_GAP * (row + 1);
    tourAddLine(tour, tourElbowPath(box.centerX, box.bottom + 4, box.centerX + labelInset, lineY), row);
    tourAddLabel(tour, step, row, { top: lineY - 26, left: box.centerX + labelInset, narrow: true });
  });
}

function tourDrawHeaderStep(tour) {
  const firstHeaderTargets = tourVisibleTargets(TOUR_HEADER_STEPS[0]);
  const labelRightEdge = firstHeaderTargets[0].getBoundingClientRect().left - 28;
  const labelRightInset = window.innerWidth - labelRightEdge;

  // The header labels start below the tab row, which has its own trace label.
  const traceTargets = tourLiftTargets(tour, TOUR_TRACE, 0);
  const traceBox = tourCombinedBox(traceTargets);
  const firstRowY = traceBox.bottom + 44;
  tourAddLine(tour, `M ${traceBox.left - 4} ${traceBox.centerY} H ${traceBox.left - 20}`, 0);
  tourAddLabel(tour, TOUR_TRACE, 0, { top: traceBox.centerY - 26, right: window.innerWidth - traceBox.left + 20 });

  TOUR_HEADER_STEPS.forEach((step, row) => {
    const targets = tourLiftTargets(tour, step, row + 1);
    if (targets.length === 0) return;
    const box = tourCombinedBox(targets);
    const endY = firstRowY + row * TOUR_ROW_GAP;
    tourAddLine(tour, tourElbowPath(box.centerX, box.bottom + 4, labelRightEdge + 8, endY), row + 1);
    tourAddLabel(tour, step, row + 1, { top: endY - 16, right: labelRightInset });
  });

  const menuTargets = tourLiftTargets(tour, TOUR_MENU, 5);
  if (menuTargets.length === 0) return;
  const menuBox = tourCombinedBox(menuTargets);
  const menuLabelY = firstRowY + TOUR_HEADER_STEPS.length * TOUR_ROW_GAP;
  tourAddLine(tour, `M ${menuBox.centerX} ${menuBox.bottom + 4} V ${menuLabelY}`, 5);
  tourAddLabel(tour, TOUR_MENU, 5, { top: menuLabelY, left: Math.max(8, menuBox.centerX - 16), narrow: true });
}

function tourDrawHelpStep(tour) {
  const targets = tourLiftTargets(tour, TOUR_HELP, 0);
  const box = tourCombinedBox(targets);
  const endY = box.bottom + 60;
  tourAddLine(tour, tourElbowPath(box.centerX, box.bottom + 4, box.left - 20, endY), 0);
  tourAddLabel(tour, TOUR_HELP, 0, { top: endY - 26, right: window.innerWidth - box.left + 20, narrow: true });
}

function tourDrawList(tour, steps) {
  const list = document.createElement("div");
  list.className = "tour-list";
  let lowestTargetBottom = 0;
  steps.forEach((step, order) => {
    const targets = tourLiftTargets(tour, step, order);
    if (targets.length === 0) return;
    list.append(tourBuildLabel(step, order));
    lowestTargetBottom = Math.max(lowestTargetBottom, tourCombinedBox(targets).bottom);
  });
  tour.stage.append(list);
  // Controls near the top of the page would otherwise sit on the first labels.
  const listBox = list.getBoundingClientRect();
  const targetsAreAboveMiddle = lowestTargetBottom < window.innerHeight / 2;
  if (targetsAreAboveMiddle && lowestTargetBottom > listBox.top) {
    list.style.top = `${lowestTargetBottom + 12}px`;
  }
}

const TOUR_PAGES = [
  { draw: tourDrawComposerStep, listSteps: [TOUR_ATTACH, TOUR_PROMPT, TOUR_MODELS, TOUR_RUN] },
  { draw: tourDrawStagesStep, listSteps: TOUR_STAGES },
  { draw: tourDrawTabsStep, listSteps: TOUR_TABS },
  { draw: tourDrawHeaderStep, listSteps: [TOUR_MENU, ...TOUR_HEADER_STEPS, TOUR_TRACE] },
  { draw: tourDrawHelpStep, listSteps: [TOUR_HELP] },
];

function startTour() {
  const overlay = document.createElement("div");
  overlay.id = "tour";
  const svgNs = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNs, "svg");
  const stage = document.createElement("div");
  overlay.append(svg, stage);
  const tour = { svgNs, svg, stage, lifted: [] };
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

  const clearDrawing = () => {
    svg.replaceChildren();
    stage.replaceChildren();
    for (const element of tour.lifted) {
      element.classList.remove("tour-lifted");
      element.style.removeProperty("animation-delay");
    }
    tour.lifted.length = 0;
  };

  const draw = () => {
    clearDrawing();
    const page = TOUR_PAGES[pageIndex];
    if (window.innerWidth < TOUR_LIST_WIDTH) tourDrawList(tour, page.listSteps);
    else page.draw(tour);
    progress.textContent = `${pageIndex + 1} of ${TOUR_PAGES.length}`;
    back.hidden = pageIndex === 0;
    next.textContent = pageIndex === TOUR_PAGES.length - 1 ? "Got it" : "Next";
  };

  // The empty-chat screen renders after this script runs and moves the
  // composer, so the drawing follows the layout instead of measuring once.
  const layoutObserver = new ResizeObserver(draw);

  const finish = () => {
    try { localStorage.setItem(TOUR_SEEN_KEY, "1"); } catch {}
    layoutObserver.disconnect();
    clearDrawing();
    overlay.remove();
    document.removeEventListener("keydown", onKey);
  };
  const goForward = () => {
    if (pageIndex === TOUR_PAGES.length - 1) {
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

  draw();
  layoutObserver.observe(document.body);
  layoutObserver.observe(document.getElementById("messages"));
  layoutObserver.observe(document.getElementById("composer-dock"));
  document.body.append(overlay);
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
