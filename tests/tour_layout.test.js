// Tests for app/static/tour_layout.js, the geometry behind the first-run tour.
// Driven by tests/test_tour_layout.py; runs on its own with
//
//     node --test tests/tour_layout.test.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const TourLayout = require("../app/static/tour_layout.js");

const { makeRect, solve, validate, quality, routeLeader, simplifyPoints, roundedPath, pathLength } = TourLayout;
const SETTINGS = TourLayout.DEFAULTS;

// What a label's text can measure at, roomiest first.
const LABEL_SIZES = [
  { width: 240, height: 56 },
  { width: 190, height: 72 },
  { width: 150, height: 90 },
];

function makeStep(id, anchors) {
  return { id, anchors, sizes: LABEL_SIZES };
}

function makeScene(width, height, steps, salient, controls) {
  return {
    viewport: { left: 0, top: 0, right: width, bottom: height },
    controls: controls || [],
    salient: salient || [],
    steps,
  };
}

function solveAndCheck(scene) {
  const started = Date.now();
  const solution = solve(scene);
  const milliseconds = Date.now() - started;
  assert.ok(solution.ok, `no layout found (failed on ${solution.failedId})`);
  const problems = validate(scene, solution);
  assert.deepEqual(problems, []);
  return { solution, milliseconds };
}

// How many spans of a solution's lines run over a rect.
function linesOver(solution, rect) {
  let count = 0;
  for (const placement of solution.placements) {
    for (let index = 1; index < placement.points.length; index += 1) {
      const from = placement.points[index - 1];
      const to = placement.points[index];
      const box = {
        left: Math.min(from.x, to.x),
        right: Math.max(from.x, to.x),
        top: Math.min(from.y, to.y),
        bottom: Math.max(from.y, to.y),
      };
      const crossesAcross = box.left < rect.right && box.right > rect.left && box.top < rect.bottom && box.bottom > rect.top;
      const isLine = box.left === box.right || box.top === box.bottom;
      const insideAcross = box.left === box.right ? rect.left < box.left && box.left < rect.right : rect.top < box.top && box.top < rect.bottom;
      if (isLine && crossesAcross && insideAcross) count += 1;
    }
  }
  return count;
}

// ---- the router on its own ----
function routeRequest(overrides) {
  const base = {
    sources: [{ dot: { x: 100, y: 96 }, stub: { x: 100, y: 86 }, heading: TourLayout.NORTH, penalty: 0 }],
    targets: [{ port: { x: 300, y: 20 }, stub: { x: 300, y: 34 }, heading: TourLayout.NORTH, penalty: 0 }],
    bounds: { left: 0, top: 0, right: 400, bottom: 200 },
    stubBlockers: [],
    gridBlockers: [],
    gridRects: [],
    softRects: [],
    placed: [],
  };
  return Object.assign(base, overrides);
}

test("a route in open ground has one bend at most to reach a stub, and ends on its target", () => {
  const route = routeLeader(routeRequest({}), SETTINGS);
  assert.ok(route);
  assert.deepEqual(route.points[0], { x: 100, y: 96 });
  assert.deepEqual(route.points[route.points.length - 1], { x: 300, y: 20 });
  for (let index = 1; index < route.points.length; index += 1) {
    const from = route.points[index - 1];
    const to = route.points[index];
    assert.ok(from.x === to.x || from.y === to.y, "every segment is horizontal or vertical");
  }
});

test("a route goes around a wall rather than through it", () => {
  const wall = makeRect(150, -10, 20, 170);
  const request = routeRequest({ stubBlockers: [wall], gridBlockers: [wall], gridRects: [wall] });
  const route = routeLeader(request, SETTINGS);
  assert.ok(route);
  for (let index = 1; index < route.points.length; index += 1) {
    const from = route.points[index - 1];
    const to = route.points[index];
    const lowX = Math.min(from.x, to.x);
    const highX = Math.max(from.x, to.x);
    const lowY = Math.min(from.y, to.y);
    const highY = Math.max(from.y, to.y);
    const insideWall = lowX < wall.right && highX > wall.left && lowY < wall.bottom && highY > wall.top;
    assert.equal(insideWall, false, "no segment enters the wall");
  }
  assert.ok(route.points.some((point) => point.y >= 160), "it passes under the wall");
});

