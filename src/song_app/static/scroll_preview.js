// The scrolling video, played in the page before anything is rendered.
//
// The frames are not drawn here. The server rasterises the score with the same
// call the renderer uses and sends two strips as PNG tiles: the engraving, and the
// same engraving with every playable symbol already repainted blue. A frame is a
// copy of a window out of the first, a translucent band for the beat marker, and a
// copy of each sounding symbol's box out of the second — which is exactly what
// `video.render` writes, so the preview cannot look different from the video.
//
// This file therefore decides nothing: not the layout, not the clock, not which
// symbol lights up, not even what colour it goes.
(function () {
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

  // Where the music is on the strip at time t. A repeat is a real backward jump in
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

  const loadImage = (url) => new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(`Could not load ${url}`));
    image.src = url;
  });

  // Fetch the payload and every tile it names. The tiles are the picture, so there
  // is nothing to show until they are here.
  async function loadPreview(url, tileUrl) {
    const response = await fetch(url);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || response.statusText);
    const all = [...data.strip.tiles, ...data.lit.tiles];
    const images = await Promise.all(all.map((tile) => loadImage(tileUrl(tile.name))));
    all.forEach((tile, index) => { tile.image = images[index]; });
    return data;
  }

  function makePlayer(data) {
    const frame = data.frame;
    const events = data.events;
    const onsets = events.map((e) => e.on);
    let longest = 0;
    for (const e of events) longest = Math.max(longest, e.off - e.on);

    const stage = document.createElement("div");
    stage.className = "pvviewport";
    stage.style.aspectRatio = `${frame.width} / ${frame.height}`;
    const canvas = document.createElement("canvas");
    canvas.width = frame.width;
    canvas.height = frame.height;
    canvas.setAttribute("data-preview", "canvas");
    stage.append(canvas);
    const ctx = canvas.getContext("2d");
    const band = data.highlight.colour;
    const rgb = [1, 3, 5].map((i) => parseInt(band.slice(i, i + 2), 16));
    const marker = `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${data.highlight.marker_alpha})`;

    // Copy a box out of a tiled strip, drawing from as many tiles as it spans.
    // `dx` is where source column `sx` lands on the canvas; rows do not move, so a
    // box keeps the vertical position the renderer gave it.
    const copy = (tiles, sx, sy, w, h, dx) => {
      for (const tile of tiles) {
        const x0 = Math.max(sx, tile.x);
        const x1 = Math.min(sx + w, tile.x + tile.width);
        if (x1 <= x0) continue;
        ctx.drawImage(tile.image, x0 - tile.x, sy, x1 - x0, h,
                      dx + (x0 - sx), sy, x1 - x0, h);
      }
    };

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
      // The same three steps, in the same order, as one turn of `video.render`'s
      // loop: the window, the beat marker over it, then the lit symbols on top.
      const left = Math.trunc(positionAt(data.scroll, t) - data.playhead * frame.width);
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, frame.width, frame.height);
      copy(data.strip.tiles, left, 0, frame.width, frame.height, 0);

      const beat = markerAt(t);
      if (beat) {
        ctx.fillStyle = marker;
        ctx.fillRect(beat[0] - left, 0, beat[1] - beat[0], frame.height);
      }

      for (const e of sounding(t)) {
        copy(data.lit.tiles, e.x0, e.y0, e.x1 - e.x0, e.y1 - e.y0, e.x0 - left);
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
  // the picture is ready. Preparing it engraves and rasterises the score, which
  // takes seconds — against the minutes the render it stands in for costs.
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
      status.textContent = "Preparing the preview (drawing the score)…";
      try {
        const query = new URLSearchParams(settings()).toString();
        const data = await loadPreview(`${base}/scroll-preview?${query}`,
                                       (name) => `${base}/scroll-preview/${name}`);
        status.textContent = "Silent preview — the render's own picture, without the sound.";
        live = mount(holder, data);
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
