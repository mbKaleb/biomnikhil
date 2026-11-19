// ---- first-run tour layout: finds a spot for every label and a route for the
// line that joins it to its control. Pure geometry with no DOM, so it can be
// tested on its own (tests/test_tour_layout.py) and checked by validate().
//
// The caller describes the page as a scene, measured from the live DOM:
//   viewport  {left, top, right, bottom}
//   controls  rects nothing may cover (the tour's own buttons)
//   salient   {rect, kind} for everything else visible on the page, kind being
//             "control" (buttons, inputs) or "content" (headings, text)
//   raised    rects drawn above the tour (a lit header bar covers what lies
//             under it): no label goes there, and a line only passes through it
//             to get out
//   steps     {id, anchors: [rect], sizes: [{width, height}]}: the controls to
//             point at, and the sizes their label can take (roomiest first)
//
// solve() returns {ok: true, placements: [{id, rect, sizeIndex, anchorIndex,
// points}]} or {ok: false, failedId}. Each line is a list of corner points that
// starts just off its control and ends on the label's edge.
//
// How it works:
//   1. Candidates: label rectangles on every side of each control, at growing
//      distances, dropped when they leave the window or cover a control.
//   2. Routing: for one candidate, an orthogonal A* over a sparse grid whose
//      lines are the edges of every obstacle nearby. A route pays for length,
//      for bends, and for passing over text or controls or crossing a line; it
//      may not touch a lit control, a label or another line's path.
//   3. Search: a beam over the steps, most constrained first. Each partial
//      layout is extended with the cheapest candidates that route, and the
//      best few survive to the next step.
//   4. Two passes: the first keeps labels off every control; if that finds no
//      layout the second lets them cover unlit ones at a price.
const TourLayout = (() => {
  const DEFAULTS = {
    edgeMargin: 8,           // labels and lines stay this far inside the window
    clearance: 6,            // lines stay this far off lit controls and labels
    labelClearance: 6,       // labels stay this far off lit and unlit controls
    labelGap: 10,            // between two labels
    portGap: 4,              // the line's dot floats this far off its control
    stubLength: 14,          // a line runs straight this far before it may turn
    portPitch: 24,           // spacing of the points a line may leave a control from
    cornerMargin: 10,        // lines leave this far in from a control's corner
    bendPenalty: 30,
    crossingPenalty: 140,
    parallelSpacing: 6,      // two lines running side by side stay this far apart
    controlLinePenalty: 6,   // per pixel of line over an unlit control
    raisedLinePenalty: 25,   // per pixel of line under something drawn above the tour
    linePadding: 3,          // a line passing this close to an unlit control counts as over it
    contentLinePenalty: 1.5, // per pixel of line over text
    contentLabelPenalty: 0.004, // per square pixel of label over text
    contentLabelShare: 80,   // and this much for covering all of a block of text, in proportion to the share covered
    controlLabelPenalty: 0.03,  // per square pixel of label over an unlit control (second pass)
    controlLabelShare: 200,  // and for covering all of one
    sizePenalty: 10,         // for each size step below the roomiest
    offCenterPenalty: 6,     // a line that meets its label away from the middle
    alignmentBonus: 12,      // a label that lines up with one already placed
    openPenalty: 400,        // for taking all of another step's remaining places (less for a share)
    snapGap: 8,              // a label snapped beside another control stops this far from it
    snapReach: 420,          // and only controls within this far along the side count
    snappedGaps: 6,          // the most extra distances taken from nearby obstacle edges
    gaps: [16, 28, 40, 52, 64, 78, 92, 108, 124, 142, 160, 180, 200, 222, 246, 272, 300, 330, 362],
    lateralStep: 24,
    lateralSteps: 10,
    candidatesPerBucket: 3,  // kept for each side and distance
    alignedPerBucket: 1,     // and, besides those, each one lined up with an edge or a neighbour
    beamWidth: 5,
    orders: ["x-asc", "x-desc", "y-asc", "y-desc", "constrained"], // the step orders the beam is run in
    rankPool: 80,            // how many of the cheapest candidates are ranked by what they leave others
    attemptsPerState: 40,
    successesPerState: 10,
    fallbackRoutes: 2500,    // the most routes the fallback search tries
    fallbackBranching: 8,    // routable candidates it tries at each step
    routeMargin: 120,
    maxExpansions: 8000,
    cornerRadius: 12,
  };

  // Headings, in the order the grid search turns through them.
  const EAST = 0;
  const SOUTH = 1;
  const WEST = 2;
  const NORTH = 3;
  const STEP_X = [1, 0, -1, 0];
  const STEP_Y = [0, 1, 0, -1];
  const HEADINGS = [NORTH, SOUTH, WEST, EAST];

  // ---- rectangles ----
  // Coordinates are snapped to a thousandth of a pixel so equal places compare
  // equal after the arithmetic that produced them.
  function snap(value) {
    return Math.round(value * 1000) / 1000;
  }

  function makeRect(left, top, width, height) {
    return { left: snap(left), top: snap(top), right: snap(left + width), bottom: snap(top + height) };
  }

  function snapRect(rect) {
    return { left: snap(rect.left), top: snap(rect.top), right: snap(rect.right), bottom: snap(rect.bottom) };
  }

  function inflate(rect, amount) {
    return snapRect({ left: rect.left - amount, top: rect.top - amount, right: rect.right + amount, bottom: rect.bottom + amount });
  }

  // Rects that only touch don't overlap.
  function overlaps(a, b) {
    return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  }

  function overlapArea(a, b) {
    const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    if (width <= 0 || height <= 0) return 0;
    return width * height;
  }

  function isInside(outer, inner) {
    return inner.left >= outer.left && inner.top >= outer.top && inner.right <= outer.right && inner.bottom <= outer.bottom;
  }

  // The gap between two rects, as the distance a line must cover to join them.
  function rectGap(a, b) {
    const across = Math.max(0, b.left - a.right, a.left - b.right);
    const down = Math.max(0, b.top - a.bottom, a.top - b.bottom);
    return across + down;
  }

  // ---- axis-aligned spans ----
  // A span is a stretch of a horizontal or vertical line: `fixed` is the y of a
  // horizontal one (or the x of a vertical one), `low`..`high` runs along it.
  function makeSpan(from, to) {
    const horizontal = from.y === to.y;
    if (horizontal) {
      return { horizontal, fixed: from.y, low: Math.min(from.x, to.x), high: Math.max(from.x, to.x) };
    }
    return { horizontal, fixed: from.x, low: Math.min(from.y, to.y), high: Math.max(from.y, to.y) };
  }

  // The length of a span that lies inside a rect, not counting its edges.
  function spanLengthInside(rect, span) {
    let acrossLow;
    let acrossHigh;
    let alongLow;
    let alongHigh;
    if (span.horizontal) {
      acrossLow = rect.top;
      acrossHigh = rect.bottom;
      alongLow = rect.left;
      alongHigh = rect.right;
    } else {
      acrossLow = rect.left;
      acrossHigh = rect.right;
      alongLow = rect.top;
      alongHigh = rect.bottom;
    }
    if (!(acrossLow < span.fixed && span.fixed < acrossHigh)) return 0;
    const length = Math.min(span.high, alongHigh) - Math.max(span.low, alongLow);
    if (length <= 0) return 0;
    return length;
  }

  function spansOf(points) {
    const spans = [];
    for (let index = 1; index < points.length; index += 1) {
      spans.push(makeSpan(points[index - 1], points[index]));
    }
    return spans;
  }

  function pathLength(points) {
    let length = 0;
    for (let index = 1; index < points.length; index += 1) {
      length += Math.abs(points[index].x - points[index - 1].x) + Math.abs(points[index].y - points[index - 1].y);
    }
    return length;
  }

  // Drops repeated points and the middle of any three in a row.
  function simplifyPoints(points) {
    const kept = [];
    for (const point of points) {
      if (kept.length > 0) {
        const previous = kept[kept.length - 1];
        if (previous.x === point.x && previous.y === point.y) continue;
      }
      if (kept.length >= 2) {
        const before = kept[kept.length - 2];
        const previous = kept[kept.length - 1];
        const onOneColumn = before.x === previous.x && previous.x === point.x;
        const onOneRow = before.y === previous.y && previous.y === point.y;
        if (onOneColumn || onOneRow) {
          kept[kept.length - 1] = point;
          continue;
        }
      }
      kept.push(point);
    }
    return kept;
  }

  // The SVG path through corner points, each corner rounded.
  function roundedPath(points, radius) {
    let path = `M ${points[0].x} ${points[0].y}`;
    for (let index = 1; index < points.length - 1; index += 1) {
      const before = points[index - 1];
      const corner = points[index];
      const after = points[index + 1];
      const inLength = Math.abs(corner.x - before.x) + Math.abs(corner.y - before.y);
      const outLength = Math.abs(after.x - corner.x) + Math.abs(after.y - corner.y);
      const cornerRadius = Math.min(radius, inLength / 2, outLength / 2);
      const enterX = corner.x - Math.sign(corner.x - before.x) * cornerRadius;
      const enterY = corner.y - Math.sign(corner.y - before.y) * cornerRadius;
      const leaveX = corner.x + Math.sign(after.x - corner.x) * cornerRadius;
      const leaveY = corner.y + Math.sign(after.y - corner.y) * cornerRadius;
      path += ` L ${enterX} ${enterY} Q ${corner.x} ${corner.y} ${leaveX} ${leaveY}`;
    }
    const last = points[points.length - 1];
    return `${path} L ${last.x} ${last.y}`;
  }

  // ---- what a stretch of line costs ----
  // Returns a function giving the cost of one span: its length, plus what it
  // runs over, plus the lines it crosses; Infinity when it can't go there.
  //   blockers  rects a line may not enter
  //   softRects {rect, perPixel}: things a line may cross at a price
  //   placed    spans of lines already drawn
  function makeCostModel(blockers, softRects, placed, settings) {
    return function spanCost(from, to) {
      const span = makeSpan(from, to);
      for (const blocker of blockers) {
        if (spanLengthInside(blocker, span) > 0) return Infinity;
      }
      let cost = span.high - span.low;
      for (const soft of softRects) {
        const inside = spanLengthInside(soft.rect, span);
        if (inside > 0) cost += inside * soft.perPixel;
      }
      for (const other of placed) {
        if (other.horizontal === span.horizontal) {
          const closeEnough = Math.abs(other.fixed - span.fixed) < settings.parallelSpacing;
          const shared = Math.min(other.high, span.high) - Math.max(other.low, span.low);
          if (closeEnough && shared > 0) return Infinity;
        } else {
          const crosses = other.low <= span.fixed && span.fixed <= other.high && span.low <= other.fixed && other.fixed <= span.high;
          if (crosses) cost += settings.crossingPenalty;
        }
      }
      return cost;
    };
  }

  // ---- the grid search ----
  function createHeap() {
    const keys = [];
    const states = [];
    const costs = [];
    return {
      size() {
        return keys.length;
      },
      push(key, state, cost) {
        let index = keys.length;
        keys.push(key);
        states.push(state);
        costs.push(cost);
        while (index > 0) {
          const parent = (index - 1) >> 1;
          if (keys[parent] <= key) break;
          keys[index] = keys[parent];
          states[index] = states[parent];
          costs[index] = costs[parent];
          index = parent;
        }
        keys[index] = key;
        states[index] = state;
        costs[index] = cost;
      },
      pop() {
        const top = { state: states[0], cost: costs[0] };
        const lastKey = keys.pop();
        const lastState = states.pop();
        const lastCost = costs.pop();
        const remaining = keys.length;
        if (remaining > 0) {
          let index = 0;
          const half = remaining >> 1;
          while (index < half) {
            let child = index * 2 + 1;
            if (child + 1 < remaining && keys[child + 1] < keys[child]) child += 1;
            if (keys[child] >= lastKey) break;
            keys[index] = keys[child];
            states[index] = states[child];
            costs[index] = costs[child];
            index = child;
          }
          keys[index] = lastKey;
          states[index] = lastState;
          costs[index] = lastCost;
        }
        return top;
      },
    };
  }

  // The sorted, distinct values of a list that fall inside low..high.
  function axisValues(values, low, high) {
    const inside = [];
    for (const value of values) {
      const snapped = snap(value);
      if (snapped >= low && snapped <= high) inside.push(snapped);
    }
    inside.sort((a, b) => a - b);
    const distinct = [];
    for (const value of inside) {
      if (distinct.length === 0 || distinct[distinct.length - 1] !== value) distinct.push(value);
    }
    return distinct;
  }

  // Cheapest orthogonal path from any source to any target.
  //   sources  {stub, heading, startCost}: where a path may begin, and the way
  //            it must head from there
  //   targets  {stub, heading, endCost}: where it may end, and the way it must
  //            be heading as it does
  // The grid's lines are the window's edges and the edges of every rect and
  // end point, so any path worth taking runs along them. Returns the corner
  // points from a source's stub to a target's stub, or null.
  function searchGrid(request, spanCost, settings) {
    const bounds = request.bounds;
    const xValues = [bounds.left, bounds.right];
    const yValues = [bounds.top, bounds.bottom];
    for (const end of request.sources) {
      xValues.push(end.stub.x);
      yValues.push(end.stub.y);
    }
    for (const end of request.targets) {
      xValues.push(end.stub.x);
      yValues.push(end.stub.y);
    }
    for (const rect of request.gridRects) {
      if (!overlaps(rect, bounds)) continue;
      xValues.push(rect.left, rect.right);
      yValues.push(rect.top, rect.bottom);
    }
    const xs = axisValues(xValues, bounds.left, bounds.right);
    const ys = axisValues(yValues, bounds.top, bounds.bottom);
    const columns = xs.length;
    const rows = ys.length;
    const xIndex = new Map();
    const yIndex = new Map();
    xs.forEach((value, index) => xIndex.set(value, index));
    ys.forEach((value, index) => yIndex.set(value, index));

    const stateCount = columns * rows * 4;
    const bestCost = new Float64Array(stateCount).fill(Infinity);
    const cameFrom = new Int32Array(stateCount).fill(-1);
    const sourceAt = new Map();
    const heap = createHeap();

    const targetNodes = [];
    const targetsAtNode = new Map();
    request.targets.forEach((end, targetIndex) => {
      const column = xIndex.get(snap(end.stub.x));
      const row = yIndex.get(snap(end.stub.y));
      if (column === undefined || row === undefined) return;
      targetNodes.push({ x: end.stub.x, y: end.stub.y });
      const node = column * rows + row;
      if (!targetsAtNode.has(node)) targetsAtNode.set(node, []);
      targetsAtNode.get(node).push(targetIndex);
    });
    if (targetNodes.length === 0) return null;

    function estimate(column, row) {
      let nearest = Infinity;
      for (const target of targetNodes) {
        const distance = Math.abs(xs[column] - target.x) + Math.abs(ys[row] - target.y);
        if (distance < nearest) nearest = distance;
      }
      return nearest;
    }

    request.sources.forEach((end, sourceIndex) => {
      const column = xIndex.get(snap(end.stub.x));
      const row = yIndex.get(snap(end.stub.y));
      if (column === undefined || row === undefined) return;
      const state = (column * rows + row) * 4 + end.heading;
      if (end.startCost >= bestCost[state]) return;
      bestCost[state] = end.startCost;
      sourceAt.set(state, sourceIndex);
      heap.push(end.startCost + estimate(column, row), state, end.startCost);
    });

    // A grid edge's cost is worked out the first time a path wants it.
    const eastCosts = new Float64Array(columns * rows).fill(-1);
    const southCosts = new Float64Array(columns * rows).fill(-1);
    function eastEdge(column, row) {
      const key = column * rows + row;
      if (eastCosts[key] === -1) {
        eastCosts[key] = spanCost({ x: xs[column], y: ys[row] }, { x: xs[column + 1], y: ys[row] });
      }
      return eastCosts[key];
    }
    function southEdge(column, row) {
      const key = column * rows + row;
      if (southCosts[key] === -1) {
        southCosts[key] = spanCost({ x: xs[column], y: ys[row] }, { x: xs[column], y: ys[row + 1] });
      }
      return southCosts[key];
    }

    // Ending at a target is its own kind of state, so that the cheapest end
    // overall comes off the heap first.
    const endBase = stateCount;
    const endCost = new Float64Array(request.targets.length).fill(Infinity);
    const endFrom = new Int32Array(request.targets.length).fill(-1);
    let expansions = 0;

    while (heap.size() > 0) {
      const entry = heap.pop();
      const state = entry.state;
      if (state >= endBase) {
        const targetIndex = state - endBase;
        return tracePath(endFrom[targetIndex], targetIndex, endCost[targetIndex], expansions);
      }
      if (entry.cost > bestCost[state]) continue;
      expansions += 1;
      if (expansions > settings.maxExpansions) return null;

      const heading = state % 4;
      const node = (state - heading) / 4;
      const column = Math.floor(node / rows);
      const row = node % rows;

      const endingHere = targetsAtNode.get(node);
      if (endingHere) {
        for (const targetIndex of endingHere) {
          const target = request.targets[targetIndex];
          if (heading === (target.heading + 2) % 4) continue; // would double back
          let total = entry.cost + target.endCost;
          if (heading !== target.heading) total += settings.bendPenalty;
          if (total < endCost[targetIndex]) {
            endCost[targetIndex] = total;
            endFrom[targetIndex] = state;
            heap.push(total, endBase + targetIndex, total);
          }
        }
      }

      for (const turn of [0, 1, 3]) {
        const nextHeading = (heading + turn) % 4;
        const nextColumn = column + STEP_X[nextHeading];
        const nextRow = row + STEP_Y[nextHeading];
        if (nextColumn < 0 || nextColumn >= columns || nextRow < 0 || nextRow >= rows) continue;
        let edge;
        if (nextHeading === EAST) edge = eastEdge(column, row);
        else if (nextHeading === WEST) edge = eastEdge(nextColumn, row);
        else if (nextHeading === SOUTH) edge = southEdge(column, row);
        else edge = southEdge(column, nextRow);
        if (edge === Infinity) continue;
        let total = entry.cost + edge;
        if (turn !== 0) total += settings.bendPenalty;
        const nextState = (nextColumn * rows + nextRow) * 4 + nextHeading;
        if (total >= bestCost[nextState]) continue;
        bestCost[nextState] = total;
        cameFrom[nextState] = state;
        heap.push(total + estimate(nextColumn, nextRow), nextState, total);
      }
    }
    return null;

    // Walks back from a target's last state to the source, collecting the
    // stub points along the way.
    function tracePath(lastState, targetIndex, cost, expansions) {
      const nodes = [];
      let state = lastState;
      let firstState = lastState;
      while (state !== -1) {
        const heading = state % 4;
        const node = (state - heading) / 4;
        nodes.push({ x: xs[Math.floor(node / rows)], y: ys[node % rows] });
        firstState = state;
        state = cameFrom[state];
      }
      nodes.reverse();
      const source = request.sources[sourceAt.get(firstState)];
      const target = request.targets[targetIndex];
      return { source, target, nodes, cost, expansions };
    }
  }

  // The cheapest line between two sets of ends, or null. A straight line is
  // tried first, since it needs no room for the stubs a bent one starts with.
  function routeLeader(request, settings) {
    const stubCost = makeCostModel(request.stubBlockers, request.softRects, request.placed, settings);
    const gridCost = makeCostModel(request.gridBlockers, request.softRects, request.placed, settings);

    let best = null;
    for (const source of request.sources) {
      for (const target of request.targets) {
        if (source.heading !== target.heading) continue;
        const horizontalTravel = source.heading === EAST || source.heading === WEST;
        const lined = horizontalTravel ? source.dot.y === target.port.y : source.dot.x === target.port.x;
        if (!lined) continue;
        const distance = horizontalTravel ? (target.port.x - source.dot.x) * STEP_X[source.heading] : (target.port.y - source.dot.y) * STEP_Y[source.heading];
        if (distance <= 0) continue;
        const cost = stubCost(source.dot, target.port);
        if (cost === Infinity) continue;
        const total = cost + source.penalty + target.penalty;
        if (best === null || total < best.cost) best = { points: [source.dot, target.port], cost: total };
      }
    }

    const sources = [];
    for (const source of request.sources) {
      const cost = stubCost(source.dot, source.stub);
      if (cost === Infinity) continue;
      sources.push({ stub: source.stub, heading: source.heading, startCost: cost + source.penalty, dot: source.dot });
    }
    const targets = [];
    for (const target of request.targets) {
      const cost = stubCost(target.stub, target.port);
      if (cost === Infinity) continue;
      targets.push({ stub: target.stub, heading: target.heading, endCost: cost + target.penalty, port: target.port });
    }
    if (sources.length > 0 && targets.length > 0) {
      const found = searchGrid({ bounds: request.bounds, gridRects: request.gridRects, sources, targets }, gridCost, settings);
      if (found && (best === null || found.cost < best.cost)) {
        const points = [found.source.dot, ...found.nodes, found.target.port];
        best = { points: simplifyPoints(points), cost: found.cost, expansions: found.expansions };
      }
    }
    return best;
  }

  // ---- where a line may start and end ----
  // Points along an edge, from `low` to `high`, a line may leave from: the
  // middle, the point nearest `pull`, and a few steps either side of that.
  function portPositions(low, high, pull, settings) {
    const margin = Math.min(settings.cornerMargin, (high - low) / 2);
    const first = low + margin;
    const last = high - margin;
    const found = [];
    function add(value) {
      const clamped = snap(Math.min(last, Math.max(first, value)));
      if (found.indexOf(clamped) === -1) found.push(clamped);
    }
    add((low + high) / 2);
    add(pull);
    for (let step = 1; step <= 3; step += 1) {
      add(pull - step * settings.portPitch);
      add(pull + step * settings.portPitch);
    }
    return found;
  }

  function pointAt(horizontalTravel, along, across) {
    if (horizontalTravel) return { x: snap(along), y: snap(across) };
    return { x: snap(across), y: snap(along) };
  }

  // The ways a line can leave `anchor` and meet `label`, for each side the
  // label lies toward. A source is where it leaves (a dot off the control, then
  // a stub to run straight); a target is where it arrives on the label.
  function leaderEnds(anchor, label, settings) {
    const sources = [];
    const targets = [];
    const lying = {};
    lying[NORTH] = label.bottom <= anchor.top;
    lying[SOUTH] = label.top >= anchor.bottom;
    lying[WEST] = label.right <= anchor.left;
    lying[EAST] = label.left >= anchor.right;

    for (const heading of HEADINGS) {
      if (!lying[heading]) continue;
      const horizontalTravel = heading === EAST || heading === WEST;
      const direction = horizontalTravel ? STEP_X[heading] : STEP_Y[heading];
      let anchorEdge;
      let labelEdge;
      let anchorLow;
      let anchorHigh;
      let labelLow;
      let labelHigh;
      if (heading === NORTH) {
        anchorEdge = anchor.top;
        labelEdge = label.bottom;
      } else if (heading === SOUTH) {
        anchorEdge = anchor.bottom;
        labelEdge = label.top;
      } else if (heading === WEST) {
        anchorEdge = anchor.left;
        labelEdge = label.right;
      } else {
        anchorEdge = anchor.right;
        labelEdge = label.left;
      }
      if (horizontalTravel) {
        anchorLow = anchor.top;
        anchorHigh = anchor.bottom;
        labelLow = label.top;
        labelHigh = label.bottom;
      } else {
        anchorLow = anchor.left;
        anchorHigh = anchor.right;
        labelLow = label.left;
        labelHigh = label.right;
      }

      const labelCenter = (labelLow + labelHigh) / 2;
      const anchorPositions = portPositions(anchorLow, anchorHigh, labelCenter, settings);
      for (const position of anchorPositions) {
        sources.push({
          dot: pointAt(horizontalTravel, anchorEdge + direction * settings.portGap, position),
          stub: pointAt(horizontalTravel, anchorEdge + direction * settings.stubLength, position),
          heading,
          penalty: 0,
        });
      }

      // A label can be met at its middle, or level with a port, so that the
      // line runs straight.
      const labelMargin = Math.min(settings.cornerMargin, (labelHigh - labelLow) / 2);
      const labelPositions = [snap(labelCenter)];
      for (const position of anchorPositions) {
        const reachable = position >= labelLow + labelMargin && position <= labelHigh - labelMargin;
        if (reachable && labelPositions.indexOf(position) === -1) labelPositions.push(position);
      }
      for (const position of labelPositions) {
        targets.push({
          port: pointAt(horizontalTravel, labelEdge, position),
          stub: pointAt(horizontalTravel, labelEdge - direction * settings.stubLength, position),
          heading,
          penalty: position === snap(labelCenter) ? 0 : settings.offCenterPenalty,
        });
      }
    }
    return { sources, targets };
  }

  // ---- candidates ----
  // Where along a side a label may start: centred on the control and stepping
  // either way; lined up with the control's near and far edges; and stopping
  // just short of each other lit control, or starting just past it, so that a
  // row of controls can each have a label that clears the lines of the rest.
  // Those last are tagged, so each is kept rather than crowded out by the
  // many that are almost centred.
  function labelStarts(anchor, size, side, neighbors, inner, settings) {
    const starts = [];
    const horizontalSide = side === NORTH || side === SOUTH;
    let center;
    let alignedLow;
    let alignedHigh;
    let extent;
    if (horizontalSide) {
      center = (anchor.left + anchor.right) / 2 - size.width / 2;
      alignedLow = anchor.left;
      alignedHigh = anchor.right - size.width;
      extent = size.width;
    } else {
      center = (anchor.top + anchor.bottom) / 2 - size.height / 2;
      alignedLow = anchor.top;
      alignedHigh = anchor.bottom - size.height;
      extent = size.height;
    }
    for (let step = -settings.lateralSteps; step <= settings.lateralSteps; step += 1) {
      starts.push({ value: Math.round(center + step * settings.lateralStep), tag: step === 0 ? "center" : "" });
    }
    starts.push({ value: Math.round(alignedLow), tag: "low" });
    starts.push({ value: Math.round(alignedHigh), tag: "high" });
    // Flush with the window's edges, for a control near one.
    if (horizontalSide) {
      starts.push({ value: inner.left, tag: "edge-low" });
      starts.push({ value: inner.right - extent, tag: "edge-high" });
    } else {
      starts.push({ value: inner.top, tag: "edge-low" });
      starts.push({ value: inner.bottom - extent, tag: "edge-high" });
    }
    neighbors.forEach((neighbor, index) => {
      let nearEdge;
      let farEdge;
      let distance;
      if (horizontalSide) {
        nearEdge = neighbor.left;
        farEdge = neighbor.right;
        distance = Math.abs((neighbor.left + neighbor.right) / 2 - (anchor.left + anchor.right) / 2);
      } else {
        nearEdge = neighbor.top;
        farEdge = neighbor.bottom;
        distance = Math.abs((neighbor.top + neighbor.bottom) / 2 - (anchor.top + anchor.bottom) / 2);
      }
      if (distance > settings.snapReach) return;
      starts.push({ value: Math.round(nearEdge - settings.snapGap - extent), tag: `before-${index}` });
      starts.push({ value: Math.round(farEdge + settings.snapGap), tag: `after-${index}` });
    });
    return starts;
  }

  // The thin strip a line would most likely take from the control to the label,
  // straight out of the control toward the label. A label placed across it
  // makes the candidate worse, and enough of them make a step unplaceable.
  function corridorOf(anchor, rect, side) {
    const half = 3;
    if (side === NORTH || side === SOUTH) {
      const labelCenter = (rect.left + rect.right) / 2;
      const across = Math.min(anchor.right - 4, Math.max(anchor.left + 4, labelCenter));
      if (side === NORTH) return makeRect(across - half, rect.bottom, half * 2, anchor.top - rect.bottom);
      return makeRect(across - half, anchor.bottom, half * 2, rect.top - anchor.bottom);
    }
    const labelCenter = (rect.top + rect.bottom) / 2;
    const across = Math.min(anchor.bottom - 4, Math.max(anchor.top + 4, labelCenter));
    if (side === WEST) return makeRect(rect.right, across - half, anchor.left - rect.right, half * 2);
    return makeRect(anchor.right, across - half, rect.left - anchor.right, half * 2);
  }

  // The distances to try along one side: the fixed ladder, plus the ones that
  // put the label flush against the edge of an obstacle in the way, which a
  // ladder can miss by a fraction of a pixel.
  function gapsFor(anchor, side, world, settings) {
    const nearest = settings.gaps[0];
    const farthest = settings.gaps[settings.gaps.length - 1];
    const extra = [];
    for (const blocker of world.labelBlockers) {
      let gap;
      let reaches;
      if (side === SOUTH) {
        gap = blocker.bottom - anchor.bottom;
        reaches = blocker.right > anchor.left - settings.snapReach && blocker.left < anchor.right + settings.snapReach;
      } else if (side === NORTH) {
        gap = anchor.top - blocker.top;
        reaches = blocker.right > anchor.left - settings.snapReach && blocker.left < anchor.right + settings.snapReach;
      } else if (side === EAST) {
        gap = blocker.right - anchor.right;
        reaches = blocker.bottom > anchor.top - settings.snapReach && blocker.top < anchor.bottom + settings.snapReach;
      } else {
        gap = anchor.left - blocker.left;
        reaches = blocker.bottom > anchor.top - settings.snapReach && blocker.top < anchor.bottom + settings.snapReach;
      }
      if (!reaches || gap < nearest || gap > farthest) continue;
      if (settings.gaps.indexOf(gap) === -1 && extra.indexOf(gap) === -1) extra.push(gap);
    }
    extra.sort((a, b) => a - b);
    return settings.gaps.concat(extra.slice(0, settings.snappedGaps));
  }

  // The distances that put a label a fixed gap beyond each label already
  // placed, so rows and columns of labels pack flush and line up.
  function flushGapsFor(anchor, side, placedRects, settings) {
    const nearest = settings.gaps[0];
    const farthest = settings.gaps[settings.gaps.length - 1] * 1.5;
    const found = [];
    for (const placed of placedRects) {
      let gap;
      let reaches;
      if (side === SOUTH) {
        gap = placed.bottom + settings.labelGap - anchor.bottom;
        reaches = placed.right > anchor.left - settings.snapReach && placed.left < anchor.right + settings.snapReach;
      } else if (side === NORTH) {
        gap = anchor.top - (placed.top - settings.labelGap);
        reaches = placed.right > anchor.left - settings.snapReach && placed.left < anchor.right + settings.snapReach;
      } else if (side === EAST) {
        gap = placed.right + settings.labelGap - anchor.right;
        reaches = placed.bottom > anchor.top - settings.snapReach && placed.top < anchor.bottom + settings.snapReach;
      } else {
        gap = anchor.left - (placed.left - settings.labelGap);
        reaches = placed.bottom > anchor.top - settings.snapReach && placed.top < anchor.bottom + settings.snapReach;
      }
      if (!reaches || gap < nearest || gap > farthest) continue;
      if (found.indexOf(gap) === -1) found.push(gap);
    }
    found.sort((a, b) => a - b);
    return found.slice(0, settings.snappedGaps);
  }

  function placeLabel(anchor, size, side, gap, start) {
    if (side === NORTH) return makeRect(start, anchor.top - gap - size.height, size.width, size.height);
    if (side === SOUTH) return makeRect(start, anchor.bottom + gap, size.width, size.height);
    if (side === WEST) return makeRect(anchor.left - gap - size.width, start, size.width, size.height);
    return makeRect(anchor.right + gap, start, size.width, size.height);
  }

  // Where a line leaving `anchor` on `side` could start its straight run: the
  // stub ends beyond each of a few points along that edge. Those a lit control
  // or the tour's buttons sit across are dropped; with none left, nothing can
  // leave on that side at all.
  function launchPointsFor(anchor, side, stepIndex, anchorIndex, world, settings) {
    const horizontalSide = side === EAST || side === WEST;
    let low;
    let high;
    let edge;
    let direction;
    if (side === NORTH) {
      low = anchor.left;
      high = anchor.right;
      edge = anchor.top;
      direction = -1;
    } else if (side === SOUTH) {
      low = anchor.left;
      high = anchor.right;
      edge = anchor.bottom;
      direction = 1;
    } else if (side === WEST) {
      low = anchor.top;
      high = anchor.bottom;
      edge = anchor.left;
      direction = -1;
    } else {
      low = anchor.top;
      high = anchor.bottom;
      edge = anchor.right;
      direction = 1;
    }
    const points = [];
    for (const position of portPositions(low, high, (low + high) / 2, settings)) {
      const dot = pointAt(horizontalSide, edge + direction * settings.portGap, position);
      const stub = pointAt(horizontalSide, edge + direction * settings.stubLength, position);
      let blocked = false;
      for (const lit of world.lit) {
        if (lit.stepIndex === stepIndex && lit.anchorIndex === anchorIndex) continue;
        const zone = inflate(lit.rect, settings.clearance);
        if (spanLengthInside(zone, makeSpan(dot, stub)) > 0) blocked = true;
        if (stub.x > zone.left && stub.x < zone.right && stub.y > zone.top && stub.y < zone.bottom) blocked = true;
      }
      for (const control of world.controls) {
        const zone = inflate(control, settings.clearance);
        if (spanLengthInside(zone, makeSpan(dot, stub)) > 0) blocked = true;
      }
      if (!blocked) points.push(stub);
    }
    return points;
  }

  // Every label rectangle worth trying for one step, cheapest first. The
  // cheapest are all next to the control, which is no use when something
  // else needs that spot, so the survivors are chosen per side and
  // distance rather than overall.
  function candidatesFor(step, stepIndex, world, settings, placedRects) {
    const found = [];
    const seen = new Set();
    const launches = new Map();
    const neighbors = [];
    for (const lit of world.lit) {
      if (lit.stepIndex !== stepIndex) neighbors.push(lit.rect);
    }
    for (let anchorIndex = 0; anchorIndex < step.anchors.length; anchorIndex += 1) {
      const anchor = step.anchors[anchorIndex];
      for (let sizeIndex = 0; sizeIndex < step.sizes.length; sizeIndex += 1) {
        const size = step.sizes[sizeIndex];
        for (const side of HEADINGS) {
          const launchKey = `${anchorIndex},${side}`;
          if (!launches.has(launchKey)) launches.set(launchKey, launchPointsFor(anchor, side, stepIndex, anchorIndex, world, settings));
          const launch = launches.get(launchKey);
          if (launch.length === 0) continue; // nothing can leave the control this way
          let starts = labelStarts(anchor, size, side, neighbors, world.inner, settings);
          let gaps;
          if (placedRects) {
            gaps = flushGapsFor(anchor, side, placedRects, settings);
            starts = starts.filter((start) => start.tag !== "");
            for (const placed of placedRects) {
              const horizontalSide = side === NORTH || side === SOUTH;
              const extent = horizontalSide ? size.width : size.height;
              const low = horizontalSide ? placed.left : placed.top;
              const high = horizontalSide ? placed.right : placed.bottom;
              starts.push({ value: Math.round(low), tag: "with-low" });
              starts.push({ value: Math.round(high - extent), tag: "with-high" });
            }
          } else {
            gaps = gapsFor(anchor, side, world, settings);
          }
          for (const gap of gaps) {
            for (const start of starts) {
              const rect = placeLabel(anchor, size, side, gap, start.value);
              const key = `${anchorIndex},${sizeIndex},${rect.left},${rect.top}`;
              if (seen.has(key)) continue;
              seen.add(key);
              if (!isInside(world.inner, rect)) continue;
              if (coversAny(rect, world.labelBlockers)) continue;
              const staticCost = settings.sizePenalty * sizeIndex + softLabelCost(rect, world.labelSoft);
              const along = side === NORTH || side === SOUTH ? Math.abs(rect.left + size.width / 2 - (anchor.left + anchor.right) / 2) : Math.abs(rect.top + size.height / 2 - (anchor.top + anchor.bottom) / 2);
              const guess = staticCost + Math.max(0, rectGap(anchor, rect) - settings.portGap) + along * 0.02;
              found.push({ rect, padded: inflate(rect, 4), corridor: corridorOf(anchor, rect, side), launch, sizeIndex, anchorIndex, side, gap, tag: start.tag, staticCost, guess });
            }
          }
        }
      }
    }
    found.sort((a, b) => a.guess - b.guess);

    const taken = new Map();
    const kept = [];
    for (const candidate of found) {
      const bucket = `${candidate.anchorIndex},${candidate.side},${candidate.gap},${candidate.tag}`;
      const quota = candidate.tag === "" ? settings.candidatesPerBucket : settings.alignedPerBucket;
      const count = taken.get(bucket) || 0;
      if (count >= quota) continue;
      taken.set(bucket, count + 1);
      kept.push(candidate);
    }
    return kept;
  }

  function coversAny(rect, blockers) {
    for (const blocker of blockers) {
      if (overlaps(rect, blocker)) return true;
    }
    return false;
  }

  function softLabelCost(rect, softRects) {
    let cost = 0;
    for (const soft of softRects) {
      const area = overlapArea(rect, soft.rect);
      if (area > 0) cost += area * soft.perArea + soft.perShare * Math.min(1, area / soft.itemArea);
    }
    return cost;
  }

  // ---- the page, as the search sees it ----
  function buildWorld(scene, settings, pass) {
    const viewport = scene.viewport;
    const inner = inflate(viewport, -settings.edgeMargin);
    const world = { inner, lit: [], controls: [], labelBlockers: [], labelSoft: [], lineSoft: [] };

    scene.steps.forEach((step, stepIndex) => {
      step.anchors.forEach((anchor, anchorIndex) => {
        world.lit.push({ stepIndex, anchorIndex, rect: anchor });
        world.labelBlockers.push(inflate(anchor, settings.labelClearance));
      });
    });
    for (const control of scene.controls) {
      world.controls.push(control);
      world.labelBlockers.push(inflate(control, settings.labelClearance));
    }
    for (const region of scene.raised) {
      world.labelBlockers.push(inflate(region, settings.labelClearance));
      world.lineSoft.push({ rect: region, perPixel: settings.raisedLinePenalty });
    }
    for (const item of scene.salient) {
      const isControl = item.kind === "control";
      if (isControl && pass.controlsBlockLabels) {
        world.labelBlockers.push(inflate(item.rect, settings.labelClearance));
      } else {
        const perArea = isControl ? settings.controlLabelPenalty : settings.contentLabelPenalty;
        const perShare = isControl ? settings.controlLabelShare : settings.contentLabelShare;
        const itemArea = Math.max(1, (item.rect.right - item.rect.left) * (item.rect.bottom - item.rect.top));
        world.labelSoft.push({ rect: item.rect, perArea, perShare, itemArea });
      }
      const perPixel = isControl ? settings.controlLinePenalty : settings.contentLinePenalty;
      const padding = isControl ? settings.linePadding : 0;
      world.lineSoft.push({ rect: inflate(item.rect, padding), perPixel });
    }
    return world;
  }

  function alignmentScore(rect, placedRects, settings) {
    let aligned = 0;
    for (const other of placedRects) {
      const sharesEdge = Math.abs(other.left - rect.left) < 0.5 || Math.abs(other.right - rect.right) < 0.5 || Math.abs(other.top - rect.top) < 0.5 || Math.abs(other.bottom - rect.bottom) < 0.5;
      if (sharesEdge) aligned += 1;
    }
    return Math.min(aligned, 2) * settings.alignmentBonus;
  }

  // Whether a label at `rect` clears the labels and lines already placed.
  function fitsState(rect, state, settings) {
    for (const placed of state.rects) {
      if (overlaps(rect, inflate(placed, settings.labelGap))) return false;
    }
    const padded = inflate(rect, 4);
    for (const span of state.spans) {
      if (spanLengthInside(padded, span) > 0) return false;
    }
    return true;
  }

  function routeBounds(ends, world, settings) {
    let left = Infinity;
    let top = Infinity;
    let right = -Infinity;
    let bottom = -Infinity;
    const all = ends.sources.concat(ends.targets);
    for (const end of all) {
      const points = [end.stub, end.dot || end.port];
      for (const point of points) {
        left = Math.min(left, point.x);
        top = Math.min(top, point.y);
        right = Math.max(right, point.x);
        bottom = Math.max(bottom, point.y);
      }
    }
    const margin = settings.routeMargin;
    return snapRect({
      left: Math.max(world.inner.left, left - margin),
      top: Math.max(world.inner.top, top - margin),
      right: Math.min(world.inner.right, right + margin),
      bottom: Math.min(world.inner.bottom, bottom + margin),
    });
  }

  function insideBounds(point, bounds) {
    return point.x >= bounds.left && point.x <= bounds.right && point.y >= bounds.top && point.y <= bounds.bottom;
  }

  // The best line from one candidate label to its control, in this state.
  function routeCandidate(stepIndex, step, candidate, state, world, settings) {
    const anchor = step.anchors[candidate.anchorIndex];
    const ends = leaderEnds(anchor, candidate.rect, settings);
    const bounds = routeBounds(ends, world, settings);
    const sources = [];
    for (const source of ends.sources) {
      if (insideBounds(source.stub, bounds) && insideBounds(source.dot, bounds)) sources.push(source);
    }
    const targets = [];
    for (const target of ends.targets) {
      if (insideBounds(target.stub, bounds) && insideBounds(target.port, bounds)) targets.push(target);
    }
    if (sources.length === 0 || targets.length === 0) return null;

    const stubBlockers = [];
    const gridRects = [];
    function addBlocker(rect) {
      const padded = inflate(rect, settings.clearance);
      if (!overlaps(padded, bounds)) return;
      stubBlockers.push(padded);
    }
    for (const lit of world.lit) {
      const isOwn = lit.stepIndex === stepIndex && lit.anchorIndex === candidate.anchorIndex;
      if (!isOwn) addBlocker(lit.rect);
    }
    for (const control of world.controls) addBlocker(control);
    for (const placed of state.rects) addBlocker(placed);

    const gridBlockers = stubBlockers.slice();
    gridBlockers.push(inflate(anchor, settings.clearance));
    gridBlockers.push(inflate(candidate.rect, settings.clearance));
    for (const blocker of gridBlockers) gridRects.push(blocker);

    const softRects = [];
    for (const soft of world.lineSoft) {
      if (!overlaps(soft.rect, bounds)) continue;
      softRects.push(soft);
      gridRects.push(soft.rect);
    }
    const placed = [];
    for (const span of state.spans) {
      const reaches = span.horizontal
        ? span.fixed >= bounds.top && span.fixed <= bounds.bottom && span.high >= bounds.left && span.low <= bounds.right
        : span.fixed >= bounds.left && span.fixed <= bounds.right && span.high >= bounds.top && span.low <= bounds.bottom;
      if (reaches) placed.push(span);
    }

    return routeLeader({ sources, targets, bounds, stubBlockers, gridBlockers, gridRects, softRects, placed }, settings);
  }

  // ---- the search over steps ----
  // Whether the zone around a label is over every one of the points.
  function coversEveryPoint(zone, points) {
    for (const point of points) {
      const inside = point.x > zone.left && point.x < zone.right && point.y > zone.top && point.y < zone.bottom;
      if (!inside) return false;
    }
    return true;
  }

  // How many of each unplaced step's candidates survive a new label and line:
  // those that neither overlap the label nor have the label across their
  // corridor, nor the line through their label. Returns null when some step is
  // left with none, else the surviving flags and counts, and what the loss of
  // places costs.
  function opennessAfter(state, stepIndex, rect, spans, world, settings) {
    const candidateLists = world.candidateLists;
    const flags = state.flags.slice();
    const counts = state.counts.slice();
    flags[stepIndex] = null;
    counts[stepIndex] = 0;
    const labelZone = inflate(rect, settings.labelGap);
    const corridorZone = inflate(rect, 2);
    const launchZone = inflate(rect, settings.clearance);
    let spanReach = null;
    if (spans.length > 0) {
      spanReach = { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity };
      for (const span of spans) {
        const low = span.horizontal ? { x: span.low, y: span.fixed } : { x: span.fixed, y: span.low };
        const high = span.horizontal ? { x: span.high, y: span.fixed } : { x: span.fixed, y: span.high };
        spanReach.left = Math.min(spanReach.left, low.x - 4, high.x - 4);
        spanReach.top = Math.min(spanReach.top, low.y - 4, high.y - 4);
        spanReach.right = Math.max(spanReach.right, low.x + 4, high.x + 4);
        spanReach.bottom = Math.max(spanReach.bottom, low.y + 4, high.y + 4);
      }
    }
    let lost = 0;
    for (let other = 0; other < flags.length; other += 1) {
      if (other === stepIndex || flags[other] === null) continue;
      // Nothing of this step's lies where the label or line went: no change.
      const reach = world.candidateBounds[other];
      const touched = overlaps(reach, labelZone) || (spanReach !== null && overlaps(reach, spanReach));
      if (!touched) continue;
      const before = flags[other];
      const after = new Uint8Array(before.length);
      const candidates = candidateLists[other];
      let alive = 0;
      for (let index = 0; index < candidates.length; index += 1) {
        if (before[index] === 0) continue;
        const candidate = candidates[index];
        if (overlaps(candidate.rect, labelZone) || overlaps(candidate.corridor, corridorZone)) continue;
        if (coversEveryPoint(launchZone, candidate.launch)) continue;
        let crossed = false;
        for (const span of spans) {
          if (spanLengthInside(candidate.padded, span) > 0) {
            crossed = true;
            break;
          }
        }
        if (crossed) continue;
        after[index] = 1;
        alive += 1;
      }
      if (alive === 0) return null;
      flags[other] = after;
      lost += (counts[other] - alive) / candidates.length;
      counts[other] = alive;
    }
    return { flags, counts, cost: lost * settings.openPenalty };
  }

  function extendState(state, stepIndex, step, candidate, route, settings, open) {
    const cost = state.cost + route.cost + candidate.staticCost - alignmentScore(candidate.rect, state.rects, settings) + (open ? open.cost : 0);
    const placement = {
      id: step.id,
      stepIndex,
      rect: candidate.rect,
      sizeIndex: candidate.sizeIndex,
      anchorIndex: candidate.anchorIndex,
      points: route.points,
    };
    return {
      cost,
      placements: state.placements.concat([placement]),
      rects: state.rects.concat([candidate.rect]),
      spans: state.spans.concat(spansOf(route.points)),
      flags: open ? open.flags : state.flags,
      counts: open ? open.counts : state.counts,
    };
  }

  // A step's candidates in a given state: its fixed ones, and those that sit
  // flush against the labels already placed, cheapest first.
  function candidatesInState(stepIndex, step, state, world, settings) {
    const fixed = world.candidateLists[stepIndex];
    if (state.rects.length === 0) return fixed;
    const flush = candidatesFor(step, stepIndex, world, settings, state.rects);
    if (flush.length === 0) return fixed;
    if (!world.candidateKeys) world.candidateKeys = [];
    if (!world.candidateKeys[stepIndex]) {
      const keys = new Set();
      for (const candidate of fixed) keys.add(`${candidate.anchorIndex},${candidate.sizeIndex},${candidate.rect.left},${candidate.rect.top}`);
      world.candidateKeys[stepIndex] = keys;
    }
    const known = world.candidateKeys[stepIndex];
    const merged = fixed.slice();
    for (const candidate of flush) {
      if (!known.has(`${candidate.anchorIndex},${candidate.sizeIndex},${candidate.rect.left},${candidate.rect.top}`)) merged.push(candidate);
    }
    merged.sort((a, b) => a.guess - b.guess);
    return merged;
  }

  // The candidates to try in a state, best first: the cheapest few that fit,
  // each charged for the places it takes from the steps still to come, with
  // those that would leave a step nowhere dropped.
  function rankedCandidates(stepIndex, step, state, world, settings) {
    const ranked = [];
    let taken = 0;
    for (const candidate of candidatesInState(stepIndex, step, state, world, settings)) {
      if (taken >= settings.rankPool) break;
      if (!fitsState(candidate.rect, state, settings)) continue;
      taken += 1;
      const open = opennessAfter(state, stepIndex, candidate.rect, [], world, settings);
      if (open === null) continue;
      ranked.push({ candidate, rank: candidate.guess + open.cost });
    }
    ranked.sort((a, b) => a.rank - b.rank);
    return ranked;
  }

  // The box around everything a step's candidates cover: labels, corridors and
  // launch points, with room for the gap kept between labels.
  function boundsOfCandidates(candidates, settings) {
    const bounds = { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity };
    for (const candidate of candidates) {
      for (const rect of [candidate.rect, candidate.corridor]) {
        bounds.left = Math.min(bounds.left, rect.left);
        bounds.top = Math.min(bounds.top, rect.top);
        bounds.right = Math.max(bounds.right, rect.right);
        bounds.bottom = Math.max(bounds.bottom, rect.bottom);
      }
      for (const point of candidate.launch) {
        bounds.left = Math.min(bounds.left, point.x);
        bounds.top = Math.min(bounds.top, point.y);
        bounds.right = Math.max(bounds.right, point.x);
        bounds.bottom = Math.max(bounds.bottom, point.y);
      }
    }
    return inflate(bounds, settings.labelGap);
  }

  function emptyState(candidateLists) {
    const flags = [];
    const counts = [];
    for (const candidates of candidateLists) {
      flags.push(new Uint8Array(candidates.length).fill(1));
      counts.push(candidates.length);
    }
    return { cost: 0, placements: [], rects: [], spans: [], flags, counts };
  }

  // The usual search: a beam over the steps in a given order. Keeps the
  // cheapest few partial layouts at each step, so it finds good ones, but
  // they can all box in a later step the same way, and which steps come first
  // decides who gets boxed in.
  function beamSearch(scene, world, order, settings, stats) {
    const candidateLists = world.candidateLists;
    let beam = [emptyState(candidateLists)];
    for (const stepIndex of order) {
      const step = scene.steps[stepIndex];
      const children = [];
      for (const state of beam) {
        let attempts = 0;
        let successes = 0;
        for (const { candidate } of rankedCandidates(stepIndex, step, state, world, settings)) {
          if (attempts >= settings.attemptsPerState || successes >= settings.successesPerState) break;
          attempts += 1;
          stats.routes += 1;
          const route = routeCandidate(stepIndex, step, candidate, state, world, settings);
          if (route === null) continue;
          const open = opennessAfter(state, stepIndex, candidate.rect, spansOf(route.points), world, settings);
          if (open === null) continue;
          successes += 1;
          children.push(extendState(state, stepIndex, step, candidate, route, settings, open));
        }
      }
      if (children.length === 0) return { failedId: step.id };
      children.sort((a, b) => a.cost - b.cost);
      beam = children.slice(0, settings.beamWidth);
    }
    return { state: beam[0] };
  }

  // The orders worth trying the beam in: the step with least room first, and
  // each sweep across the page. A row of controls laid out left to right wants
  // its labels placed in that order, or the later ones are boxed in by the
  // earlier; no single order suits every page.
  function stepOrders(scene, candidateLists, settings) {
    const indices = [];
    const centers = [];
    for (let stepIndex = 0; stepIndex < scene.steps.length; stepIndex += 1) {
      indices.push(stepIndex);
      const anchor = scene.steps[stepIndex].anchors[0];
      centers.push({ x: (anchor.left + anchor.right) / 2, y: (anchor.top + anchor.bottom) / 2 });
    }
    function sorted(compare) {
      const order = indices.slice();
      order.sort(compare);
      return order;
    }
    const byName = {
      constrained: () => sorted((a, b) => candidateLists[a].length - candidateLists[b].length || a - b),
      "x-asc": () => sorted((a, b) => centers[a].x - centers[b].x || a - b),
      "x-desc": () => sorted((a, b) => centers[b].x - centers[a].x || a - b),
      "y-asc": () => sorted((a, b) => centers[a].y - centers[b].y || a - b),
      "y-desc": () => sorted((a, b) => centers[b].y - centers[a].y || a - b),
    };
    const candidates = [];
    for (const name of settings.orders) candidates.push(byName[name]());
    const orders = [];
    const seen = new Set();
    for (const order of candidates) {
      const key = order.join(",");
      if (seen.has(key)) continue;
      seen.add(key);
      orders.push(order);
    }
    return orders;
  }

  // The fallback, for when the beam boxes itself in: depth-first with
  // backtracking. At each point it takes the step with the fewest labels that
  // still fit (so the tightest goes first), gives up on a branch the moment
  // some step has none left, and otherwise tries that step's candidates
  // cheapest first. It stops after `fallbackRoutes` routes.
  function backtrackSearch(scene, world, candidateLists, settings, stats) {
    let routesLeft = settings.fallbackRoutes;
    let deepestFailure = null;

    function extend(state, unplaced) {
      if (unplaced.length === 0) return state;
      let chosen = -1;
      let chosenFits = null;
      for (const stepIndex of unplaced) {
        const fitting = [];
        for (const candidate of candidatesInState(stepIndex, scene.steps[stepIndex], state, world, settings)) {
          if (fitsState(candidate.rect, state, settings)) fitting.push(candidate);
        }
        if (fitting.length === 0) {
          deepestFailure = scene.steps[stepIndex].id;
          return null;
        }
        if (chosenFits === null || fitting.length < chosenFits.length) {
          chosen = stepIndex;
          chosenFits = fitting;
        }
      }
      const rest = [];
      for (const stepIndex of unplaced) {
        if (stepIndex !== chosen) rest.push(stepIndex);
      }
      const step = scene.steps[chosen];
      let tried = 0;
      for (const candidate of chosenFits) {
        if (routesLeft <= 0 || tried >= settings.fallbackBranching) return null;
        routesLeft -= 1;
        stats.routes += 1;
        const route = routeCandidate(chosen, step, candidate, state, world, settings);
        if (route === null) continue;
        tried += 1;
        const finished = extend(extendState(state, chosen, step, candidate, route, settings), rest);
        if (finished) return finished;
      }
      deepestFailure = step.id;
      return null;
    }

    const everyStep = [];
    for (let stepIndex = 0; stepIndex < scene.steps.length; stepIndex += 1) everyStep.push(stepIndex);
    const state = extend(emptyState(candidateLists), everyStep);
    if (state) return { state };
    return { failedId: deepestFailure };
  }

  function solvePass(scene, settings, pass, stats) {
    const world = buildWorld(scene, settings, pass);
    const candidateLists = [];
    for (let stepIndex = 0; stepIndex < scene.steps.length; stepIndex += 1) {
      const candidates = candidatesFor(scene.steps[stepIndex], stepIndex, world, settings);
      if (candidates.length === 0) return { ok: false, failedId: scene.steps[stepIndex].id };
      candidateLists.push(candidates);
    }

    world.candidateLists = candidateLists;
    world.candidateBounds = candidateLists.map((candidates) => boundsOfCandidates(candidates, settings));
    let best = null;
    let failedId = null;
    for (const order of stepOrders(scene, candidateLists, settings)) {
      const found = beamSearch(scene, world, order, settings, stats);
      if (found.state && (best === null || found.state.cost < best.cost)) best = found.state;
      if (!found.state) failedId = found.failedId;
    }
    let search = "beam";
    if (best === null) {
      const found = backtrackSearch(scene, world, candidateLists, settings, stats);
      if (!found.state) return { ok: false, failedId: found.failedId || failedId };
      best = found.state;
      search = "backtrack";
    }

    const placements = best.placements.slice();
    placements.sort((a, b) => a.stepIndex - b.stepIndex);
    return { ok: true, placements, cost: best.cost, pass: pass.name, search };
  }

  // The scene with every rect and size snapped, so that edges, ports and the
  // lines between them agree exactly even when the page's boxes have fractions.
  function normalizeScene(scene) {
    const steps = [];
    for (const step of scene.steps) {
      const anchors = [];
      for (const anchor of step.anchors) anchors.push(snapRect(anchor));
      const sizes = [];
      for (const size of step.sizes) sizes.push({ width: snap(size.width), height: snap(size.height) });
      steps.push({ id: step.id, anchors, sizes });
    }
    const controls = [];
    for (const control of scene.controls) controls.push(snapRect(control));
    const salient = [];
    for (const item of scene.salient) salient.push({ rect: snapRect(item.rect), kind: item.kind });
    const raised = [];
    for (const region of scene.raised || []) raised.push(snapRect(region));
    return { viewport: snapRect(scene.viewport), controls, salient, raised, steps };
  }

  const PASSES = [
    { name: "strict", controlsBlockLabels: true },
    { name: "relaxed", controlsBlockLabels: false },
  ];

  function solve(rawScene, overrides) {
    const settings = Object.assign({}, DEFAULTS, overrides || {});
    const scene = normalizeScene(rawScene);
    const stats = { routes: 0 };
    if (scene.steps.length === 0) return { ok: true, placements: [], cost: 0, pass: "strict", stats };
    let failure = { ok: false };
    for (const pass of PASSES) {
      const outcome = solvePass(scene, settings, pass, stats);
      outcome.stats = stats;
      if (outcome.ok) return outcome;
      failure = outcome;
    }
    return failure;
  }

  // ---- how tidy a layout is ----
  // The turns in a line that a reader sees. A jog shorter than `shortest`
  // reads as a rounded corner, not a turn, so such segments are left out.
  function visibleBends(points, shortest) {
    const segments = [];
    for (let index = 1; index < points.length; index += 1) {
      const span = makeSpan(points[index - 1], points[index]);
      if (span.high - span.low >= shortest) segments.push(span);
    }
    let bends = 0;
    for (let index = 1; index < segments.length; index += 1) {
      if (segments[index].horizontal !== segments[index - 1].horizontal) bends += 1;
    }
    return bends;
  }

  // The most visible bends in any one line, and how many times lines cross, so
  // that a caller can prefer a plain list to a tangle.
  function quality(solution) {
    let mostBends = 0;
    const all = [];
    for (const placement of solution.placements) {
      mostBends = Math.max(mostBends, visibleBends(placement.points, DEFAULTS.cornerRadius + 4));
      for (const span of spansOf(placement.points)) all.push({ owner: placement.id, span });
    }
    let crossings = 0;
    for (let first = 0; first < all.length; first += 1) {
      for (let second = first + 1; second < all.length; second += 1) {
        const a = all[first];
        const b = all[second];
        if (a.owner === b.owner || a.span.horizontal === b.span.horizontal) continue;
        const horizontal = a.span.horizontal ? a.span : b.span;
        const vertical = a.span.horizontal ? b.span : a.span;
        const crosses = horizontal.low <= vertical.fixed && vertical.fixed <= horizontal.high && vertical.low <= horizontal.fixed && horizontal.fixed <= vertical.high;
        if (crosses) crossings += 1;
      }
    }
    return { mostBends, crossings };
  }

  // ---- checking a layout ----
  // A hair of overlap is rounding, not a collision.
  const TOLERANCE = 0.005;

  function collides(a, b) {
    return overlaps(inflate(a, -TOLERANCE), b);
  }

  function runsOver(rect, span) {
    return spanLengthInside(inflate(rect, -TOLERANCE), span) > 0;
  }


  // Re-derives every rule from the scene and the result alone, sharing no code
  // with the search. Returns a list of what's wrong; empty means sound.
  function validate(rawScene, solution, overrides) {
    const settings = Object.assign({}, DEFAULTS, overrides || {});
    const scene = normalizeScene(rawScene);
    const problems = [];
    if (!solution.ok) return ["the layout failed"];
    const strict = solution.pass === "strict";
    const inner = inflate(scene.viewport, -settings.edgeMargin);
    const lit = [];
    for (const step of scene.steps) {
      for (const anchor of step.anchors) lit.push(anchor);
    }
    if (solution.placements.length !== scene.steps.length) {
      problems.push(`${solution.placements.length} placements for ${scene.steps.length} steps`);
    }

    const allSpans = [];
    for (const placement of solution.placements) {
      const name = placement.id;
      const rect = placement.rect;
      const step = scene.steps.find((candidate) => candidate.id === name);
      if (!step) {
        problems.push(`${name}: not a step in the scene`);
        continue;
      }
      const size = step.sizes[placement.sizeIndex];
      if (!size || Math.abs(rectSize(rect).width - size.width) > 0.01 || Math.abs(rectSize(rect).height - size.height) > 0.01) {
        problems.push(`${name}: label is not the size it was given`);
      }
      if (!isInside(inner, rect)) problems.push(`${name}: label leaves the window`);
      for (const other of solution.placements) {
        if (other !== placement && collides(rect, other.rect)) problems.push(`${name}: label overlaps ${other.id}'s label`);
      }
      for (const anchor of lit) {
        if (collides(rect, anchor)) problems.push(`${name}: label covers a lit control`);
      }
      for (const control of scene.controls) {
        if (collides(rect, control)) problems.push(`${name}: label covers the tour's buttons`);
      }
      for (const region of scene.raised) {
        if (collides(rect, region)) problems.push(`${name}: label sits under something drawn above the tour`);
      }
      if (strict) {
        for (const item of scene.salient) {
          if (item.kind === "control" && collides(rect, item.rect)) problems.push(`${name}: label covers a control`);
        }
      }

      const points = placement.points;
      if (!points || points.length < 2) {
        problems.push(`${name}: no line`);
        continue;
      }
      for (let index = 1; index < points.length; index += 1) {
        const from = points[index - 1];
        const to = points[index];
        const straight = from.x === to.x || from.y === to.y;
        const moves = from.x !== to.x || from.y !== to.y;
        if (!straight) problems.push(`${name}: line segment ${index} is diagonal`);
        if (!moves) problems.push(`${name}: line segment ${index} has no length`);
        if (index >= 2) {
          const before = points[index - 2];
          const turnsBack = (before.x === from.x && from.x === to.x && (from.y - before.y) * (to.y - from.y) < 0)
            || (before.y === from.y && from.y === to.y && (from.x - before.x) * (to.x - from.x) < 0);
          if (turnsBack) problems.push(`${name}: line doubles back at point ${index - 1}`);
        }
      }

      const anchor = step.anchors[placement.anchorIndex];
      const start = points[0];
      if (!sitsJustOff(anchor, start, settings.portGap)) problems.push(`${name}: line doesn't start just off its control`);
      const end = points[points.length - 1];
      if (!liesOnEdge(rect, end)) problems.push(`${name}: line doesn't end on its label`);
      for (const point of points) {
        if (!insideRect(inner, point)) problems.push(`${name}: line leaves the window`);
      }

      const spans = spansOf(points);
      for (const span of spans) {
        for (const anchorRect of lit) {
          if (runsOver(anchorRect, span)) problems.push(`${name}: line runs over a lit control`);
        }
        for (const other of solution.placements) {
          if (runsOver(other.rect, span)) problems.push(`${name}: line runs over ${other.id}'s label`);
        }
        for (const control of scene.controls) {
          if (runsOver(control, span)) problems.push(`${name}: line runs over the tour's buttons`);
        }
        allSpans.push({ owner: name, span });
      }
    }

    for (let first = 0; first < allSpans.length; first += 1) {
      for (let second = first + 1; second < allSpans.length; second += 1) {
        const a = allSpans[first];
        const b = allSpans[second];
        if (a.owner === b.owner) continue;
        if (a.span.horizontal !== b.span.horizontal) continue;
        const shared = Math.min(a.span.high, b.span.high) - Math.max(a.span.low, b.span.low);
        if (shared > 0 && Math.abs(a.span.fixed - b.span.fixed) < 1) problems.push(`${a.owner} and ${b.owner}: lines run on top of each other`);
      }
    }
    return problems;
  }

  function rectSize(rect) {
    return { width: rect.right - rect.left, height: rect.bottom - rect.top };
  }

  function insideRect(rect, point) {
    return point.x >= rect.left && point.x <= rect.right && point.y >= rect.top && point.y <= rect.bottom;
  }

  // Whether a point is on the rect's outline.
  function liesOnEdge(rect, point) {
    const withinX = point.x >= rect.left - 0.01 && point.x <= rect.right + 0.01;
    const withinY = point.y >= rect.top - 0.01 && point.y <= rect.bottom + 0.01;
    const onVertical = Math.abs(point.x - rect.left) < 0.01 || Math.abs(point.x - rect.right) < 0.01;
    const onHorizontal = Math.abs(point.y - rect.top) < 0.01 || Math.abs(point.y - rect.bottom) < 0.01;
    return (onVertical && withinY) || (onHorizontal && withinX);
  }

  // Whether a point is `gap` off one of the rect's edges, level with that edge.
  function sitsJustOff(rect, point, gap) {
    const withinX = point.x >= rect.left - 0.01 && point.x <= rect.right + 0.01;
    const withinY = point.y >= rect.top - 0.01 && point.y <= rect.bottom + 0.01;
    const above = withinX && Math.abs(point.y - (rect.top - gap)) < 0.01;
    const below = withinX && Math.abs(point.y - (rect.bottom + gap)) < 0.01;
    const left = withinY && Math.abs(point.x - (rect.left - gap)) < 0.01;
    const right = withinY && Math.abs(point.x - (rect.right + gap)) < 0.01;
    return above || below || left || right;
  }

  return { DEFAULTS, solve, validate, quality, routeLeader, searchGrid, roundedPath, pathLength, simplifyPoints, makeRect, overlaps, EAST, SOUTH, WEST, NORTH };
})();

if (typeof module !== "undefined") module.exports = TourLayout;