test("a target sealed off from every side has no route", () => {
  const sealing = [makeRect(240, 0, 10, 200), makeRect(240, 0, 120, 10), makeRect(350, 0, 10, 200), makeRect(240, 190, 120, 10)];
  const request = routeRequest({ stubBlockers: sealing, gridBlockers: sealing, gridRects: sealing });
  assert.equal(routeLeader(request, SETTINGS), null);
});

test("a route prefers one bend to several", () => {
  const request = routeRequest({
    sources: [{ dot: { x: 100, y: 150 }, stub: { x: 100, y: 140 }, heading: TourLayout.NORTH, penalty: 0 }],
    targets: [{ port: { x: 300, y: 50 }, stub: { x: 290, y: 50 }, heading: TourLayout.EAST, penalty: 0 }],
  });
  // The target is reached heading east; a one-corner L works.
  const route = routeLeader(request, SETTINGS);
  assert.ok(route);
  assert.equal(route.points.length, 3, "an L has three points");
});

test("a route that would run along another line is refused, one that crosses it pays", () => {
  const along = { horizontal: false, fixed: 102, low: 0, high: 200 };
  const blocked = routeLeader(
    routeRequest({
      sources: [{ dot: { x: 100, y: 96 }, stub: { x: 100, y: 86 }, heading: TourLayout.NORTH, penalty: 0 }],
      targets: [{ port: { x: 100, y: 20 }, stub: { x: 100, y: 34 }, heading: TourLayout.NORTH, penalty: 0 }],
      placed: [along],
    }),
    SETTINGS,
  );
  assert.equal(blocked, null, "the straight line would run beside another within the spacing");

  const across = { horizontal: true, fixed: 60, low: 0, high: 400 };
  const sameEnds = {
    sources: [{ dot: { x: 100, y: 96 }, stub: { x: 100, y: 86 }, heading: TourLayout.NORTH, penalty: 0 }],
    targets: [{ port: { x: 100, y: 20 }, stub: { x: 100, y: 34 }, heading: TourLayout.NORTH, penalty: 0 }],
  };
  const free = routeLeader(routeRequest(sameEnds), SETTINGS);
  const crossing = routeLeader(routeRequest(Object.assign({ placed: [across] }, sameEnds)), SETTINGS);
  assert.ok(free);
  assert.ok(crossing);
  assert.equal(crossing.cost - free.cost, SETTINGS.crossingPenalty, "crossing costs exactly the crossing penalty");
});

test("a line pays for running over text it can't go around", () => {
  const band = { rect: makeRect(0, 40, 400, 40), perPixel: 5 };
  const ends = {
    sources: [{ dot: { x: 100, y: 96 }, stub: { x: 100, y: 86 }, heading: TourLayout.NORTH, penalty: 0 }],
    targets: [{ port: { x: 100, y: 20 }, stub: { x: 100, y: 34 }, heading: TourLayout.NORTH, penalty: 0 }],
  };
  const clear = routeLeader(routeRequest(ends), SETTINGS);
  const over = routeLeader(routeRequest(Object.assign({ softRects: [band], gridRects: [band.rect] }, ends)), SETTINGS);
  assert.ok(clear && over);
  assert.equal(over.cost - clear.cost, 40 * 5, "the 40px over the band is charged at its rate");
});

test("simplifyPoints drops repeats and straight-through corners", () => {
  const points = [
    { x: 0, y: 0 },
    { x: 0, y: 0 },
    { x: 0, y: 5 },
    { x: 0, y: 10 },
    { x: 7, y: 10 },
    { x: 20, y: 10 },
  ];
  assert.deepEqual(simplifyPoints(points), [{ x: 0, y: 0 }, { x: 0, y: 10 }, { x: 20, y: 10 }]);
});

