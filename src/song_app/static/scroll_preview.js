// The scrolling video, played in the page before anything is rendered.
//
// Everything that decides what this looks like — the engraving, the viewport, the
// clock, the scroll curve, which symbol lights up when — is computed by the Python
// renderer and arrives in one payload. This file moves a window over an SVG and
// puts a class on the symbols the payload says are sounding. It works out nothing
// about music, layout or time on its own, so the preview cannot disagree with the
// render it is a preview of.
(function () {
  const svgEl = (tag, props) => {
    const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(props || {})) e.setAttribute(k, v);
    return e;
  };

  const clock = (seconds) => {
    const whole = Math.max(0, Math.floor(seconds));
    return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
  };

  // The last sample at or before t. The curve is dense (one sample per rendered
  // frame), so this is what a frame's position is read off.
  const before = (times, t) => {
    let lo = 0, hi = times.length - 1, best = 0;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (times[mid] <= t) { best = mid; lo = mid + 1; } else { hi = mid - 1; }
    }
    return best;
  };

  // Where the music is on the page at time t. A repeat is a real backward jump in
  // the curve, and `jump` is how big a step the renderer treats as one: across it
  // the position holds until the far side, so the scroll snaps back to the repeated
  // bar instead of sliding through everything in between.
  function positionAt(scroll, t) {
    const { times, xs, jump } = scroll;
    const i = before(times, t);
    if (i >= times.length - 1) return xs[xs.length - 1];
    const step = xs[i + 1] - xs[i];
    if (jump && step < -jump) return xs[i];
    const span = times[i + 1] - times[i];
    return span > 0 ? xs[i] + (step * (t - times[i])) / span : xs[i];
  }

  function makePlayer(data) {
    const view = data.view;
    const windowUnits = view.height * view.aspect;
    const events = data.events;
    const onsets = events.map((e) => e.on);
    let longest = 0;
    for (const e of events) longest = Math.max(longest, e.off - e.on);

    const stage = document.createElement("div");
    stage.className = "pvviewport";
    stage.style.aspectRatio = String(view.aspect);
    stage.innerHTML = data.svg;
    const svg = stage.querySelector("svg");
    svg.style.setProperty("--pv-highlight", data.highlight.colour);

    // The beat marker, drawn over the engraving in the same page units.
    const marker = svgEl("rect", {
      y: view.start, height: view.height, fill: data.highlight.colour,
      "fill-opacity": data.highlight.marker_alpha, "data-preview": "marker",
    });
    svg.appendChild(marker);

    const byId = new Map();
    for (const e of events) {
      if (e.id && !byId.has(e.id)) byId.set(e.id, svg.querySelector(`[id="${e.id}"]`));
    }
    let lit = [];

    const sounding = (t) => {
      const out = [];
      for (let i = before(onsets, t); i >= 0 && onsets[i] >= t - longest; i--) {
        if (onsets[i] <= t && events[i].off > t) out.push(events[i]);
      }
      return out;
    };

    const markerAt = (t) => {
      for (let i = before(onsets, t); i >= 0; i--) {
        if (onsets[i] <= t && events[i].marker) return events[i].marker;
      }
      return null;
    };

    function draw(t) {
      const left = positionAt(data.scroll, t) - data.playhead * windowUnits;
      svg.setAttribute("viewBox",
        `${left} ${view.start} ${windowUnits} ${view.height}`);

      const active = sounding(t);
      for (const e of lit) byId.get(e.id)?.classList.remove("pv-on");
      for (const e of active) byId.get(e.id)?.classList.add("pv-on");
      lit = active;

      const band = markerAt(t);
      if (band) {
        marker.setAttribute("x", band[0]);
        marker.setAttribute("width", band[1] - band[0]);
      } else {
        marker.setAttribute("width", 0);
      }
    }

    return { stage, draw, duration: data.duration };
  }

  // A control strip and a clock over the picture: play/pause, restart, and a
  // scrubber that moves the score the moment it is dragged.
  function mount(host, data) {
    const player = makePlayer(data);
    let time = 0;
    let playing = false;
    let raf = null;
    let last = 0;

    const playBtn = document.createElement("button");
    playBtn.setAttribute("data-preview", "play");
    const restartBtn = document.createElement("button");
    restartBtn.setAttribute("data-preview", "restart");
    restartBtn.textContent = "Restart";
    const seek = document.createElement("input");
    seek.type = "range";
    seek.min = 0;
    seek.max = String(player.duration);
    seek.step = "0.01";
    seek.value = "0";
    seek.className = "pvseek";
    seek.setAttribute("data-preview", "seek");
    const readout = document.createElement("span");
    readout.className = "pvtime";
    readout.setAttribute("data-preview", "time");

    const show = () => {
      seek.value = String(time);
      readout.textContent = `${clock(time)} / ${clock(player.duration)}`;
      playBtn.textContent = playing ? "Pause" : "Play";
      player.draw(time);
    };

    const stop = () => {
      playing = false;
      if (raf) cancelAnimationFrame(raf);
      raf = null;
    };

    const frame = (now) => {
      // The panel is rebuilt from scratch on every refresh, so a player whose
      // picture has left the page must let go rather than animate a stray node.
      if (!player.stage.isConnected) return stop();
      time = Math.min(player.duration, time + (now - last) / 1000);
      last = now;
      if (time >= player.duration) { stop(); show(); return; }
      show();
      raf = requestAnimationFrame(frame);
    };

    const play = () => {
      if (playing) return;
      if (time >= player.duration) time = 0;
      playing = true;
      last = performance.now();
      raf = requestAnimationFrame(frame);
      show();
    };

    playBtn.onclick = () => (playing ? (stop(), show()) : play());
    restartBtn.onclick = () => { time = 0; show(); };
    seek.oninput = () => { time = Number(seek.value); last = performance.now(); show(); };

    const controls = document.createElement("div");
    controls.className = "row pvcontrols";
    controls.append(playBtn, restartBtn, seek, readout);
    host.append(player.stage, controls);
    show();
    return { stop };
  }

  // The Record panel's preview block: a button, a status line, and the player once
  // the payload is ready. Preparing it engraves the score, which takes seconds.
  window.scrollPreviewPanel = function (base, settings) {
    const box = document.createElement("div");
    box.className = "svgpreview";
    const status = document.createElement("div");
    status.className = "pvstatus";
    const holder = document.createElement("div");
    const button = document.createElement("button");
    button.setAttribute("data-preview", "open");
    button.textContent = "Preview scroll";
    let live = null;

    button.onclick = async () => {
      if (live) live.stop();
      live = null;
      holder.textContent = "";
      button.disabled = true;
      status.className = "pvstatus";
      status.textContent = "Preparing the preview (engraving the score)…";
      try {
        const query = new URLSearchParams(settings()).toString();
        const response = await fetch(`${base}/scroll-preview?${query}`);
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.detail || response.statusText);
        status.textContent = "Silent preview — the picture and the timing, not the sound.";
        live = mount(holder, body);
      } catch (err) {
        status.className = "pvstatus err";
        status.textContent = err.message;
      } finally {
        button.disabled = false;
      }
    };

    const row = document.createElement("div");
    row.className = "row";
    row.append(button);
    box.append(row, status, holder);
    return box;
  };
})();
