(function () {
  var DATA = window.__UNITS__.units;
  var THREE = window.THREE;
  var dark = function () {
    var t = document.documentElement.getAttribute('data-theme');
    if (t) return t === 'dark';
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  };

  // ---- scene ------------------------------------------------------------
  var canvas = document.getElementById('stage');
  var renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  if ('outputColorSpace' in renderer && 'SRGBColorSpace' in THREE) {
    renderer.outputColorSpace = THREE.SRGBColorSpace;
  } else if ('outputEncoding' in renderer && 'sRGBEncoding' in THREE) {
    renderer.outputEncoding = THREE.sRGBEncoding;
  }
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.06;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  var scene = new THREE.Scene();
  var camera = new THREE.PerspectiveCamera(42, 1, 0.05, 400);
  var root = new THREE.Group();
  scene.add(root);

  var hemi = new THREE.HemisphereLight(0xffffff, 0x6d7681, 1.05);
  scene.add(hemi);
  var sun = new THREE.DirectionalLight(0xfff3e2, 1.55);
  sun.position.set(-9, 16, 7);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.camera.left = sun.shadow.camera.bottom = -16;
  sun.shadow.camera.right = sun.shadow.camera.top = 16;
  sun.shadow.camera.far = 60;
  sun.shadow.bias = -0.0008;
  scene.add(sun);
  var fill = new THREE.DirectionalLight(0xcfe2ea, 0.28);
  fill.position.set(8, 7, -9);
  scene.add(fill);

  function paint() {
    var d = dark();
    scene.background = new THREE.Color(d ? 0x0f1318 : 0xc8d0d3);
    hemi.intensity = d ? 0.55 : 0.72;
    hemi.groundColor.setHex(d ? 0x232a31 : 0x7e878e);
    sun.intensity = d ? 1.5 : 2.1;
    ground.material.color.setHex(d ? 0x0b0e12 : 0xbcc4c7);
    MAT.wall.color.setHex(d ? 0xcfcabf : 0xeae6dd);
    MAT.wallTop.color.setHex(d ? 0x6f6a61 : 0x9b968c);
  }

  var MAT = {
    wall: new THREE.MeshStandardMaterial({ color: 0xe9e6df, roughness: 0.92, metalness: 0 }),
    wallTop: new THREE.MeshStandardMaterial({ color: 0xb9b5ab, roughness: 0.95 }),
    slab: new THREE.MeshStandardMaterial({ color: 0xdedad2, roughness: 0.9 }),
    deck: new THREE.MeshStandardMaterial({ color: 0xb0714b, roughness: 0.82 }),
    parapet: new THREE.MeshStandardMaterial({ color: 0xd9d5cc, roughness: 0.9 }),
    joinery: new THREE.MeshStandardMaterial({ color: 0xc9c4b8, roughness: 0.8 }),
    frame: new THREE.MeshStandardMaterial({ color: 0x8b9298, roughness: 0.6, metalness: 0.25 }),
    glass: new THREE.MeshStandardMaterial({
      color: 0x9fc3d4, roughness: 0.08, metalness: 0.1,
      transparent: true, opacity: 0.3, side: THREE.DoubleSide
    }),
    voidm: new THREE.MeshStandardMaterial({ color: 0x9c9c9c, roughness: 1 })
  };

  var ground = new THREE.Mesh(
    new THREE.PlaneGeometry(300, 300),
    new THREE.MeshStandardMaterial({ color: 0xd3d8d8, roughness: 1 })
  );
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.305;
  ground.receiveShadow = true;
  scene.add(ground);

  // ---- geometry helpers -------------------------------------------------
  // Plan coordinates are (x right, y down). A shape built as (x, -y) and
  // rotated -90 deg about X lands at world (x, height, y), so the model keeps
  // the drawing's orientation and extrudes upward.
  function shapeOf(ring, holes) {
    var s = new THREE.Shape();
    ring.forEach(function (p, i) {
      if (i === 0) s.moveTo(p[0], -p[1]); else s.lineTo(p[0], -p[1]);
    });
    (holes || []).forEach(function (h) {
      var path = new THREE.Path();
      h.forEach(function (p, i) {
        if (i === 0) path.moveTo(p[0], -p[1]); else path.lineTo(p[0], -p[1]);
      });
      s.holes.push(path);
    });
    return s;
  }

  function extrude(polys, height, mat, capMat, base) {
    var shapes = polys.map(function (p) { return shapeOf(p.outer, p.holes); });
    if (!shapes.length) return null;
    var geo = new THREE.ExtrudeGeometry(shapes, {
      depth: height, bevelEnabled: false, curveSegments: 1
    });
    geo.rotateX(-Math.PI / 2);
    var mesh = new THREE.Mesh(geo, capMat ? [mat, capMat] : mat);
    mesh.position.y = base || 0;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return mesh;
  }

  // ---- build one unit --------------------------------------------------
  var loader = new THREE.TextureLoader();
  var built = {};

  function build(unit) {
    if (built[unit.key]) return built[unit.key];
    var g = new THREE.Group();
    var H = unit.heights;

    var slab = extrude(unit.slab, 0.3, MAT.slab, MAT.slab, -0.3);
    if (slab) g.add(slab);
    var voids = extrude(unit.voids, 0.14, MAT.voidm, MAT.voidm, -0.14);
    if (voids) g.add(voids);

    var deck = extrude(unit.deck, 0.03, MAT.deck, MAT.deck, 0.001);
    if (deck) g.add(deck);

    var walls = extrude(unit.walls, H.wall, MAT.wall, MAT.wallTop);
    if (walls) { g.add(walls); g.userData.walls = walls; }
    var para = extrude(unit.parapets, H.parapet, MAT.parapet, MAT.parapet);
    if (para) g.add(para);
    var join = extrude(unit.joinery, H.joinery, MAT.joinery, MAT.joinery);
    if (join) g.add(join);

    // glazing: sill and head as solid, glass between
    unit.glazing.forEach(function (o) {
      var t = Math.max(o.width, 0.14);
      var top = o.kind === 'door' ? H.door : H.header;
      var bot = o.kind === 'door' ? 0.04 : H.sill;
      var parts = [
        [0, bot, MAT.frame],
        [bot, top, MAT.glass],
        [top, H.wall, MAT.frame]
      ];
      parts.forEach(function (p) {
        var h = p[1] - p[0];
        if (h <= 0.01) return;
        var m = new THREE.Mesh(new THREE.BoxGeometry(o.length, h, t), p[2]);
        m.position.set(o.centre[0], p[0] + h / 2, o.centre[1]);
        m.rotation.y = -o.angle;
        if (p[2] !== MAT.glass) { m.castShadow = true; m.receiveShadow = true; }
        g.add(m);
      });
    });

    // the plan sheet itself, laid on the floor
    var tex = loader.load(unit.texture.data);
    if ('SRGBColorSpace' in THREE) tex.colorSpace = THREE.SRGBColorSpace;
    else if ('sRGBEncoding' in THREE) tex.encoding = THREE.sRGBEncoding;
    tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
    var tw = unit.texture.size_m[0], th = unit.texture.size_m[1];
    var ox = unit.texture.origin_m[0], oy = unit.texture.origin_m[1];
    var overlay = new THREE.Mesh(
      new THREE.PlaneGeometry(tw, th),
      new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.97 })
    );
    overlay.rotation.x = -Math.PI / 2;
    overlay.position.set(ox + tw / 2, 0.035, oy + th / 2);
    g.add(overlay);
    g.userData.overlay = overlay;

    var w = unit.size_m[0], d = unit.size_m[1];
    g.position.set(-w / 2, 0, -d / 2);
    var wrap = new THREE.Group();
    wrap.add(g);
    wrap.userData = { inner: g, span: Math.max(w, d), w: w, d: d };
    built[unit.key] = wrap;
    return wrap;
  }

  // ---- camera control --------------------------------------------------
  var orbit = { az: -0.72, el: 0.86, dist: 22, target: new THREE.Vector3(0, 0.9, 0) };
  var walk = { pos: new THREE.Vector3(0, 1.62, 0), az: 0, el: -0.05 };
  var mode = 'doll';
  var keys = {};

  function applyCamera() {
    if (mode === 'walk') {
      camera.fov = 68;
      camera.position.copy(walk.pos);
      camera.lookAt(
        walk.pos.x + Math.cos(walk.el) * Math.sin(walk.az),
        walk.pos.y + Math.sin(walk.el),
        walk.pos.z + Math.cos(walk.el) * Math.cos(walk.az)
      );
    } else {
      camera.fov = mode === 'plan' ? 26 : 42;
      var r = Math.cos(orbit.el) * orbit.dist;
      camera.position.set(
        orbit.target.x + r * Math.sin(orbit.az),
        orbit.target.y + Math.sin(orbit.el) * orbit.dist,
        orbit.target.z + r * Math.cos(orbit.az)
      );
      camera.lookAt(orbit.target);
    }
    camera.updateProjectionMatrix();
  }

  var drag = null;
  canvas.addEventListener('pointerdown', function (e) {
    canvas.setPointerCapture(e.pointerId);
    drag = { x: e.clientX, y: e.clientY, pan: e.button === 2 || e.shiftKey };
  });
  canvas.addEventListener('contextmenu', function (e) { e.preventDefault(); });
  canvas.addEventListener('pointermove', function (e) {
    if (!drag) return;
    var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    drag.x = e.clientX; drag.y = e.clientY;
    if (mode === 'walk') {
      walk.az -= dx * 0.005;
      walk.el = Math.max(-1.2, Math.min(1.2, walk.el - dy * 0.005));
    } else if (drag.pan) {
      var s = orbit.dist * 0.0013;
      orbit.target.x -= (dx * Math.cos(orbit.az) - dy * Math.sin(orbit.az) * 0.5) * s;
      orbit.target.z += (dx * Math.sin(orbit.az) + dy * Math.cos(orbit.az) * 0.5) * s;
    } else {
      orbit.az -= dx * 0.006;
      orbit.el = Math.max(0.12, Math.min(1.5, orbit.el + dy * 0.005));
    }
  });
  addEventListener('pointerup', function () { drag = null; });
  canvas.addEventListener('wheel', function (e) {
    e.preventDefault();
    if (mode === 'walk') return;
    orbit.dist = Math.max(3.5, Math.min(70, orbit.dist * (1 + Math.sign(e.deltaY) * 0.1)));
  }, { passive: false });
  addEventListener('keydown', function (e) {
    keys[e.key.toLowerCase()] = true;
    if (mode === 'walk' && ['w', 'a', 's', 'd', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright'].indexOf(e.key.toLowerCase()) >= 0) e.preventDefault();
  });
  addEventListener('keyup', function (e) { keys[e.key.toLowerCase()] = false; });

  // ---- state -----------------------------------------------------------
  var current = null, cut = false, furn = true;

  function frameUnit(wrap) {
    var span = wrap.userData.span;
    orbit.dist = span * 1.5;
    orbit.target.set(0, 0.9, 0);
    walk.pos.set(0, 1.62, wrap.userData.d * 0.32);
    walk.az = Math.PI;
    var s = Math.max(wrap.userData.w, wrap.userData.d) * 0.75;
    sun.shadow.camera.left = sun.shadow.camera.bottom = -s;
    sun.shadow.camera.right = sun.shadow.camera.top = s;
    sun.shadow.camera.updateProjectionMatrix();
    sun.position.set(-span * 0.6, span * 1.05, span * 0.45);
  }

  function applyFlags() {
    if (!current) return;
    var w = current.userData.inner.userData.walls;
    if (w) w.scale.y = cut ? 0.42 : 1;
    var o = current.userData.inner.userData.overlay;
    if (o) o.visible = furn;
  }

  function select(key) {
    var unit = DATA.filter(function (u) { return u.key === key; })[0];
    if (current) root.remove(current);
    current = build(unit);
    root.add(current);
    frameUnit(current);
    applyFlags();

    document.querySelectorAll('.tab').forEach(function (b) {
      b.setAttribute('aria-selected', String(b.dataset.key === key));
    });
    document.getElementById('uname').textContent = unit.name;
    var a = unit.areas_m2;
    document.getElementById('areas').innerHTML =
      row('Internal, net', a.internal_net) +
      row('Internal, gross', a.internal_gross) +
      row('Balcony', a.balcony) +
      row('Total, gross', a.total_gross, true) +
      '<dt>Envelope</dt><dd>' + unit.size_m[0].toFixed(1) + ' × ' + unit.size_m[1].toFixed(1) + ' m</dd>';
    document.getElementById('rooms').innerHTML = unit.rooms.map(function (r) {
      return '<tr><td>' + r.name + '</td><td>' + r.dims.replace(/ x /g, ' × ') + '</td></tr>';
    }).join('');
  }
  function row(label, v, big) {
    var c = big ? ' class="big"' : '';
    return '<dt' + c + '>' + label + '</dt><dd' + c + '>' + v.toFixed(1) + ' m²</dd>';
  }

  var tabs = document.querySelector('.tabs');
  DATA.forEach(function (u) {
    var b = document.createElement('button');
    b.className = 'tab';
    b.type = 'button';
    b.role = 'tab';
    b.dataset.key = u.key;
    b.textContent = u.key;
    b.setAttribute('aria-selected', 'false');
    b.setAttribute('aria-label', u.name);
    b.addEventListener('click', function () { select(u.key); });
    tabs.appendChild(b);
  });

  var TIPS = {
    doll: 'Drag to orbit · scroll to zoom · shift-drag to pan',
    plan: 'Scroll to zoom · shift-drag to pan',
    walk: 'W A S D to move · drag to look'
  };
  document.querySelectorAll('.hud [data-view]').forEach(function (b) {
    b.addEventListener('click', function () {
      mode = b.dataset.view;
      document.querySelectorAll('.hud [data-view]').forEach(function (o) {
        o.setAttribute('aria-pressed', String(o === b));
      });
      if (mode === 'plan') { orbit.el = 1.48; orbit.az = 0; }
      if (mode === 'doll') { orbit.el = 0.86; orbit.az = -0.72; }
      if (mode === 'walk' && current) {
        walk.pos.set(0, 1.62, current.userData.d * 0.32);
        walk.az = Math.PI;
      }
      document.getElementById('tip').textContent = TIPS[mode];
    });
  });
  document.querySelectorAll('.hud [data-toggle]').forEach(function (b) {
    b.addEventListener('click', function () {
      var on = b.getAttribute('aria-pressed') !== 'true';
      b.setAttribute('aria-pressed', String(on));
      if (b.dataset.toggle === 'cut') cut = on; else furn = on;
      applyFlags();
    });
  });

  // ---- loop ------------------------------------------------------------
  function resize() {
    var r = canvas.getBoundingClientRect();
    renderer.setSize(r.width, r.height, false);
    camera.aspect = r.width / Math.max(1, r.height);
    camera.updateProjectionMatrix();
  }
  addEventListener('resize', resize);

  var last = performance.now();
  function tick(now) {
    var dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    if (mode === 'walk') {
      var sp = (keys.shift ? 3.4 : 1.7) * dt;
      var f = (keys.w || keys.arrowup ? 1 : 0) - (keys.s || keys.arrowdown ? 1 : 0);
      var s = (keys.d || keys.arrowright ? 1 : 0) - (keys.a || keys.arrowleft ? 1 : 0);
      if (f || s) {
        walk.pos.x += (Math.sin(walk.az) * f + Math.cos(walk.az) * s) * sp;
        walk.pos.z += (Math.cos(walk.az) * f - Math.sin(walk.az) * s) * sp;
      }
    }
    applyCamera();
    // scale bar: 5 m at the target's depth
    var px = 5 / (2 * Math.tan((camera.fov * Math.PI / 180) / 2) *
      camera.position.distanceTo(mode === 'walk' ? camera.position.clone().add(new THREE.Vector3(0, 0, 5)) : orbit.target)) *
      canvas.clientHeight;
    var bar = document.getElementById('sbar');
    if (isFinite(px) && px > 8) { bar.style.width = Math.min(220, px).toFixed(0) + 'px'; }
    renderer.render(scene, camera);
    requestAnimationFrame(tick);
  }

  paint();
  var mq = window.matchMedia('(prefers-color-scheme: dark)');
  (mq.addEventListener ? mq.addEventListener.bind(mq, 'change') : mq.addListener.bind(mq))(paint);
  resize();
  select(DATA[0].key);
  requestAnimationFrame(tick);
})();