test("roundedPath rounds each corner by the radius, less when the sides are short", () => {
  const long = roundedPath([{ x: 0, y: 0 }, { x: 0, y: 100 }, { x: 100, y: 100 }], 12);
  assert.equal(long, "M 0 0 L 0 88 Q 0 100 12 100 L 100 100");
  const short = roundedPath([{ x: 0, y: 0 }, { x: 0, y: 10 }, { x: 100, y: 10 }], 12);
  assert.equal(short, "M 0 0 L 0 5 Q 0 10 5 10 L 100 10");
  assert.equal(pathLength([{ x: 0, y: 0 }, { x: 0, y: 10 }, { x: 5, y: 10 }]), 15);
});

// ---- scenes shaped like the app ----
// The composer: a prompt bar with an attach button on its left and a run
// button on its right, a row of stage toggles above it, a model picker below,
// and suggestion chips and a title floating higher up.
function composerScene(width, height) {
  const dockLeft = Math.round(width / 2 - 380);
  const prompt = makeRect(dockLeft + 50, height - 131, 655, 41);
  const attach = makeRect(dockLeft + 8, height - 124, 30, 30);
  const run = makeRect(dockLeft + 720, height - 130, 40, 40);
  const picker = makeRect(dockLeft, height - 68, 131, 30);
  const stages = [
    makeRect(dockLeft + 8, height - 170, 134, 30),
    makeRect(dockLeft + 150, height - 170, 113, 30),
    makeRect(dockLeft + 272, height - 170, 126, 30),
    makeRect(dockLeft + 407, height - 170, 139, 30),
  ];
  const salient = [];
  for (const stage of stages) salient.push({ rect: stage, kind: "control" });
  salient.push({ rect: makeRect(dockLeft + 60, 328, 413, 38), kind: "control" });
  salient.push({ rect: makeRect(dockLeft + 480, 328, 224, 38), kind: "control" });
  salient.push({ rect: makeRect(dockLeft + 240, 366, 290, 38), kind: "control" });
  salient.push({ rect: makeRect(dockLeft + 290, 265, 120, 36), kind: "content" });
  const controls = [makeRect(width - 330, height - 66, 300, 44)];
  return { attach, prompt, run, picker, stages, scene: makeScene(width, height, [
    makeStep("attach", [attach]),
    makeStep("prompt", [prompt]),
    makeStep("run", [run]),
    makeStep("picker", [picker]),
  ], salient, controls) };
}

// The header's row of buttons, the tab row with a trace button on its right,
// and the empty-chat chips below.
function headerScene(width, height) {
  const buttons = [];
  const right = width - 20;
  for (let index = 0; index < 5; index += 1) {
    buttons.push(makeRect(right - 38 - index * 44, 10, 36, 36));
  }
  buttons.reverse();
  const menu = makeRect(18, 10, 36, 36);
  const trace = makeRect(width / 2 + 290, 80, 76, 28);
  const tabs = [];
  for (let index = 0; index < 6; index += 1) {
    tabs.push(makeRect(width / 2 - 380 + index * 100, 80, 92, 30));
  }
  const salient = [{ rect: makeRect(width / 2 - 140, 90, 90, 0), kind: "content" }];
  salient.length = 0;
  salient.push({ rect: makeRect(width / 2 - 270, 328, 413, 38), kind: "control" });
  salient.push({ rect: makeRect(width / 2 + 150, 328, 224, 38), kind: "control" });
  salient.push({ rect: makeRect(width / 2 - 100, 270, 200, 40), kind: "content" });
  salient.push({ rect: makeRect(width / 2 - 380, height - 170, 760, 120), kind: "control" });
  salient.push({ rect: makeRect(70, 10, 36, 36), kind: "control" });
  const controls = [makeRect(width - 330, height - 66, 300, 44)];
  return { buttons, menu, trace, tabs, scene: makeScene(width, height, [
    makeStep("menu", [menu]),
    makeStep("leads", [buttons[0]]),
    makeStep("files", [buttons[1]]),
    makeStep("integrity", [buttons[2]]),
    makeStep("spending", [buttons[3]]),
    makeStep("settings", [buttons[4]]),
    makeStep("trace", [trace]),
  ], salient, controls) };
}

