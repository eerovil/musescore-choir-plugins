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
    const all = [...data.strip.tiles, ...data.lit.tiles, ...data.background.tiles];
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

    function draw(t, focusStaff = null) {
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
        const tiles = focusStaff === null || e.staff === focusStaff
          ? data.lit.tiles : data.background.tiles;
        copy(tiles, e.x0, e.y0, e.x1 - e.x0, e.y1 - e.y0, e.x0 - left);
      }
    }

    return { stage, draw, duration: data.duration, parts: data.parts };
  }

  // Audio is the clock while it exists. The short visual tail after MuseScore's
  // WAV ends uses wall time only because there is no longer an audio time to read.
  function mount(host, data, audioUrl) {
    const player = makePlayer(data);
    let time = 0;
    let playing = false;
    let wantedPlay = false;
    let raf = null;
    let last = 0;
    let audioReady = false;
    let loading = false;
    let preparing = false;
    let audioFailed = false;
    let tail = false;
    let focusStaff = null;
    let request = 0;
    let controller = null;
    let objectUrl = null;

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
    const mix = document.createElement("select");
    mix.setAttribute("data-preview", "mix");
    for (const name of ["ALL", ...player.parts]) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      mix.append(option);
    }
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "auto";
    audio.className = "pvaudio";
    audio.setAttribute("data-preview", "audio");
    const audioStatus = document.createElement("span");
    audioStatus.className = "pvaudio-status";
    audioStatus.setAttribute("data-preview", "audio-status");
    const retryBtn = document.createElement("button");
    retryBtn.textContent = "Retry audio";
    retryBtn.hidden = true;
    retryBtn.setAttribute("data-preview", "retry-audio");

    const show = () => {
      seek.value = String(time);
      readout.textContent = `${clock(time)} / ${clock(player.duration)}`;
      playBtn.textContent = playing || wantedPlay ? "Pause" : "Play";
      playBtn.disabled = !audioReady;
      retryBtn.hidden = !audioFailed;
      player.draw(time, focusStaff);
    };

    const stopFrames = () => {
      playing = false;
      if (raf) cancelAnimationFrame(raf);
      raf = null;
    };

    const startFrames = () => {
      if (raf) return;
      last = performance.now();
      raf = requestAnimationFrame(frame);
    };

    const frame = (now) => {
      // The panel is rebuilt from scratch on every refresh, so a player whose
      // picture has left the page must let go rather than animate a stray node.
      if (!player.stage.isConnected) return destroy();
      if (audioReady && !tail && !audio.paused && !audio.ended) {
        time = Math.min(player.duration, audio.currentTime);
      } else {
        time = Math.min(player.duration, time + (now - last) / 1000);
      }
      last = now;
      if (time >= player.duration) {
        wantedPlay = false;
        loading = true;
        audio.pause();
        loading = false;
        stopFrames();
        show();
        return;
      }
      show();
      raf = requestAnimationFrame(frame);
    };

    const pause = () => {
      wantedPlay = false;
      if (audioReady && !tail) time = audio.currentTime;
      audio.pause();
      stopFrames();
      show();
    };

    const play = async () => {
      if (time >= player.duration) {
        time = 0;
        tail = false;
      }
      wantedPlay = true;
      if (!audioReady) {
        if (!preparing) loadMix(mix.value);
        show();
        return;
      }
      if (time < audio.duration) {
        tail = false;
        audio.currentTime = time;
        try {
          await audio.play();
        } catch (_err) {
          wantedPlay = false;
          audioStatus.textContent = "Audio is ready — press Play again.";
          show();
          return;
        }
      } else {
        tail = true;
      }
      if (!wantedPlay) return;
      playing = true;
      startFrames();
      show();
    };

    const seekTo = (value) => {
      time = Math.max(0, Math.min(player.duration, value));
      if (audioReady) {
        tail = time >= audio.duration;
        audio.currentTime = Math.min(time, audio.duration);
      }
      last = performance.now();
      show();
    };

    const clearAudio = () => {
      loading = true;
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      loading = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = null;
      audioReady = false;
    };

    const attach = (url) => new Promise((resolve, reject) => {
      const ready = () => { cleanup(); resolve(); };
      const failed = () => { cleanup(); reject(new Error("The browser could not play this WAV.")); };
      const cleanup = () => {
        audio.removeEventListener("loadedmetadata", ready);
        audio.removeEventListener("error", failed);
      };
      audio.addEventListener("loadedmetadata", ready);
      audio.addEventListener("error", failed);
      audio.src = url;
      audio.load();
    });

    const loadMix = async (name) => {
      const token = ++request;
      if (controller) controller.abort();
      controller = new AbortController();
      if (audioReady && !tail) time = audio.currentTime;
      const resume = wantedPlay || playing;
      stopFrames();
      preparing = true;
      clearAudio();
      audioFailed = false;
      wantedPlay = resume;
      focusStaff = name === "ALL" || !data.focus_staves
        ? null : player.parts.indexOf(name);
      audioStatus.className = "pvaudio-status";
      audioStatus.textContent = `Preparing ${name} audio…`;
      show();
      try {
        const response = await fetch(audioUrl(name), { signal: controller.signal });
        const blob = await response.blob();
        if (!response.ok) {
          let detail = response.statusText;
          try { detail = JSON.parse(await blob.text()).detail || detail; } catch (_err) {}
          throw new Error(detail);
        }
        const url = URL.createObjectURL(blob);
        if (token !== request || mix.value !== name) {
          URL.revokeObjectURL(url);
          return;
        }
        objectUrl = url;
        await attach(url);
        if (token !== request || mix.value !== name) return;
        audioReady = true;
        preparing = false;
        tail = time >= audio.duration;
        audio.currentTime = Math.min(time, audio.duration);
        audioStatus.textContent = `${name} audio ready`;
        show();
        if (wantedPlay) await play();
      } catch (err) {
        if (err.name === "AbortError" || token !== request) return;
        preparing = false;
        audioFailed = true;
        audioStatus.className = "pvaudio-status err";
        audioStatus.textContent = err.message;
        wantedPlay = false;
        show();
      }
    };

    playBtn.onclick = () => (playing || wantedPlay ? pause() : play());
    restartBtn.onclick = () => seekTo(0);
    seek.oninput = () => seekTo(Number(seek.value));
    mix.onchange = () => loadMix(mix.value);
    retryBtn.onclick = () => loadMix(mix.value);

    audio.addEventListener("play", () => {
      if (loading) return;
      wantedPlay = true;
      playing = true;
      tail = false;
      time = audio.currentTime;
      startFrames();
      show();
    });
    audio.addEventListener("pause", () => {
      if (loading || preparing || audio.ended) return;
      wantedPlay = false;
      time = audio.currentTime;
      stopFrames();
      show();
    });
    audio.addEventListener("seeking", () => {
      if (!tail) {
        time = audio.currentTime;
        show();
      }
    });
    audio.addEventListener("ended", () => {
      if (wantedPlay && time < player.duration) {
        tail = true;
        time = Math.max(time, audio.duration);
        playing = true;
        startFrames();
      } else {
        wantedPlay = false;
        stopFrames();
      }
      show();
    });

    const controls = document.createElement("div");
    controls.className = "row pvcontrols";
    controls.append(playBtn, restartBtn, seek, readout);
    const audioControls = document.createElement("div");
    audioControls.className = "row pvaudio-controls";
    const label = document.createElement("label");
    label.textContent = "Mix ";
    label.append(mix);
    audioControls.append(label, audio, audioStatus, retryBtn);
    host.append(player.stage, controls, audioControls);
    show();
    loadMix("ALL");

    function destroy() {
      ++request;
      if (controller) controller.abort();
      wantedPlay = false;
      stopFrames();
      preparing = true;
      clearAudio();
    }
    return { stop: destroy };
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
    let request = 0;

    box._stopPreview = () => {
      request += 1;
      if (live) live.stop();
      live = null;
      holder.replaceChildren();
      status.textContent = "";
      button.disabled = false;
    };

    button.onclick = async () => {
      box._stopPreview();
      const token = request;
      button.disabled = true;
      status.className = "pvstatus";
      status.textContent = "Preparing the preview (drawing the score)…";
      try {
        const chosen = settings();
        const query = new URLSearchParams(chosen).toString();
        const data = await loadPreview(`${base}/scroll-preview?${query}`,
                                       (name) => `${base}/scroll-preview/${name}`);
        if (token !== request) return;
        status.textContent = "Preview ready — sound is prepared one selected mix at a time.";
        live = mount(holder, data, (mix) => {
          const audioQuery = new URLSearchParams({
            ...chosen, mix, revision: data.revision,
          }).toString();
          return `${base}/scroll-preview-audio?${audioQuery}`;
        });
      } catch (err) {
        if (token !== request) return;
        status.className = "pvstatus err";
        status.textContent = err.message;
      } finally {
        if (token === request) button.disabled = false;
      }
    };

    const row = document.createElement("div");
    row.className = "row";
    row.append(button);
    box.append(row, status, holder);
    return box;
  };
})();