for (const [width, height] of [[1440, 900], [1280, 720], [1100, 800]]) {
  test(`the composer's four controls get labels and lines at ${width}x${height}`, () => {
    const { scene, stages } = composerScene(width, height);
    const { solution, milliseconds } = solveAndCheck(scene);
    assert.equal(solution.pass, "strict");
    for (const stage of stages) {
      assert.equal(linesOver(solution, stage), 0, "no line strikes through a stage toggle");
    }
    let total = 0;
    for (const placement of solution.placements) total += pathLength(placement.points);
    assert.ok(total < 1400, `lines are short overall (${Math.round(total)}px)`);
    assert.ok(milliseconds < 2500, `solved in ${milliseconds}ms`);
  });

  test(`the header's seven controls get labels and lines at ${width}x${height}`, () => {
    const { scene } = headerScene(width, height);
    const { solution, milliseconds } = solveAndCheck(scene);
    assert.equal(solution.placements.length, 7);
    assert.ok(milliseconds < 2500, `solved in ${milliseconds}ms`);
  });
}

test("the same scene always gives the same layout", () => {
  const { scene } = headerScene(1440, 900);
  const first = solve(scene);
  const second = solve(scene);
  assert.deepEqual(first.placements, second.placements);
});

test("a single control in the middle of an empty window gets a short straight line", () => {
  const anchor = makeRect(600, 400, 80, 36);
  const scene = makeScene(1280, 800, [makeStep("only", [anchor])]);
  const { solution } = solveAndCheck(scene);
  const points = solution.placements[0].points;
  assert.equal(points.length, 2, "a straight line has two points");
  assert.ok(pathLength(points) < 40);
});

test("a control with two parts can be pointed at from either", () => {
  const toggle = makeRect(300, 700, 100, 30);
  const pen = makeRect(404, 700, 30, 30);
  const scene = makeScene(1280, 800, [makeStep("notes", [toggle, pen])]);
  solveAndCheck(scene);
});

test("labels move to where there is room when their first choice is taken", () => {
  // Controls that fill every row above the anchor force the label elsewhere.
  const anchor = makeRect(600, 100, 80, 36);
  const salient = [];
  for (let row = 0; row < 3; row += 1) {
    salient.push({ rect: makeRect(300, 20 + row * 40, 700, 36), kind: "control" });
  }
  const scene = makeScene(1280, 800, [makeStep("only", [anchor])], salient);
  const { solution } = solveAndCheck(scene);
  assert.equal(solution.pass, "strict");
  assert.ok(solution.placements[0].rect.top > 100, "the label goes below");
});

test("when no label can clear every control, the second pass covers unlit ones", () => {
  // Controls on both sides of the anchor and a wall of them below it leave no
  // room anywhere.
  const anchor = makeRect(600, 20, 80, 36);
  const salient = [
    { rect: makeRect(0, 8, 590, 58), kind: "control" },
    { rect: makeRect(690, 8, 590, 58), kind: "control" },
    { rect: makeRect(0, 70, 1280, 730), kind: "control" },
  ];
  const scene = makeScene(1280, 800, [makeStep("only", [anchor])], salient);
  const solution = solve(scene);
  assert.ok(solution.ok);
  assert.equal(solution.pass, "relaxed");
  assert.deepEqual(validate(scene, solution), []);
});

test("a control with no room for any label fails, naming the step", () => {
  const anchor = makeRect(8, 8, 1264, 784);
  const scene = makeScene(1280, 800, [makeStep("everything", [anchor])]);
  const solution = solve(scene);
  assert.equal(solution.ok, false);
  assert.equal(solution.failedId, "everything");
});

test("an empty scene has an empty layout", () => {
  const solution = solve(makeScene(1280, 800, []));
  assert.ok(solution.ok);
  assert.deepEqual(solution.placements, []);
});

test("validate catches a label on top of a lit control, a diagonal line and a missing line", () => {
  const anchor = makeRect(600, 400, 80, 36);
  const scene = makeScene(1280, 800, [makeStep("only", [anchor])]);
  const solution = solve(scene);
  assert.ok(solution.ok);

  const covering = JSON.parse(JSON.stringify(solution));
  covering.placements[0].rect = makeRect(590, 395, 240, 56);
  assert.ok(validate(scene, covering).some((problem) => problem.includes("covers a lit control")));

  const diagonal = JSON.parse(JSON.stringify(solution));
  diagonal.placements[0].points[1].x += 30;
  diagonal.placements[0].points[1].y += 30;
  assert.ok(validate(scene, diagonal).some((problem) => problem.includes("diagonal")));

  const lineless = JSON.parse(JSON.stringify(solution));
  lineless.placements[0].points = [];
  assert.ok(validate(scene, lineless).some((problem) => problem.includes("no line")));
});

// ---- random scenes ----
// A small seeded generator, so a failure can be reproduced from its seed.
function seededRandom(seed) {
  let state = seed >>> 0;
  return function next() {
    state = (state + 0x6d2b79f5) >>> 0;
    let mixed = Math.imul(state ^ (state >>> 15), 1 | state);
    mixed = (mixed + Math.imul(mixed ^ (mixed >>> 7), 61 | mixed)) ^ mixed;
    return ((mixed ^ (mixed >>> 14)) >>> 0) / 4294967296;
  };
}

function randomScene(seed) {
  const random = seededRandom(seed);
  const between = (low, high) => low + random() * (high - low);
  const width = Math.round(between(760, 1700));
  const height = Math.round(between(520, 1000));
  const placed = [];
  function randomRect(minWidth, maxWidth, minHeight, maxHeight) {
    for (let attempt = 0; attempt < 30; attempt += 1) {
      const rect = makeRect(Math.round(between(10, width - maxWidth - 10)), Math.round(between(10, height - maxHeight - 10)), Math.round(between(minWidth, maxWidth)), Math.round(between(minHeight, maxHeight)));
      let clear = true;
      for (const other of placed) {
        if (TourLayout.overlaps(rect, { left: other.left - 6, top: other.top - 6, right: other.right + 6, bottom: other.bottom + 6 })) clear = false;
      }
      if (clear) {
        placed.push(rect);
        return rect;
      }
    }
    return null;
  }
  const steps = [];
  const stepCount = 1 + Math.floor(random() * 7);
  for (let index = 0; index < stepCount; index += 1) {
    const rect = randomRect(26, 160, 26, 48);
    if (!rect) continue;
    const anchors = [rect];
    if (random() < 0.25) {
      const sibling = makeRect(rect.right + 4, rect.top, 30, rect.bottom - rect.top);
      if (sibling.right < width - 10) {
        placed.push(sibling);
        anchors.push(sibling);
      }
    }
    steps.push(makeStep(`step-${index}`, anchors));
  }
  const salient = [];
  const salientCount = Math.floor(random() * 30);
  for (let index = 0; index < salientCount; index += 1) {
    const rect = randomRect(30, 320, 20, 60);
    if (!rect) continue;
    salient.push({ rect, kind: random() < 0.6 ? "control" : "content" });
  }
  const controls = [makeRect(width - 330, height - 66, 300, 44)];
  return makeScene(width, height, steps, salient, controls);
}

test("every layout the solver returns for 60 random scenes passes the validator", () => {
  let solved = 0;
  let slowest = 0;
  for (let seed = 1; seed <= 60; seed += 1) {
    const scene = randomScene(seed);
    const started = Date.now();
    const solution = solve(scene);
    slowest = Math.max(slowest, Date.now() - started);
    if (!solution.ok) continue;
    solved += 1;
    assert.deepEqual(validate(scene, solution), [], `seed ${seed}`);
  }
  console.log(`# random scenes: ${solved}/60 solved, slowest ${slowest}ms`);
  assert.ok(solved >= 48, `most random scenes are solvable (${solved}/60)`);
  assert.ok(slowest < 4000, `the slowest took ${slowest}ms`);
});

// The page's boxes have fractions (523.296875), which the engine snaps to a
// thousandth of a pixel: an end that landed a hair inside its own label once
// made every layout look broken. The same scenes, moved onto awkward fractions.
function withFractions(scene) {
  const scale = 1.0371;
  const shiftX = 0.296875;
  const shiftY = 0.59375;
  function move(rect) {
    return { left: rect.left * scale + shiftX, top: rect.top * scale + shiftY, right: rect.right * scale + shiftX, bottom: rect.bottom * scale + shiftY };
  }
  return {
    viewport: scene.viewport,
    controls: scene.controls.map(move),
    salient: scene.salient.map((item) => ({ rect: move(item.rect), kind: item.kind })),
    raised: (scene.raised || []).map(move),
    steps: scene.steps.map((step) => ({
      id: step.id,
      anchors: step.anchors.map(move),
      sizes: step.sizes.map((size) => ({ width: size.width * 1.0173, height: size.height * 1.0091 })),
    })),
  };
}

test("layouts of scenes with fractional coordinates pass the validator", () => {
  let solved = 0;
  for (let seed = 1; seed <= 25; seed += 1) {
    const scene = withFractions(randomScene(seed));
    const solution = solve(scene);
    if (!solution.ok) continue;
    solved += 1;
    assert.deepEqual(validate(scene, solution), [], `seed ${seed}`);
  }
  assert.ok(solved >= 18, `most are solvable (${solved}/25)`);
});

test("quality counts the turns a reader sees and the crossings between lines", () => {
  const straight = { id: "a", points: [{ x: 0, y: 0 }, { x: 0, y: 50 }] };
  const bent = { id: "b", points: [{ x: 20, y: 80 }, { x: 20, y: 25 }, { x: -30, y: 25 }, { x: -30, y: -10 }] };
  assert.deepEqual(quality({ placements: [straight] }), { mostBends: 0, crossings: 0 });
  // b's run along y=25 crosses a's line at x=0.
  assert.deepEqual(quality({ placements: [straight, bent] }), { mostBends: 2, crossings: 1 });
});

test("quality ignores jogs too short to read as turns", () => {
  const jogged = { id: "a", points: [{ x: 0, y: 0 }, { x: -10, y: 0 }, { x: -10, y: 14 }, { x: -46, y: 14 }, { x: -46, y: 66 }] };
  // The 10px and 14px nudges are corners; what's left is one turn.
  assert.deepEqual(quality({ placements: [jogged] }), { mostBends: 1, crossings: 0 });
});

// ---- scenes recorded from the real page ----
// What the tour measured on the live page (tests/tour_scenes/page-N-WxH.json),
// at a wide, a middle and a narrow window. Every layout must be sound at all
// three, and tidy where there is room; a tangle at the narrow ones is the
// tour's cue to show a list instead.
const FIXTURE_DIRECTORY = path.join(__dirname, "tour_scenes");
const MOST_BENDS = 3;
const MOST_CROSSINGS = 1;
if (fs.existsSync(FIXTURE_DIRECTORY)) {
  for (const name of fs.readdirSync(FIXTURE_DIRECTORY).sort()) {
    if (!name.endsWith(".json")) continue;
    test(`recorded scene ${name} lays out soundly`, () => {
      const scene = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIRECTORY, name), "utf8"));
      const { solution, milliseconds } = solveAndCheck(scene);
      assert.equal(solution.placements.length, scene.steps.length);
      assert.ok(milliseconds < 4000, `solved in ${milliseconds}ms`);
      if (name.includes("1440x900")) {
        const tidiness = quality(solution);
        assert.ok(tidiness.mostBends <= MOST_BENDS, `${tidiness.mostBends} bends in a line`);
        assert.ok(tidiness.crossings <= MOST_CROSSINGS, `${tidiness.crossings} crossings`);
      }
    });
  }
}
