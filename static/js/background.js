/**
 * AMIKO - Per-Theme Interactive Desktop Background Engine
 *
 * Renders a hardware-accelerated HTML5 <canvas> inside #amikoDesktop with a
 * unique, signature visual effect tailored to each AMIKO theme:
 *
 *  - amiko-theme     (AMIKO Base):  Teal/Red Interactive Neural Constellation & Data Nodes
 *  - amiko-matrix    (Matrix):      Digital Katakana/Hex Rain with glowing cursor wake
 *  - amiko-cyberpunk (Cyberpunk):   Night City Skyline — Megatowers, Neon Beams & Flying AVs
 *  - amiko-synthwave (Synthwave):   80s Outrun 3D Perspective Grid & Retro Sun Horizon
 *  - amiko-batman    (Batman):      Stealth Cowl in the Shadows with Piercing Glowing White Eyes
 *  - amiko-barbie    (Barbie):      Dreamhouse Sparkle Bokeh, Floating Hearts & Diamond Stars
 *  - amiko-classic   (Classic):     Sober Matrix Grid — Warm Orange Tiles & Rare Ottanio Accents
 *  - amiko-pacman    (Pac-Man):     Classic 1980 Arcade Maze, Power Pellets & Chasing Ghosts
 *
 * Fully toggleable by the user via Settings → Themes ("🌌 LIVE BACKGROUND EFFECT")
 * or Settings → Interface ("Live Desktop Background").
 */

(function () {
    'use strict';

    class AmikoBackgroundEngine {
        constructor() {
            this.desktop = document.getElementById('amikoDesktop');
            if (!this.desktop) return;

            this.canvas = document.createElement('canvas');
            this.canvas.id = 'amikoDesktopCanvas';
            this.canvas.setAttribute('aria-hidden', 'true');
            Object.assign(this.canvas.style, {
                position: 'absolute',
                top: '0',
                left: '0',
                width: '100%',
                height: '100%',
                pointerEvents: 'none',
                zIndex: '0',
                opacity: '1',
                transition: 'opacity 0.35s ease'
            });

            // Insert as first child of #amikoDesktop (behind #emptyDesktopMessage and WinBox windows)
            this.desktop.insertBefore(this.canvas, this.desktop.firstChild);
            this.ctx = this.canvas.getContext('2d');

            this.width = 0;
            this.height = 0;
            this.dpr = Math.min(window.devicePixelRatio || 1, 2);

            this.enabled = true;
            this.animationsEnabled = true;
            this.themeId = window.AMIKO_ACTIVE_THEME_ID || 'amiko-theme';
            this.rafId = null;
            this.lastTime = 0;
            this.time = 0;

            // Mouse & ripple interaction state
            this.mouse = { x: -9999, y: -9999, active: false, vx: 0, vy: 0 };
            this.ripples = [];

            // Effect-specific state container
            this.state = {};

            this._bindEvents();
            this.resize();
            this._initThemeState(this.themeId);
            this.start();
        }

        _bindEvents() {
            window.addEventListener('resize', () => this.resize());

            if (typeof ResizeObserver !== 'undefined') {
                this.resizeObserver = new ResizeObserver(() => this.resize());
                this.resizeObserver.observe(this.desktop);
            }

            // Track mouse over the desktop area
            this.desktop.addEventListener('mousemove', (e) => {
                const rect = this.desktop.getBoundingClientRect();
                const nx = e.clientX - rect.left;
                const ny = e.clientY - rect.top;
                this.mouse.vx = nx - this.mouse.x;
                this.mouse.vy = ny - this.mouse.y;
                this.mouse.x = nx;
                this.mouse.y = ny;
                this.mouse.active = true;
            });

            this.desktop.addEventListener('mouseleave', () => {
                this.mouse.active = false;
                this.mouse.x = -9999;
                this.mouse.y = -9999;
            });

            this.desktop.addEventListener('mousedown', (e) => {
                if (!this.isRunningAllowed()) return;
                const rect = this.desktop.getBoundingClientRect();
                this.ripples.push({
                    x: e.clientX - rect.left,
                    y: e.clientY - rect.top,
                    r: 4,
                    maxR: Math.max(this.width, this.height) * 0.22,
                    alpha: 0.75
                });
                if (this.themeId === 'amiko-batman' && this.state) {
                    this.state.blinkProgress = Math.max(this.state.blinkProgress, 0.01);
                    this.state.blinkTimer = 0;
                }
            });

            // React to theme changes
            window.addEventListener('amikoThemeChanged', (e) => {
                const newId = (e.detail && e.detail.id) || window.AMIKO_ACTIVE_THEME_ID || this._detectThemeFromDom();
                this.setTheme(newId);
            });

            // Pause when browser tab is hidden to save CPU/GPU
            document.addEventListener('visibilitychange', () => {
                if (document.hidden) {
                    this._stopLoop();
                } else if (this.isRunningAllowed()) {
                    this.start();
                }
            });
        }

        _detectThemeFromDom() {
            const link = document.getElementById('theme-stylesheet');
            if (link && link.getAttribute('href')) {
                const m = link.getAttribute('href').match(/themes\/([a-z0-9_-]+)\.css/i);
                if (m && m[1]) return m[1];
            }
            return 'amiko-theme';
        }

        isRunningAllowed() {
            const noAnimClass = document.documentElement.classList.contains('no-animations');
            return this.enabled && this.animationsEnabled && !noAnimClass && !document.hidden;
        }

        _syncDesktopEmptyState() {
            if (!this.desktop) return;
            const noAnimClass = document.documentElement.classList.contains('no-animations');
            const active = this.enabled && this.animationsEnabled && !noAnimClass;
            this.desktop.classList.toggle('has-bg-effect', active);
        }

        setEnabled(enabled) {
            this.enabled = Boolean(enabled);
            this._syncDesktopEmptyState();
            if (this.isRunningAllowed()) {
                this.canvas.style.display = 'block';
                requestAnimationFrame(() => {
                    this.canvas.style.opacity = '1';
                });
                this.start();
            } else {
                this.canvas.style.opacity = '0';
                this._stopLoop();
                if (this.ctx) {
                    this.ctx.clearRect(0, 0, this.width, this.height);
                }
                this.canvas.style.display = 'none';
            }
        }

        setAnimationsEnabled(enabled) {
            this.animationsEnabled = Boolean(enabled);
            this.setEnabled(this.enabled);
        }

        setTheme(themeId) {
            const nextTheme = themeId || this._detectThemeFromDom();
            this.themeId = nextTheme;
            this._initThemeState(this.themeId);
            if (this.isRunningAllowed() && !this.rafId) {
                this.start();
            }
        }

        resize() {
            if (!this.desktop) return;
            const rect = this.desktop.getBoundingClientRect();
            const w = Math.max(1, Math.floor(rect.width));
            const h = Math.max(1, Math.floor(rect.height));
            if (w === this.width && h === this.height) return;

            this.width = w;
            this.height = h;
            this.canvas.width = w * this.dpr;
            this.canvas.height = h * this.dpr;
            this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

            this._initThemeState(this.themeId);
        }

        start() {
            this._syncDesktopEmptyState();
            if (this.rafId || !this.isRunningAllowed()) return;
            this.canvas.style.display = 'block';
            this.canvas.style.opacity = '1';
            this.lastTime = performance.now();
            const loop = (now) => {
                if (!this.isRunningAllowed()) {
                    this.rafId = null;
                    return;
                }
                const dt = Math.min((now - this.lastTime) / 1000, 0.1);
                this.lastTime = now;
                this.time += dt;
                this.render(dt);
                this.rafId = requestAnimationFrame(loop);
            };
            this.rafId = requestAnimationFrame(loop);
        }

        _stopLoop() {
            if (this.rafId) {
                cancelAnimationFrame(this.rafId);
                this.rafId = null;
            }
        }

        // Read live server CPU percentage (0..100) from topbar to modulate speed/energy
        _getCpuFactor() {
            const el = document.getElementById('cpuValueHeader') || document.getElementById('cpuValueText');
            if (!el) return 1;
            const val = parseFloat(el.textContent);
            if (isNaN(val)) return 1;
            return 1 + (Math.min(100, Math.max(0, val)) / 100) * 0.85;
        }

        // =====================================================================
        // INITIALIZATION PER THEME
        // =====================================================================
        _initThemeState(themeId) {
            const w = this.width || 1280;
            const h = this.height || 720;
            this.ripples = [];

            switch (themeId) {
                case 'amiko-matrix': {
                    const fontSize = 18;
                    const cols = Math.ceil(w / fontSize);
                    const rows = Math.ceil(h / fontSize) + 2;
                    // Authentic full-width Katakana + reversed digits/symbols from the film's digital rain
                    const chars = 'アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン012345789Z:・."=*+-<>¦｜';
                    const streams = [];
                    for (let i = 0; i < cols; i++) {
                        const trailLen = 10 + Math.floor(Math.random() * 18);
                        const glyphs = [];
                        for (let r = 0; r < rows; r++) {
                            glyphs.push({
                                ch: chars.charAt(Math.floor(Math.random() * chars.length)),
                                mirror: Math.random() > 0.45
                            });
                        }
                        streams.push({
                            head: Math.random() * -rows,
                            speed: 5.5 + Math.random() * 9.5,
                            trailLen,
                            glyphs
                        });
                    }
                    this.state = { fontSize, cols, rows, chars, streams };
                    this.ctx.clearRect(0, 0, w, h);
                    break;
                }

                case 'amiko-cyberpunk': {
                    // Night City skyline: layered, pre-rendered offscreen canvases (depth + 60fps)
                    // Rigorously text-free: only architecture, light, haze and motion
                    this.state = this._buildCyberpunkCity(w, h);
                    break;
                }

                case 'amiko-synthwave': {
                    const stars = [];
                    for (let i = 0; i < 90; i++) {
                        stars.push({
                            x: Math.random() * w,
                            y: Math.random() * (h * 0.58),
                            r: 0.6 + Math.random() * 1.8,
                            phase: Math.random() * Math.PI * 2,
                            color: Math.random() > 0.3 ? '#ffffff' : (Math.random() > 0.5 ? '#00d4ff' : '#ff2975')
                        });
                    }
                    // Mountain silhouette points
                    const peaks = [];
                    const segs = 28;
                    for (let i = 0; i <= segs; i++) {
                        const norm = Math.abs((i / segs) - 0.5) * 2; // dips in center for the sun
                        const dip = Math.max(0.15, Math.min(1, norm * 1.6));
                        peaks.push((18 + Math.sin(i * 1.7) * 22 + Math.cos(i * 3.1) * 14) * dip);
                    }
                    this.state = { stars, peaks, gridOffset: 0 };
                    break;
                }

                case 'amiko-batman': {
                    // Minimalist Stealth Batman Cowl emerging from the Shadows with Piercing White Eyes
                    this.state = {
                        tiltX: 0,
                        tiltY: 0,
                        gazeX: 0,
                        gazeY: 0,
                        blinkTimer: 6 + Math.random() * 4,
                        blinkProgress: 0,
                        lightningTimer: 22 + Math.random() * 15,
                        lightningAlpha: 0
                    };
                    break;
                }

                case 'amiko-barbie': {
                    const items = [];
                    const count = Math.min(65, Math.floor((w * h) / 16000));
                    const colors = ['#ff3399', '#cc0066', '#ff80bf', '#00ccff', '#ffb3d9', '#ffffff'];
                    const types = ['sparkle', 'heart', 'bokeh', 'sparkle'];
                    for (let i = 0; i < count; i++) {
                        items.push({
                            x: Math.random() * w,
                            y: Math.random() * h,
                            vx: (Math.random() - 0.5) * 18,
                            vy: -12 - Math.random() * 24,
                            size: 6 + Math.random() * 14,
                            rot: Math.random() * Math.PI * 2,
                            vRot: (Math.random() - 0.5) * 1.2,
                            phase: Math.random() * Math.PI * 2,
                            color: colors[i % colors.length],
                            type: types[i % types.length]
                        });
                    }
                    this.state = { items, trail: [] };
                    break;
                }

                case 'amiko-classic': {
                    // Sober Digital Matrix: Dark tile grid with slow, sporadic warm orange tiles & rare ottanio accents
                    const activeCells = new Map();
                    const tileSize = 24;
                    const gap = 4;
                    const step = tileSize + gap;
                    const cols = Math.floor(w / step) || 40;
                    const rows = Math.floor(h / step) || 25;

                    const orangeShades = [
                        { hex: '#ff9500', glow: 'rgba(255, 149, 0, 0.45)' },
                        { hex: '#ffaa00', glow: 'rgba(255, 170, 0, 0.45)' },
                        { hex: '#ff8800', glow: 'rgba(255, 136, 0, 0.45)' },
                        { hex: '#ffb300', glow: 'rgba(255, 179, 0, 0.45)' },
                        { hex: '#f39c12', glow: 'rgba(243, 156, 18, 0.45)' }
                    ];
                    const ottanioShades = [
                        { hex: '#14b8a6', glow: 'rgba(20, 184, 166, 0.5)' },
                        { hex: '#0d7377', glow: 'rgba(13, 115, 119, 0.5)' }
                    ];

                    // Pre-populate with just 6-8 quiet ambient glowing tiles
                    const initialCount = 7;
                    for (let i = 0; i < initialCount; i++) {
                        const sc = Math.floor(Math.random() * cols);
                        const sr = Math.floor(Math.random() * rows);
                        const isOttanio = i === 0; // exactly 1 rare ottanio tile
                        const col = isOttanio
                            ? ottanioShades[Math.floor(Math.random() * ottanioShades.length)]
                            : orangeShades[Math.floor(Math.random() * orangeShades.length)];
                        activeCells.set(`${sc},${sr}`, {
                            c: sc, r: sr,
                            color: col.hex,
                            glow: col.glow,
                            alpha: 0.2 + Math.random() * 0.5,
                            targetAlpha: 0.65 + Math.random() * 0.25,
                            state: 'out',
                            decay: 0.18 + Math.random() * 0.12
                        });
                    }

                    this.state = {
                        activeCells,
                        spawnTimer: 1.5,
                        lastMouseCol: -1,
                        lastMouseRow: -1
                    };
                    break;
                }

                case 'amiko-pacman': {
                    const cell = 44;
                    const cols = Math.ceil(w / cell);
                    const rows = Math.ceil(h / cell);
                    const pellets = [];
                    for (let r = 1; r < rows; r += 2) {
                        for (let c = 1; c < cols; c += 2) {
                            const isPower = (r === 1 && c === 1) || (r === 1 && c >= cols - 2) ||
                                            (r >= rows - 2 && c === 1) || (r >= rows - 2 && c >= cols - 2);
                            pellets.push({
                                x: c * cell,
                                y: r * cell,
                                power: isPower,
                                eatenTimer: 0
                            });
                        }
                    }
                    const actors = [
                        { type: 'pacman', x: cell, y: Math.floor(rows / 2) * cell, vx: 110, vy: 0, color: '#ffff00', mouth: 0 },
                        { type: 'ghost', name: 'Blinky', x: cell * 5, y: cell * 3, vx: 95, vy: 0, color: '#ff0000' },
                        { type: 'ghost', name: 'Pinky',  x: cell * 9, y: cell * 5, vx: 0, vy: 95, color: '#ffb8ff' },
                        { type: 'ghost', name: 'Inky',   x: cell * 3, y: cell * 7, vx: 95, vy: 0, color: '#00ffff' },
                        { type: 'ghost', name: 'Clyde',  x: cell * 11, y: cell * 9, vx: 0, vy: -95, color: '#ffb852' }
                    ];
                    this.state = { cell, cols, rows, pellets, actors };
                    break;
                }

                case 'amiko-pipboy': {
                    // Pip-Boy 3000 / Vault-Tec: V.A.T.S. targeting brackets, Geiger counter telemetry & CRT phosphor grid
                    this.state = { rads: 0.04, pulsePhase: 0 };
                    break;
                }

                case 'amiko-spaceinvaders': {
                    // Space Invaders 1978: Marching 8-bit alien fleet, Mystery Saucer UFO & player cannon
                    const invaders = [];
                    const rows = 3;
                    const cols = Math.min(9, Math.max(5, Math.floor(w / 80)));
                    const invW = 28;
                    const invH = 20;
                    const gapX = 26;
                    const gapY = 24;
                    const totalFleetW = cols * invW + (cols - 1) * gapX;
                    const startX = Math.max(30, (w - totalFleetW) * 0.5);
                    const startY = 70;

                    for (let r = 0; r < rows; r++) {
                        for (let c = 0; c < cols; c++) {
                            invaders.push({
                                col: c,
                                row: r,
                                type: r === 0 ? 'squid' : (r === 1 ? 'crab' : 'octopus'),
                                relX: c * (invW + gapX),
                                relY: r * (invH + gapY)
                            });
                        }
                    }

                    this.state = {
                        invW, invH,
                        totalFleetW,
                        fleetX: startX,
                        fleetY: startY,
                        fleetDir: 1,
                        fleetSpeed: 42,
                        frame: 0,
                        frameTimer: 0,
                        invaders,
                        ufo: { x: -80, active: false, timer: 3 },
                        cannonX: w * 0.5
                    };
                    break;
                }

                case 'amiko-highcontrast': {
                    // High Contrast: Precision drafting coordinate grid & hairline crosshairs
                    this.state = {};
                    break;
                }

                case 'amiko-nordic': {
                    // Nordic Slate: Clean, sober harmonic sinusoidal topographic blueprint waves (distraction-free for office)
                    this.state = { offset: 0 };
                    break;
                }

                case 'amiko-theme':
                default: {
                    const count = Math.min(75, Math.max(32, Math.floor((w * h) / 18000)));
                    const nodes = [];
                    for (let i = 0; i < count; i++) {
                        const isAccent = i % 7 === 0;
                        nodes.push({
                            x: Math.random() * w,
                            y: Math.random() * h,
                            vx: (Math.random() - 0.5) * 28,
                            vy: (Math.random() - 0.5) * 28,
                            r: isAccent ? 3.2 : (1.6 + Math.random() * 1.6),
                            accent: isAccent,
                            pulse: Math.random() * Math.PI * 2
                        });
                    }
                    this.state = { nodes };
                    break;
                }
            }
        }

        // =====================================================================
        // MASTER RENDER DISPATCH
        // =====================================================================
        render(dt) {
            const ctx = this.ctx;
            const w = this.width;
            const h = this.height;
            if (!ctx || w <= 0 || h <= 0) return;

            const cpuBoost = this._getCpuFactor();

            switch (this.themeId) {
                case 'amiko-matrix':
                    this._renderMatrix(ctx, w, h, dt * cpuBoost);
                    break;
                case 'amiko-cyberpunk':
                    this._renderCyberpunk(ctx, w, h, dt * cpuBoost);
                    break;
                case 'amiko-synthwave':
                    this._renderSynthwave(ctx, w, h, dt * cpuBoost);
                    break;
                case 'amiko-batman':
                    this._renderBatman(ctx, w, h, dt * cpuBoost);
                    break;
                case 'amiko-barbie':
                    this._renderBarbie(ctx, w, h, dt * cpuBoost);
                    break;
                case 'amiko-classic':
                    this._renderClassic(ctx, w, h, dt * cpuBoost);
                    break;
                case 'amiko-pacman':
                    this._renderPacman(ctx, w, h, dt * cpuBoost);
                    break;
                case 'amiko-pipboy':
                    this._renderPipBoy(ctx, w, h, dt * cpuBoost);
                    break;
                case 'amiko-spaceinvaders':
                    this._renderSpaceInvaders(ctx, w, h, dt * cpuBoost);
                    break;
                case 'amiko-highcontrast':
                    this._renderHighContrast(ctx, w, h, dt * cpuBoost);
                    break;
                case 'amiko-nordic':
                    this._renderNordic(ctx, w, h, dt * cpuBoost);
                    break;
                case 'amiko-theme':
                default:
                    this._renderBaseConstellation(ctx, w, h, dt * cpuBoost);
                    break;
            }

            this._renderRipples(ctx, dt);
        }

        _renderRipples(ctx, dt) {
            if (!this.ripples.length) return;
            const accent = (window.AMIKO_THEME && window.AMIKO_THEME.text_primary) || '#0d7377';
            ctx.save();
            for (let i = this.ripples.length - 1; i >= 0; i--) {
                const rp = this.ripples[i];
                rp.r += dt * 260;
                rp.alpha -= dt * 0.9;
                if (rp.alpha <= 0 || rp.r >= rp.maxR) {
                    this.ripples.splice(i, 1);
                    continue;
                }
                ctx.beginPath();
                ctx.arc(rp.x, rp.y, rp.r, 0, Math.PI * 2);
                ctx.strokeStyle = accent;
                ctx.globalAlpha = rp.alpha * 0.55;
                ctx.lineWidth = 1.5;
                ctx.stroke();
            }
            ctx.restore();
        }

        // =====================================================================
        // 1. AMIKO BASE (Teal & Red Neural Network Constellation)
        // =====================================================================
        _renderBaseConstellation(ctx, w, h, dt) {
            ctx.clearRect(0, 0, w, h);

            const theme = window.AMIKO_THEME || {};
            const isCustomTheme = this.themeId !== 'amiko-theme';
            const primaryColor = isCustomTheme ? (theme.text_secondary || '#0d7377') : '#0d7377';
            const dangerColor = isCustomTheme ? (theme.accent_danger || '#c0392b') : '#c0392b';

            // Subtle technical dot matrix grid
            ctx.save();
            ctx.fillStyle = primaryColor;
            ctx.globalAlpha = 0.08;
            const step = 48;
            for (let x = step / 2; x < w; x += step) {
                for (let y = step / 2; y < h; y += step) {
                    ctx.fillRect(x, y, 1.5, 1.5);
                }
            }
            ctx.restore();

            const nodes = this.state.nodes || [];
            const linkDist = 145;
            const mouseDist = 180;

            // Update & draw nodes
            for (let i = 0; i < nodes.length; i++) {
                const n = nodes[i];
                n.x += n.vx * dt;
                n.y += n.vy * dt;
                n.pulse += dt * 2.2;

                if (n.x < 0) { n.x = 0; n.vx *= -1; }
                if (n.x > w) { n.x = w; n.vx *= -1; }
                if (n.y < 0) { n.y = 0; n.vy *= -1; }
                if (n.y > h) { n.y = h; n.vy *= -1; }

                // Gentle attraction/orbit to cursor
                if (this.mouse.active) {
                    const dx = this.mouse.x - n.x;
                    const dy = this.mouse.y - n.y;
                    const d = Math.hypot(dx, dy);
                    if (d < mouseDist && d > 1) {
                        const force = (1 - d / mouseDist) * 18 * dt;
                        n.x += (dx / d) * force;
                        n.y += (dy / d) * force;
                    }
                }
            }

            // Connections between nodes
            ctx.save();
            ctx.lineWidth = 1;
            for (let i = 0; i < nodes.length; i++) {
                const a = nodes[i];
                for (let j = i + 1; j < nodes.length; j++) {
                    const b = nodes[j];
                    const dx = a.x - b.x;
                    const dy = a.y - b.y;
                    const dist = Math.hypot(dx, dy);
                    if (dist < linkDist) {
                        const alpha = (1 - dist / linkDist) * 0.22;
                        ctx.strokeStyle = (a.accent || b.accent) ? dangerColor : primaryColor;
                        ctx.globalAlpha = alpha;
                        ctx.beginPath();
                        ctx.moveTo(a.x, a.y);
                        ctx.lineTo(b.x, b.y);
                        ctx.stroke();
                    }
                }

                // Connection to cursor
                if (this.mouse.active) {
                    const mdx = this.mouse.x - a.x;
                    const mdy = this.mouse.y - a.y;
                    const mdist = Math.hypot(mdx, mdy);
                    if (mdist < mouseDist) {
                        ctx.strokeStyle = a.accent ? dangerColor : primaryColor;
                        ctx.globalAlpha = (1 - mdist / mouseDist) * 0.38;
                        ctx.beginPath();
                        ctx.moveTo(a.x, a.y);
                        ctx.lineTo(this.mouse.x, this.mouse.y);
                        ctx.stroke();
                    }
                }
            }

            // Draw nodes & pulsing halos
            for (let i = 0; i < nodes.length; i++) {
                const n = nodes[i];
                const col = n.accent ? dangerColor : primaryColor;
                ctx.fillStyle = col;
                ctx.globalAlpha = n.accent ? 0.65 : 0.42;
                ctx.beginPath();
                ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
                ctx.fill();

                if (n.accent) {
                    const haloR = n.r + 3 + Math.sin(n.pulse) * 2.5;
                    ctx.strokeStyle = dangerColor;
                    ctx.globalAlpha = 0.2;
                    ctx.beginPath();
                    ctx.arc(n.x, n.y, haloR, 0, Math.PI * 2);
                    ctx.stroke();
                }
            }
            ctx.restore();
        }

        // =====================================================================
        // 2. MATRIX (Authentic Film Digital Rain — Mirrored Katakana & Glyphs)
        // =====================================================================
        _renderMatrix(ctx, w, h, dt) {
            const st = this.state;
            if (!st.streams) return;

            ctx.clearRect(0, 0, w, h);
            ctx.save();
            ctx.font = `bold ${st.fontSize}px "MS Gothic", "Hiragino Kaku Gothic Pro", "Meiryo", monospace`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';

            const chars = st.chars;
            const fs = st.fontSize;

            for (let c = 0; c < st.cols; c++) {
                const col = st.streams[c];
                const x = c * fs + fs * 0.5;

                // Cursor proximity accelerates and illuminates nearby columns
                let nearCursor = false;
                if (this.mouse.active && Math.abs(x - this.mouse.x) < 90) {
                    nearCursor = true;
                }

                col.head += col.speed * (nearCursor ? 1.65 : 1.0) * dt;
                const headRow = Math.floor(col.head);

                // Randomly mutate one glyph in the column (classic Matrix shimmer)
                if (Math.random() < 0.18 && col.glyphs.length > 0) {
                    const idx = Math.floor(Math.random() * col.glyphs.length);
                    col.glyphs[idx].ch = chars.charAt(Math.floor(Math.random() * chars.length));
                }

                for (let k = 0; k < col.trailLen; k++) {
                    const r = headRow - k;
                    if (r < 0 || r >= st.rows) continue;

                    const g = col.glyphs[r % col.glyphs.length];
                    const y = r * fs;
                    const fade = 1 - k / col.trailLen;

                    ctx.save();
                    ctx.translate(x, y);
                    if (g.mirror) {
                        ctx.scale(-1, 1); // Horizontally mirrored glyph like the film's title sequence
                    }

                    if (k === 0) {
                        // Leading white-green glowing glyph
                        ctx.fillStyle = '#e8ffe8';
                        ctx.shadowColor = '#00ff41';
                        ctx.shadowBlur = 8;
                        ctx.globalAlpha = 0.95;
                    } else if (k <= 2) {
                        ctx.fillStyle = '#00ff41';
                        ctx.globalAlpha = 0.82;
                    } else {
                        ctx.fillStyle = '#00b82e';
                        ctx.globalAlpha = Math.pow(fade, 1.35) * 0.68;
                    }

                    ctx.fillText(g.ch, 0, 0);
                    ctx.restore();
                }

                if (headRow - col.trailLen > st.rows) {
                    col.head = -Math.random() * 14;
                    col.speed = 5.5 + Math.random() * 9.5;
                    col.trailLen = 10 + Math.floor(Math.random() * 18);
                }
            }
            ctx.restore();
        }

        // =====================================================================
        // 3. CYBERPUNK (Night City Skyline — layered haze, hero towers, AVs)
        // =====================================================================
        _buildCyberpunkCity(w, h) {
            const od = Math.min(this.dpr || 1, 1.5); // offscreen density (memory-friendly)
            const margin = 60;                       // parallax overscan
            const rand = (a, b) => a + Math.random() * (b - a);
            const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

            // ---- Generic skyline layer, baked once into an offscreen canvas ----
            const makeLayer = (cfg) => {
                const cw = w + margin * 2;
                const top = Math.max(0, Math.floor(h - h * cfg.maxH * 1.65 - 60));
                const chh = h - top;
                const canvas = document.createElement('canvas');
                canvas.width = Math.ceil(cw * od);
                canvas.height = Math.ceil(chh * od);
                const c = canvas.getContext('2d');
                // Local x (0..cw), screen-space y
                c.setTransform(od, 0, 0, od, 0, -top * od);

                const flickers = [];
                const signs = [];
                const beacons = [];

                let x = -rand(0, 30);
                while (x < cw) {
                    const bw = rand(cfg.minW, cfg.maxW);
                    let bh = rand(cfg.minH, cfg.maxH) * h;
                    if (Math.random() < cfg.landmark) bh *= rand(1.25, 1.55);
                    const by = h - bh;

                    // Body gradient: catches sky glow on top, sinks into darkness below
                    const g = c.createLinearGradient(0, by, 0, h);
                    g.addColorStop(0, cfg.top);
                    g.addColorStop(1, cfg.bottom);
                    c.fillStyle = g;

                    const roof = Math.random();
                    let isFlat = false;
                    c.beginPath();
                    if (roof < 0.18) {
                        const ch = Math.min(bw * 0.35, 16);
                        c.moveTo(x, h); c.lineTo(x, by + ch); c.lineTo(x + ch, by);
                        c.lineTo(x + bw, by); c.lineTo(x + bw, h);
                    } else if (roof < 0.32) {
                        const cr = bh * 0.04;
                        c.moveTo(x, h); c.lineTo(x, by); c.lineTo(x + bw * 0.2, by);
                        c.lineTo(x + bw * 0.2, by - cr); c.lineTo(x + bw * 0.8, by - cr);
                        c.lineTo(x + bw * 0.8, by); c.lineTo(x + bw, by); c.lineTo(x + bw, h);
                    } else if (roof < 0.42) {
                        const s = Math.min(bw * 0.5, 22);
                        if (Math.random() < 0.5) {
                            c.moveTo(x, h); c.lineTo(x, by + s); c.lineTo(x + bw, by); c.lineTo(x + bw, h);
                        } else {
                            c.moveTo(x, h); c.lineTo(x, by); c.lineTo(x + bw, by + s); c.lineTo(x + bw, h);
                        }
                    } else {
                        c.rect(x, by, bw, bh);
                        isFlat = true;
                    }
                    c.closePath();
                    c.fill();

                    // Rooftop clutter (AC units, tanks) on flat roofs
                    if (cfg.clutter && isFlat) {
                        c.fillStyle = cfg.top;
                        const n = Math.floor(rand(0, 3.5));
                        for (let i = 0; i < n; i++) {
                            const uw = rand(4, 12);
                            const uh = rand(3, 9);
                            c.fillRect(x + rand(2, Math.max(3, bw - uw - 2)), by - uh, uw, uh);
                        }
                    }

                    // Soft rim light along one edge (light bounced from the neon city)
                    if (cfg.rimAlpha > 0 && Math.random() < 0.55) {
                        const rimCol = pick(cfg.rimColors);
                        const rg = c.createLinearGradient(0, by, 0, h);
                        rg.addColorStop(0, `rgba(${rimCol}, ${cfg.rimAlpha})`);
                        rg.addColorStop(0.6, `rgba(${rimCol}, ${cfg.rimAlpha * 0.3})`);
                        rg.addColorStop(1, `rgba(${rimCol}, 0)`);
                        c.fillStyle = rg;
                        c.fillRect(Math.random() < 0.5 ? x : x + bw - 1, by + 18, 1, bh - 18);
                    }

                    // Windows: building-dominant tint, occasional fully-lit floors
                    const cols = Math.floor((bw - 6) / (cfg.winW + cfg.gapX));
                    const rows = Math.floor((bh - 12) / (cfg.winH + cfg.gapY));
                    if (cols > 0 && rows > 0) {
                        const offX = x + (bw - (cols * (cfg.winW + cfg.gapX) - cfg.gapX)) / 2;
                        const density = rand(cfg.minLit, cfg.maxLit);
                        const tint = pick(cfg.palette);
                        for (let r = 0; r < rows; r++) {
                            const wy = by + 10 + r * (cfg.winH + cfg.gapY);
                            if (wy > h - 6) break;
                            const rowLit = Math.random() < 0.1 ? Math.min(0.9, density * 3) : density;
                            for (let k = 0; k < cols; k++) {
                                if (Math.random() > rowLit) continue;
                                const col = Math.random() < 0.72 ? tint : pick(cfg.palette);
                                const a = rand(cfg.winAlpha * 0.5, cfg.winAlpha);
                                const wx = offX + k * (cfg.winW + cfg.gapX);
                                if (Math.random() < cfg.flickerChance) {
                                    flickers.push({ x: wx, y: wy, w: cfg.winW, h: cfg.winH, color: col, a, phase: rand(0, 6.28), speed: rand(0.5, 2.0) });
                                    continue;
                                }
                                c.globalAlpha = a;
                                c.fillStyle = col;
                                c.fillRect(wx, wy, cfg.winW, cfg.winH);
                            }
                        }
                        c.globalAlpha = 1;
                    }

                    // Thin neon roof trim (flat roofs only, sparse)
                    if (isFlat && cfg.neonChance > 0 && Math.random() < cfg.neonChance) {
                        const nc = pick(cfg.neonPalette);
                        c.save();
                        c.shadowColor = nc;
                        c.shadowBlur = 8;
                        c.strokeStyle = nc;
                        c.globalAlpha = 0.7;
                        c.lineWidth = 1.2;
                        c.beginPath();
                        c.moveTo(x + 2, by + 0.5);
                        c.lineTo(x + bw - 2, by + 0.5);
                        c.stroke();
                        c.restore();
                    }

                    // Abstract neon signage (blank glowing panels, no text) — animated later
                    if (cfg.signChance > 0 && Math.random() < cfg.signChance && bh > h * 0.12) {
                        const color = pick(cfg.neonPalette);
                        const rects = [];
                        if (Math.random() < 0.6) {
                            const sx = Math.random() < 0.5 ? x - 2 : x + bw - 4;
                            const sy = by + rand(0.1, 0.4) * bh;
                            const n = 4 + Math.floor(Math.random() * 5);
                            for (let k = 0; k < n; k++) rects.push([sx, sy + k * 7, 6, 5]);
                        } else {
                            const sw = Math.min(bw * 0.7, rand(14, 30));
                            rects.push([x + (bw - sw) / 2, by + rand(0.08, 0.3) * bh, sw, 4]);
                        }
                        signs.push({ rects, color, a: rand(0.6, 0.85), off: 0 });
                    }

                    // Antenna mast + aviation beacon
                    if (Math.random() < cfg.antennaChance) {
                        const ax = x + bw * rand(0.3, 0.7);
                        const ah = rand(10, 34);
                        c.strokeStyle = cfg.top;
                        c.lineWidth = 1;
                        c.beginPath();
                        c.moveTo(ax, by);
                        c.lineTo(ax, by - ah);
                        c.stroke();
                        beacons.push({ x: ax, y: by - ah, r: cfg.beaconR, phase: rand(0, 6.28), color: Math.random() < 0.8 ? '#ff3355' : '#ffb35c' });
                    }

                    x += bw + rand(cfg.gapMin, cfg.gapMax);
                }

                return { canvas, margin, top, cssW: cw, cssH: chh, flickers, signs, beacons };
            };

            const layers = [
                // FAR — hazy indigo silhouettes, tiny cool windows
                makeLayer({
                    minW: 22, maxW: 60, minH: 0.30, maxH: 0.50, landmark: 0.12, gapMin: -4, gapMax: 3,
                    top: '#2a2263', bottom: '#1a1543',
                    winW: 1.4, winH: 1.6, gapX: 2.6, gapY: 3.2, winAlpha: 0.42, minLit: 0.10, maxLit: 0.30,
                    palette: ['#ffd9a0', '#cfe6ff', '#cfe6ff', '#9fdcff', '#ff9ae6'],
                    rimAlpha: 0.10, rimColors: ['255, 70, 200', '80, 220, 255'],
                    neonChance: 0, neonPalette: ['#ff2bd6'], signChance: 0,
                    antennaChance: 0.22, beaconR: 1.1, clutter: false, flickerChance: 0
                }),
                // MID — downtown: richer windows, sparse neon trims & blank signage
                makeLayer({
                    minW: 34, maxW: 86, minH: 0.20, maxH: 0.42, landmark: 0.12, gapMin: -6, gapMax: 4,
                    top: '#15113a', bottom: '#0a091f',
                    winW: 2, winH: 2.6, gapX: 3.2, gapY: 4.2, winAlpha: 0.78, minLit: 0.10, maxLit: 0.32,
                    palette: ['#ffd9a0', '#ffd9a0', '#ffe8c4', '#cfe6ff', '#7ff3ff', '#ff7ad9', '#ffb35c'],
                    rimAlpha: 0.22, rimColors: ['255, 70, 200', '80, 220, 255'],
                    neonChance: 0.20, neonPalette: ['#ff2bd6', '#2be8ff', '#ffd23f'], signChance: 0.16,
                    antennaChance: 0.30, beaconR: 1.5, clutter: false, flickerChance: 0.015
                }),
                // NEAR — almost-black rooftops, only warm interior lights (no cold cyan)
                makeLayer({
                    minW: 46, maxW: 120, minH: 0.07, maxH: 0.19, landmark: 0.05, gapMin: -2, gapMax: 2,
                    top: '#0b0a1b', bottom: '#030308',
                    winW: 3, winH: 3.6, gapX: 6, gapY: 7, winAlpha: 0.6, minLit: 0.03, maxLit: 0.12,
                    palette: ['#ffcf8a', '#ffd9a0', '#ffb35c', '#ffe8c4'],
                    rimAlpha: 0.14, rimColors: ['255, 70, 200', '255, 140, 80'],
                    neonChance: 0.07, neonPalette: ['#ff2bd6', '#ff4d6d', '#ffb35c'], signChance: 0.06,
                    antennaChance: 0.18, beaconR: 1.8, clutter: true, flickerChance: 0.02
                })
            ];

            // ---- Hero towers (left: magenta hologram tower, right: cyan screen tower) ----
            const buildHero = (side) => {
                const tw = Math.round(Math.max(110, w * (side === 'left' ? 0.135 : 0.12)));
                const topY = Math.round(h * (side === 'left' ? 0.24 : 0.19));
                const oy = topY - 110;
                const cw = tw + 24;
                const chh = h - oy;
                const canvas = document.createElement('canvas');
                canvas.width = Math.ceil(cw * od);
                canvas.height = Math.ceil(chh * od);
                const c = canvas.getContext('2d');
                c.setTransform(od, 0, 0, od, 0, -oy * od);

                const x = 12;
                const ch = 18;
                const g = c.createLinearGradient(0, topY, 0, h);
                g.addColorStop(0, '#1d1746');
                g.addColorStop(1, '#08071a');
                c.fillStyle = g;
                c.beginPath();
                c.moveTo(x, h); c.lineTo(x, topY + ch); c.lineTo(x + ch, topY);
                c.lineTo(x + tw - ch, topY); c.lineTo(x + tw, topY + ch); c.lineTo(x + tw, h);
                c.closePath();
                c.fill();
                c.fillRect(x + tw * 0.3, topY - 34, tw * 0.4, 34);

                const mastX = x + tw * 0.5;
                c.strokeStyle = '#1d1746';
                c.lineWidth = 2;
                c.beginPath();
                c.moveTo(mastX, topY - 34);
                c.lineTo(mastX, topY - 100);
                c.stroke();

                // Dense small windows
                const pal = side === 'left' ? ['#ff9ae6', '#ffd9a0', '#ff7ad9'] : ['#9ff4ff', '#e6f6ff', '#7ff3ff'];
                for (let wy = topY + 26; wy < h - 6; wy += 6) {
                    for (let wx = x + 6; wx < x + tw - 6; wx += 5) {
                        if (Math.random() > 0.2) continue;
                        c.globalAlpha = rand(0.2, 0.5);
                        c.fillStyle = pick(pal);
                        c.fillRect(wx, wy, 2, 2);
                    }
                }
                c.globalAlpha = 1;

                // Rim light on the edge facing the city centre
                const rimX = side === 'left' ? x + tw - 1.5 : x;
                const rimRGB = side === 'left' ? '255, 60, 210' : '60, 230, 255';
                const rg = c.createLinearGradient(0, topY, 0, h);
                rg.addColorStop(0, `rgba(${rimRGB}, 0.55)`);
                rg.addColorStop(0.5, `rgba(${rimRGB}, 0.22)`);
                rg.addColorStop(1, `rgba(${rimRGB}, 0.04)`);
                c.fillStyle = rg;
                c.fillRect(rimX, topY + ch, 1.5, h - topY - ch);

                if (side === 'right') {
                    c.fillStyle = 'rgba(60, 230, 255, 0.10)';
                    for (let yy = topY + 60; yy < h - 40; yy += 46) c.fillRect(x + 4, yy, tw - 8, 1);
                }

                const gutter = Math.round(w * 0.012);
                const screenX = side === 'left' ? gutter : w - gutter - tw;
                return {
                    canvas, sx: screenX - x, sy: oy, cssW: cw, cssH: chh,
                    x: screenX, topY, tw,
                    beacon: { x: screenX + tw * 0.5, y: topY - 100, phase: Math.random() * 6.28 }
                };
            };

            const heroL = buildHero('left');
            const heroR = buildHero('right');

            const bbW = heroL.tw * 0.76;
            const billboard = {
                x: heroL.x + heroL.tw * 0.12,
                y: heroL.topY + h * 0.07,
                w: bbW,
                h: Math.min(h * 0.24, bbW * 1.35),
                glitch: 0
            };

            const screens = [
                { x: heroR.x + heroR.tw * 0.15, y: heroR.topY + h * 0.10, w: heroR.tw * 0.30, h: h * 0.07, c1: '#38f0ff', c2: '#0a6c9c' },
                { x: heroR.x + heroR.tw * 0.55, y: heroR.topY + h * 0.21, w: heroR.tw * 0.32, h: h * 0.10, c1: '#2bffc6', c2: '#0b6d6a' },
                { x: heroR.x + heroR.tw * 0.20, y: heroR.topY + h * 0.37, w: heroR.tw * 0.45, h: h * 0.05, c1: '#5aa8ff', c2: '#1d3d9c' }
            ].map(s => Object.assign(s, { phase: Math.random() * 6.28, speed: 0.6 + Math.random() * 0.8 }));

            // ---- Volumetric searchlights from the city core ----
            const beams = [];
            for (let i = 0; i < 6; i++) {
                beams.push({
                    xFrac: 0.30 + (i / 5) * 0.40 + (Math.random() - 0.5) * 0.04,
                    w: 18 + Math.random() * 24,
                    hFrac: 0.60 + Math.random() * 0.30,
                    isCyan: i % 2 === 0,
                    swaySpeed: 0.2 + Math.random() * 0.3,
                    phase: Math.random() * Math.PI * 2
                });
            }

            // ---- Flying AVs ----
            const avs = [];
            for (let i = 0; i < 6; i++) {
                const dir = i % 2 === 0 ? 1 : -1;
                avs.push({
                    x: Math.random() * w,
                    y: 0.18 + Math.random() * 0.32,
                    vx: dir * (22 + Math.random() * 34),
                    s: 5 + Math.random() * 5,
                    ph: Math.random() * 6.28
                });
            }

            // ---- Drifting mist banks between layers ----
            const mistCols = ['120, 90, 220', '200, 70, 200', '80, 150, 255'];
            const mist = [];
            for (let i = 0; i < 7; i++) {
                const layer = i < 4 ? 0 : 1;
                mist.push({
                    layer,
                    x: Math.random() * 1.4 - 0.2,
                    y: layer === 0 ? 0.62 + Math.random() * 0.10 : 0.79 + Math.random() * 0.08,
                    r: 0.18 + Math.random() * 0.14,
                    vx: (Math.random() < 0.5 ? -1 : 1) * (0.004 + Math.random() * 0.006),
                    rgb: mistCols[i % mistCols.length],
                    a: 0.07 + Math.random() * 0.05
                });
            }

            // ---- Faint stars (light-polluted sky) ----
            const stars = [];
            for (let i = 0; i < 45; i++) {
                stars.push({
                    x: Math.random(), y: Math.random() * 0.38,
                    r: Math.random() < 0.8 ? 1 : 1.4,
                    a: 0.12 + Math.random() * 0.28,
                    sp: 0.5 + Math.random() * 1.5, ph: Math.random() * 6.28
                });
            }

            return { layers, heroes: [heroL, heroR], billboard, screens, beams, avs, mist, stars, parallaxX: 0, rimPulse: 0 };
        }

        _drawCyberLayer(ctx, L, ox, dt) {
            const t = this.time;
            const x0 = -L.margin + ox;
            ctx.drawImage(L.canvas, x0, L.top, L.cssW, L.cssH);

            // Flickering windows
            for (const f of L.flickers) {
                ctx.globalAlpha = f.a * (0.3 + 0.7 * (0.5 + 0.5 * Math.sin(t * f.speed + f.phase)));
                ctx.fillStyle = f.color;
                ctx.fillRect(x0 + f.x, f.y, f.w, f.h);
            }
            ctx.globalAlpha = 1;

            // Neon signage with occasional electrical buzz
            for (const s of L.signs) {
                if (s.off > 0) s.off -= dt;
                else if (Math.random() < 0.0025) s.off = 0.06 + Math.random() * 0.22;
                const on = s.off <= 0;
                ctx.save();
                ctx.globalAlpha = on ? s.a : s.a * 0.15;
                ctx.fillStyle = s.color;
                ctx.shadowColor = s.color;
                ctx.shadowBlur = on ? 10 : 0;
                ctx.beginPath();
                for (const r of s.rects) ctx.rect(x0 + r[0], r[1], r[2], r[3]);
                ctx.fill();
                ctx.restore();
            }

            // Aviation beacons (soft pulse)
            ctx.save();
            for (const b of L.beacons) {
                const p = Math.sin(t * 2.2 + b.phase);
                if (p < 0.55) continue;
                ctx.globalAlpha = (p - 0.55) / 0.45;
                ctx.fillStyle = b.color;
                ctx.shadowColor = b.color;
                ctx.shadowBlur = 8;
                ctx.beginPath();
                ctx.arc(x0 + b.x, b.y, b.r, 0, Math.PI * 2);
                ctx.fill();
            }
            ctx.restore();
        }

        _renderCyberpunk(ctx, w, h, dt) {
            ctx.clearRect(0, 0, w, h);
            const st = this.state;
            if (!st || !st.layers) return;
            const t = this.time;

            // Gentle "look around" parallax
            const targetPX = this.mouse.active ? ((this.mouse.x / w) - 0.5) * -36 : 0;
            st.parallaxX += (targetPX - st.parallaxX) * Math.min(1, dt * 3);
            const px = st.parallaxX;

            ctx.save();

            // 1. Night sky (palette preserved)
            const skyGrad = ctx.createLinearGradient(0, 0, 0, h);
            skyGrad.addColorStop(0, '#040410');
            skyGrad.addColorStop(0.5, '#070a22');
            skyGrad.addColorStop(0.82, '#120d2d');
            skyGrad.addColorStop(1, '#060716');
            ctx.fillStyle = skyGrad;
            ctx.fillRect(0, 0, w, h);

            const haze = (fx, fy, rFrac, rgb, a) => {
                const g = ctx.createRadialGradient(w * fx, h * fy, 20, w * fx, h * fy, w * rFrac);
                g.addColorStop(0, `rgba(${rgb}, ${a})`);
                g.addColorStop(1, `rgba(${rgb}, 0)`);
                ctx.fillStyle = g;
                ctx.fillRect(0, 0, w, h);
            };
            haze(0.15, 0.65, 0.45, '255, 0, 140', 0.16);
            haze(0.50, 0.72, 0.50, '0, 180, 255', 0.14);
            haze(0.85, 0.65, 0.45, '0, 255, 255', 0.15);

            // Faint stars
            ctx.fillStyle = '#cdd6ff';
            for (const s of st.stars) {
                ctx.globalAlpha = s.a * (0.6 + 0.4 * Math.sin(t * s.sp + s.ph));
                ctx.fillRect(s.x * w + px * 0.05, s.y * h, s.r, s.r);
            }
            ctx.globalAlpha = 1;

            // 2. Volumetric searchlights (bases hidden behind the skyline)
            for (const b of st.beams) {
                const bx = w * b.xFrac + Math.sin(t * b.swaySpeed + b.phase) * 12 + px * 0.12;
                const byTop = h * (1 - b.hFrac);
                const bottom = h * 0.72;
                const bg = ctx.createLinearGradient(0, byTop, 0, bottom);
                const col = b.isCyan ? 'rgba(0, 240, 255, ' : 'rgba(210, 30, 255, ';
                bg.addColorStop(0, col + '0)');
                bg.addColorStop(0.5, col + '0.06)');
                bg.addColorStop(1, col + '0.02)');
                ctx.fillStyle = bg;
                ctx.fillRect(bx - b.w * 0.5, byTop, b.w, bottom - byTop);
            }

            const drawMist = (layerIdx) => {
                for (const m of st.mist) {
                    if (m.layer !== layerIdx) continue;
                    m.x += m.vx * dt;
                    if (m.x > 1.35) m.x = -0.35;
                    if (m.x < -0.35) m.x = 1.35;
                    const r = m.r * w;
                    ctx.save();
                    ctx.translate(m.x * w + px * (layerIdx === 0 ? 0.25 : 0.7), m.y * h);
                    ctx.scale(1, 0.32);
                    const g = ctx.createRadialGradient(0, 0, 0, 0, 0, r);
                    g.addColorStop(0, `rgba(${m.rgb}, ${m.a})`);
                    g.addColorStop(1, `rgba(${m.rgb}, 0)`);
                    ctx.fillStyle = g;
                    ctx.fillRect(-r, -r, r * 2, r * 2);
                    ctx.restore();
                }
            };

            // 3. FAR skyline + atmospheric fog
            this._drawCyberLayer(ctx, st.layers[0], px * 0.15, dt);
            const fogA = ctx.createLinearGradient(0, h * 0.45, 0, h);
            fogA.addColorStop(0, 'rgba(60, 40, 130, 0)');
            fogA.addColorStop(0.55, 'rgba(70, 45, 140, 0.26)');
            fogA.addColorStop(1, 'rgba(40, 25, 90, 0.5)');
            ctx.fillStyle = fogA;
            ctx.fillRect(0, h * 0.45, w, h * 0.55);
            drawMist(0);

            // 4. HERO TOWERS
            const hp = px * 0.35;
            for (const hero of st.heroes) {
                ctx.drawImage(hero.canvas, hero.sx + hp, hero.sy, hero.cssW, hero.cssH);
                const p = Math.sin(t * 1.8 + hero.beacon.phase);
                if (p > 0.5) {
                    ctx.save();
                    ctx.globalAlpha = (p - 0.5) / 0.5;
                    ctx.fillStyle = '#ff3355';
                    ctx.shadowColor = '#ff3355';
                    ctx.shadowBlur = 10;
                    ctx.beginPath();
                    ctx.arc(hero.beacon.x + hp, hero.beacon.y, 2.2, 0, Math.PI * 2);
                    ctx.fill();
                    ctx.restore();
                }
            }

            // Left tower: abstract holographic billboard (no text)
            const bb = st.billboard;
            if (bb) {
                const bx = bb.x + hp;
                const by = bb.y;
                const bw = bb.w;
                const bh = bb.h;
                const pulse = 0.85 + 0.15 * Math.sin(t * 1.3);

                ctx.save();
                ctx.globalCompositeOperation = 'lighter';
                const spill = ctx.createRadialGradient(bx + bw / 2, by + bh / 2, 10, bx + bw / 2, by + bh / 2, Math.max(bw, bh) * 1.4);
                spill.addColorStop(0, `rgba(255, 40, 200, ${0.2 * pulse})`);
                spill.addColorStop(1, 'rgba(255, 40, 200, 0)');
                ctx.fillStyle = spill;
                ctx.fillRect(bx - bw * 1.5, by - bh * 1.5, bw * 4, bh * 4);
                ctx.restore();

                if (bb.glitch > 0) bb.glitch -= dt;
                else if (Math.random() < 0.004) bb.glitch = 0.12 + Math.random() * 0.18;
                const glitching = bb.glitch > 0;

                ctx.save();
                ctx.beginPath();
                ctx.rect(bx, by, bw, bh);
                ctx.clip();

                const shift = 0.5 + 0.5 * Math.sin(t * 0.35);
                const pg = ctx.createLinearGradient(bx, by, bx, by + bh);
                pg.addColorStop(0, `rgba(${Math.round(255 - 70 * shift)}, 30, ${Math.round(190 + 60 * shift)}, 0.85)`);
                pg.addColorStop(1, `rgba(90, 10, ${Math.round(140 + 60 * shift)}, 0.85)`);
                ctx.fillStyle = pg;
                ctx.fillRect(bx, by, bw, bh);

                const cx = bx + bw / 2 + (glitching ? (Math.random() - 0.5) * 8 : 0);
                const cy = by + bh * 0.42;
                const R = Math.min(bw, bh) * 0.30;
                ctx.strokeStyle = 'rgba(255, 230, 255, 0.9)';
                ctx.lineWidth = 1.5;
                for (let i = 0; i < 3; i++) {
                    const rr = R * (0.45 + i * 0.28) * (1 + 0.05 * Math.sin(t * 1.6 + i));
                    ctx.globalAlpha = 0.85 - i * 0.22;
                    ctx.beginPath();
                    ctx.arc(cx, cy, rr, 0, Math.PI * 2);
                    ctx.stroke();
                }
                ctx.globalAlpha = 0.9;
                ctx.lineWidth = 2.5;
                ctx.beginPath();
                ctx.arc(cx, cy, R * 1.05, t * 0.9, t * 0.9 + Math.PI * 0.6);
                ctx.stroke();

                const bars = 7;
                const barW = (bw * 0.7) / bars;
                const baseY = by + bh * 0.9;
                ctx.fillStyle = 'rgba(255, 220, 250, 0.8)';
                for (let i = 0; i < bars; i++) {
                    const hh = bh * (0.04 + 0.10 * (0.5 + 0.5 * Math.sin(t * 2.4 + i * 0.9)));
                    ctx.fillRect(bx + bw * 0.15 + i * barW + barW * 0.2, baseY - hh, barW * 0.6, hh);
                }

                ctx.globalAlpha = 0.22;
                ctx.fillStyle = '#12001c';
                for (let sy = by; sy < by + bh; sy += 3) ctx.fillRect(bx, sy, bw, 1);

                if (glitching) {
                    ctx.globalAlpha = 0.55;
                    ctx.fillStyle = Math.random() < 0.5 ? '#2be8ff' : '#ffffff';
                    ctx.fillRect(bx, by + Math.random() * bh, bw, 2 + Math.random() * 6);
                }
                ctx.restore();

                ctx.save();
                ctx.strokeStyle = '#ff5ad8';
                ctx.shadowColor = '#ff2bd6';
                ctx.shadowBlur = 12;
                ctx.lineWidth = 1.2;
                ctx.globalAlpha = glitching ? 0.5 : 0.9;
                ctx.strokeRect(bx + 0.5, by + 0.5, bw - 1, bh - 1);
                ctx.restore();
            }

            // Right tower: soft cyan/teal screens + light pulse climbing the rim
            for (const s of st.screens) {
                const sx = s.x + hp;
                const a = 0.5 + 0.25 * Math.sin(t * s.speed + s.phase);
                ctx.save();
                ctx.globalAlpha = a;
                ctx.shadowColor = s.c1;
                ctx.shadowBlur = 14;
                const g = ctx.createLinearGradient(sx, s.y, sx, s.y + s.h);
                g.addColorStop(0, s.c1);
                g.addColorStop(1, s.c2);
                ctx.fillStyle = g;
                ctx.fillRect(sx, s.y, s.w, s.h);
                ctx.shadowBlur = 0;
                ctx.globalAlpha = a * 0.35;
                ctx.fillStyle = '#001018';
                for (let yy = s.y; yy < s.y + s.h; yy += 3) ctx.fillRect(sx, yy, s.w, 1);
                ctx.restore();
            }
            const heroR = st.heroes[1];
            st.rimPulse = (st.rimPulse + dt * 0.12) % 1;
            const pulseY = h - st.rimPulse * (h - heroR.topY);
            const rp = ctx.createLinearGradient(0, pulseY, 0, pulseY + 70);
            rp.addColorStop(0, 'rgba(120, 245, 255, 0.85)');
            rp.addColorStop(1, 'rgba(120, 245, 255, 0)');
            ctx.fillStyle = rp;
            ctx.fillRect(heroR.x + hp - 0.5, pulseY, 2, 70);

            // 5. FLYING AVs
            for (const av of st.avs) {
                av.x += av.vx * dt;
                if (av.vx > 0 && av.x > w + 80) { av.x = -80; av.y = 0.18 + Math.random() * 0.32; }
                if (av.vx < 0 && av.x < -80) { av.x = w + 80; av.y = 0.18 + Math.random() * 0.32; }
                const ax = av.x + px * 0.4;
                const ay = av.y * h + Math.sin(t * 1.3 + av.ph) * 2;
                const dir = av.vx > 0 ? 1 : -1;

                const tailX = ax - dir * av.s;
                const tg = ctx.createLinearGradient(tailX, ay, tailX - dir * av.s * 3.5, ay);
                tg.addColorStop(0, 'rgba(255, 50, 90, 0.55)');
                tg.addColorStop(1, 'rgba(255, 50, 90, 0)');
                ctx.strokeStyle = tg;
                ctx.lineWidth = 1.2;
                ctx.beginPath();
                ctx.moveTo(tailX, ay);
                ctx.lineTo(tailX - dir * av.s * 3.5, ay);
                ctx.stroke();

                ctx.fillStyle = '#07061a';
                ctx.beginPath();
                ctx.ellipse(ax, ay, av.s, av.s * 0.28, 0, 0, Math.PI * 2);
                ctx.fill();
                ctx.fillStyle = 'rgba(150, 200, 255, 0.25)';
                ctx.fillRect(ax - av.s * 0.6, ay + av.s * 0.24, av.s * 1.2, 1);

                ctx.save();
                ctx.fillStyle = '#fff6e0';
                ctx.shadowColor = '#fff1c2';
                ctx.shadowBlur = 6;
                ctx.beginPath();
                ctx.arc(ax + dir * av.s * 0.9, ay, 1.1, 0, Math.PI * 2);
                ctx.fill();
                if (Math.sin(t * 5 + av.ph) > 0.6) {
                    ctx.fillStyle = '#ff3355';
                    ctx.shadowColor = '#ff3355';
                    ctx.beginPath();
                    ctx.arc(ax - dir * av.s * 0.4, ay - av.s * 0.3, 1.1, 0, Math.PI * 2);
                    ctx.fill();
                }
                ctx.restore();
            }

            // 6. MID skyline + warm magenta city glow + mist
            this._drawCyberLayer(ctx, st.layers[1], px * 0.55, dt);
            const fogB = ctx.createLinearGradient(0, h * 0.66, 0, h);
            fogB.addColorStop(0, 'rgba(180, 40, 160, 0)');
            fogB.addColorStop(0.7, 'rgba(170, 40, 150, 0.15)');
            fogB.addColorStop(1, 'rgba(120, 30, 120, 0.26)');
            ctx.fillStyle = fogB;
            ctx.fillRect(0, h * 0.66, w, h * 0.34);
            drawMist(1);

            // 7. NEAR rooftops (dark silhouettes, warm lights only)
            this._drawCyberLayer(ctx, st.layers[2], px * 0.95, dt);
            const shade = ctx.createLinearGradient(0, h * 0.86, 0, h);
            shade.addColorStop(0, 'rgba(2, 2, 8, 0)');
            shade.addColorStop(1, 'rgba(2, 2, 8, 0.55)');
            ctx.fillStyle = shade;
            ctx.fillRect(0, h * 0.86, w, h * 0.14);

            ctx.restore();
        }

        // =====================================================================
        // 4. SYNTHWAVE (80s Outrun Sun, Mountains & 3D Perspective Grid)
        // =====================================================================
        _renderSynthwave(ctx, w, h, dt) {
            ctx.clearRect(0, 0, w, h);
            const st = this.state;
            if (!st.stars) return;

            ctx.save();
            const horizonY = h * 0.56;
            const parallaxX = this.mouse.active ? ((this.mouse.x / w) - 0.5) * 45 : 0;

            // 1. Twinkling sky stars
            for (const star of st.stars) {
                const tw = 0.35 + 0.65 * Math.abs(Math.sin(this.time * 2 + star.phase));
                ctx.fillStyle = star.color;
                ctx.globalAlpha = tw * 0.7;
                ctx.beginPath();
                ctx.arc(star.x - parallaxX * 0.2, star.y, star.r, 0, Math.PI * 2);
                ctx.fill();
            }

            // 2. Retro Sliced Outrun Sun
            const sunX = w * 0.5 - parallaxX * 0.35;
            const sunY = horizonY - h * 0.04;
            const sunR = Math.min(w, h) * 0.22;

            ctx.save();
            ctx.beginPath();
            ctx.arc(sunX, sunY, sunR, Math.PI, 0, false);
            ctx.lineTo(sunX + sunR, horizonY);
            ctx.lineTo(sunX - sunR, horizonY);
            ctx.closePath();
            ctx.clip();

            const sunGrad = ctx.createLinearGradient(sunX, sunY - sunR, sunX, horizonY);
            sunGrad.addColorStop(0, '#ffe156');
            sunGrad.addColorStop(0.5, '#ff2975');
            sunGrad.addColorStop(1, '#9b00e8');
            ctx.fillStyle = sunGrad;
            ctx.globalAlpha = 0.42;
            ctx.fillRect(sunX - sunR, sunY - sunR, sunR * 2, sunR * 2);

            // Horizontal dark scanline slices across lower half of sun
            ctx.fillStyle = '#0e0b1e';
            ctx.globalAlpha = 0.9;
            for (let i = 0; i < 7; i++) {
                const sliceY = sunY - sunR * 0.15 + i * (sunR * 0.14);
                const sliceH = 2 + i * 1.6;
                ctx.fillRect(sunX - sunR, sliceY, sunR * 2, sliceH);
            }
            ctx.restore();

            // 3. Low-Poly Silhouette Mountains on Horizon
            const peaks = st.peaks || [];
            if (peaks.length > 1) {
                ctx.beginPath();
                ctx.moveTo(0, horizonY);
                const segW = w / (peaks.length - 1);
                for (let i = 0; i < peaks.length; i++) {
                    ctx.lineTo(i * segW, horizonY - peaks[i]);
                }
                ctx.lineTo(w, horizonY);
                ctx.closePath();
                ctx.fillStyle = '#140e29';
                ctx.globalAlpha = 0.9;
                ctx.fill();
                ctx.strokeStyle = '#00d4ff';
                ctx.globalAlpha = 0.35;
                ctx.lineWidth = 1.2;
                ctx.stroke();
            }

            // 4. 3D Perspective Neon Grid Floor
            const vanishX = w * 0.5 + parallaxX;
            ctx.strokeStyle = '#ff2975';
            ctx.lineWidth = 1.2;

            // Perspective radial lines
            const numRays = 22;
            for (let i = -numRays; i <= numRays; i++) {
                const bottomX = vanishX + i * (w / 10);
                ctx.globalAlpha = Math.max(0.08, 0.38 - Math.abs(i) * 0.014);
                ctx.beginPath();
                ctx.moveTo(vanishX, horizonY);
                ctx.lineTo(bottomX, h);
                ctx.stroke();
            }

            // Scrolling horizontal grid bars (exponential spacing for 3D depth)
            st.gridOffset = (st.gridOffset + dt * 0.55) % 1;
            const totalLines = 12;
            for (let i = 0; i < totalLines; i++) {
                const t = (i + st.gridOffset) / totalLines;
                const y = horizonY + Math.pow(t, 2.3) * (h - horizonY);
                ctx.strokeStyle = i % 2 === 0 ? '#ff2975' : '#00d4ff';
                ctx.globalAlpha = t * 0.45;
                ctx.beginPath();
                ctx.moveTo(0, y);
                ctx.lineTo(w, y);
                ctx.stroke();
            }

            // Horizon neon glow divider
            ctx.strokeStyle = '#00d4ff';
            ctx.globalAlpha = 0.65;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(0, horizonY);
            ctx.lineTo(w, horizonY);
            ctx.stroke();

            ctx.restore();
        }

        // =====================================================================
        // 5. BATMAN (Minimalist Stealth Cowl in Shadows with Piercing White Eyes)
        // =====================================================================
        _renderBatman(ctx, w, h, dt) {
            ctx.clearRect(0, 0, w, h);
            const st = this.state;
            if (!st) return;

            // Update smooth head parallax & mouse gaze tracking
            const targetTiltX = this.mouse.active ? ((this.mouse.x / w) - 0.5) * 16 : 0;
            const targetTiltY = this.mouse.active ? ((this.mouse.y / h) - 0.5) * 10 : 0;
            st.tiltX = (st.tiltX || 0) + (targetTiltX - (st.tiltX || 0)) * Math.min(1, dt * 4.5);
            st.tiltY = (st.tiltY || 0) + (targetTiltY - (st.tiltY || 0)) * Math.min(1, dt * 4.5);

            const targetGazeX = this.mouse.active ? ((this.mouse.x / w) - 0.5) * 7 : 0;
            const targetGazeY = this.mouse.active ? ((this.mouse.y / h) - 0.5) * 4.5 : 0;
            st.gazeX = (st.gazeX || 0) + (targetGazeX - (st.gazeX || 0)) * Math.min(1, dt * 7.5);
            st.gazeY = (st.gazeY || 0) + (targetGazeY - (st.gazeY || 0)) * Math.min(1, dt * 7.5);

            // Natural cinematic blink / squint cycle
            st.blinkTimer = (st.blinkTimer || 7) - dt;
            if (st.blinkTimer <= 0) {
                st.blinkProgress = (st.blinkProgress || 0) + dt * 8;
                if (st.blinkProgress >= Math.PI) {
                    st.blinkProgress = 0;
                    st.blinkTimer = 8 + Math.random() * 10;
                }
            }
            const blinkFactor = st.blinkProgress > 0 ? (1 - Math.sin(st.blinkProgress) * 0.94) : 1;

            // Ambient rare lightning flash in Gotham darkness
            st.lightningTimer = (st.lightningTimer || 22) - dt;
            if (st.lightningTimer <= 0) {
                st.lightningAlpha = 0.16;
                st.lightningTimer = 22 + Math.random() * 16;
            }
            if (st.lightningAlpha > 0) {
                st.lightningAlpha = Math.max(0, st.lightningAlpha - dt * 1.8);
            }

            ctx.save();

            // 1. Rare distant lightning illumination
            if (st.lightningAlpha > 0) {
                ctx.fillStyle = `rgba(180, 205, 235, ${st.lightningAlpha * 0.55})`;
                ctx.fillRect(0, 0, w, h);
            }

            // Proportional responsive scaling centered in the upper-mid viewport
            const baseDim = Math.min(w, h);
            const scale = Math.max(0.68, Math.min(1.3, baseDim / 640));
            const cx = w * 0.5 + st.tiltX;
            const cy = h * 0.46 + st.tiltY;

            // Subtle cowl charcoal tone (#0e0c0d / #111114) with subtle rim lighting during lightning
            const cowlColor = st.lightningAlpha > 0 ? `rgba(28, 30, 36, ${0.92 + st.lightningAlpha})` : '#0e0c0d';
            const rimAlpha = st.lightningAlpha * 1.4;

            // 2. Pointed Stealth Bat Ears emerging from upper darkness
            ctx.fillStyle = cowlColor;

            // Left Ear
            ctx.beginPath();
            ctx.moveTo(cx - 95.5 * scale, cy - 276 * scale);
            ctx.lineTo(cx - 131.5 * scale, cy - 87 * scale);
            ctx.lineTo(cx - 80.5 * scale, cy - 87 * scale);
            ctx.closePath();
            ctx.fill();

            // Right Ear
            ctx.beginPath();
            ctx.moveTo(cx + 95.5 * scale, cy - 276 * scale);
            ctx.lineTo(cx + 80.5 * scale, cy - 87 * scale);
            ctx.lineTo(cx + 131.5 * scale, cy - 87 * scale);
            ctx.closePath();
            ctx.fill();

            // 3. Faceted Lower Cowl & Jaw Guard
            ctx.beginPath();
            ctx.moveTo(cx - 105 * scale, cy + 38 * scale);
            ctx.quadraticCurveTo(cx, cy + 65 * scale, cx + 105 * scale, cy + 38 * scale);
            ctx.lineTo(cx + 91 * scale, cy + 155 * scale);
            ctx.lineTo(cx + 77 * scale, cy + 168 * scale);
            ctx.lineTo(cx + 55 * scale, cy + 188 * scale);
            ctx.lineTo(cx + 35 * scale, cy + 200 * scale);
            ctx.lineTo(cx - 35 * scale, cy + 200 * scale);
            ctx.lineTo(cx - 55 * scale, cy + 188 * scale);
            ctx.lineTo(cx - 77 * scale, cy + 168 * scale);
            ctx.lineTo(cx - 91 * scale, cy + 155 * scale);
            ctx.closePath();
            ctx.fill();

            if (rimAlpha > 0) {
                ctx.strokeStyle = `rgba(180, 210, 245, ${rimAlpha})`;
                ctx.lineWidth = 1;
                ctx.stroke();
            }

            // 4. Iconic Piercing White Bat Eyes
            const breathe = 0.92 + 0.08 * Math.sin(this.time * 2.2);
            const gx = st.gazeX * scale;
            const gy = st.gazeY * scale;

            ctx.shadowColor = '#ffffff';
            ctx.shadowBlur = (12 + 6 * Math.sin(this.time * 2.5)) * breathe;
            ctx.fillStyle = '#ffffff';
            ctx.globalAlpha = 1;

            // Left Eye
            ctx.beginPath();
            ctx.moveTo(cx - 115 * scale + gx, cy + (-24 * scale * blinkFactor) + gy);
            ctx.lineTo(cx - 21 * scale + gx, cy + (-2 * scale * blinkFactor) + gy);
            ctx.lineTo(cx - 21 * scale + gx, cy + (3 * scale * blinkFactor) + gy);
            ctx.lineTo(cx - 74 * scale + gx, cy + (17 * scale * blinkFactor) + gy);
            ctx.closePath();
            ctx.fill();

            // Right Eye
            ctx.beginPath();
            ctx.moveTo(cx + 115 * scale + gx, cy + (-24 * scale * blinkFactor) + gy);
            ctx.lineTo(cx + 74 * scale + gx, cy + (17 * scale * blinkFactor) + gy);
            ctx.lineTo(cx + 21 * scale + gx, cy + (3 * scale * blinkFactor) + gy);
            ctx.lineTo(cx + 21 * scale + gx, cy + (-2 * scale * blinkFactor) + gy);
            ctx.closePath();
            ctx.fill();

            ctx.restore();
        }

        // =====================================================================
        // 6. BARBIE (Dreamhouse Sparkle Bokeh, Floating Hearts & Glitter Trail)
        // =====================================================================
        _renderBarbie(ctx, w, h, dt) {
            ctx.clearRect(0, 0, w, h);
            const st = this.state;
            if (!st.items) return;

            ctx.save();

            // Soft pastel aurora glow in corners
            const g1 = ctx.createRadialGradient(w * 0.2, h * 0.25, 10, w * 0.2, h * 0.25, w * 0.45);
            g1.addColorStop(0, 'rgba(255, 51, 153, 0.12)');
            g1.addColorStop(1, 'rgba(255, 51, 153, 0)');
            ctx.fillStyle = g1;
            ctx.fillRect(0, 0, w, h);

            const g2 = ctx.createRadialGradient(w * 0.8, h * 0.75, 10, w * 0.8, h * 0.75, w * 0.45);
            g2.addColorStop(0, 'rgba(0, 204, 255, 0.12)');
            g2.addColorStop(1, 'rgba(0, 204, 255, 0)');
            ctx.fillStyle = g2;
            ctx.fillRect(0, 0, w, h);

            // Spawn glitter trail on mouse move
            if (this.mouse.active && Math.hypot(this.mouse.vx, this.mouse.vy) > 1.5) {
                st.trail.push({
                    x: this.mouse.x + (Math.random() - 0.5) * 18,
                    y: this.mouse.y + (Math.random() - 0.5) * 18,
                    vy: 15 + Math.random() * 25,
                    size: 4 + Math.random() * 6,
                    alpha: 0.85,
                    color: Math.random() > 0.35 ? '#ff3399' : '#00ccff'
                });
                if (st.trail.length > 45) st.trail.shift();
            }

            // Floating sparkles, hearts & bokeh
            for (const it of st.items) {
                it.x += it.vx * dt + Math.sin(this.time + it.phase) * 8 * dt;
                it.y += it.vy * dt;
                it.rot += it.vRot * dt;

                if (it.y < -30) {
                    it.y = h + 25;
                    it.x = Math.random() * w;
                }
                if (it.x < -30) it.x = w + 25;
                if (it.x > w + 30) it.x = -25;

                const twinkle = 0.35 + 0.65 * Math.abs(Math.sin(this.time * 2.2 + it.phase));
                ctx.fillStyle = it.color;
                ctx.strokeStyle = it.color;

                if (it.type === 'bokeh') {
                    ctx.globalAlpha = twinkle * 0.22;
                    ctx.beginPath();
                    ctx.arc(it.x, it.y, it.size * 1.5, 0, Math.PI * 2);
                    ctx.fill();
                } else if (it.type === 'heart') {
                    ctx.globalAlpha = twinkle * 0.45;
                    this._drawHeart(ctx, it.x, it.y, it.size * 0.85, it.rot);
                } else {
                    ctx.globalAlpha = twinkle * 0.65;
                    this._drawSparkleStar(ctx, it.x, it.y, it.size, it.rot);
                }
            }

            // Render cursor glitter trail
            for (let i = st.trail.length - 1; i >= 0; i--) {
                const tr = st.trail[i];
                tr.y += tr.vy * dt;
                tr.alpha -= dt * 1.1;
                if (tr.alpha <= 0) {
                    st.trail.splice(i, 1);
                    continue;
                }
                ctx.fillStyle = tr.color;
                ctx.globalAlpha = tr.alpha;
                this._drawSparkleStar(ctx, tr.x, tr.y, tr.size, this.time * 3);
            }

            ctx.restore();
        }

        _drawSparkleStar(ctx, x, y, r, rot) {
            ctx.save();
            ctx.translate(x, y);
            ctx.rotate(rot);
            ctx.beginPath();
            for (let i = 0; i < 4; i++) {
                const ang = (i * Math.PI) / 2;
                ctx.lineTo(Math.cos(ang) * r, Math.sin(ang) * r);
                const mid = ang + Math.PI / 4;
                ctx.lineTo(Math.cos(mid) * (r * 0.25), Math.sin(mid) * (r * 0.25));
            }
            ctx.closePath();
            ctx.fill();
            ctx.restore();
        }

        _drawHeart(ctx, x, y, size, rot) {
            ctx.save();
            ctx.translate(x, y);
            ctx.rotate(rot * 0.25);
            ctx.beginPath();
            const topCurveHeight = size * 0.3;
            ctx.moveTo(0, topCurveHeight);
            ctx.bezierCurveTo(0, 0, -size, 0, -size, topCurveHeight);
            ctx.bezierCurveTo(-size, (size + topCurveHeight) / 2, 0, (size + topCurveHeight) / 1.2, 0, size);
            ctx.bezierCurveTo(0, (size + topCurveHeight) / 1.2, size, (size + topCurveHeight) / 2, size, topCurveHeight);
            ctx.bezierCurveTo(size, 0, 0, 0, 0, topCurveHeight);
            ctx.closePath();
            ctx.fill();
            ctx.restore();
        }

        // =====================================================================
        // 7. CLASSIC (Sober Digital Matrix — Warm Orange & Rare Ottanio Accents)
        // =====================================================================
        _renderClassic(ctx, w, h, dt) {
            ctx.clearRect(0, 0, w, h);
            const st = this.state;
            if (!st || !st.activeCells) return;

            // 1. Calculate Grid geometry (Clean square blocks & subtle spacing)
            const tileSize = Math.max(18, Math.min(26, Math.floor(Math.min(w, h) / 32)));
            const gap = Math.max(3, Math.floor(tileSize * 0.16));
            const step = tileSize + gap;
            const radius = 2.5;

            const cols = Math.floor((w - gap) / step);
            const rows = Math.floor((h - gap) / step);
            const originX = Math.floor((w - (cols * step - gap)) * 0.5);
            const originY = Math.floor((h - (rows * step - gap)) * 0.5);

            ctx.save();

            // Sober Palette: Nuances of warm orange/amber, with rare ottanio (no purple, no red)
            const ORANGE_SHADES = [
                { hex: '#ff9500', glow: 'rgba(255, 149, 0, 0.45)' },
                { hex: '#ffaa00', glow: 'rgba(255, 170, 0, 0.45)' },
                { hex: '#ff8800', glow: 'rgba(255, 136, 0, 0.45)' },
                { hex: '#ffb300', glow: 'rgba(255, 179, 0, 0.45)' },
                { hex: '#f39c12', glow: 'rgba(243, 156, 18, 0.45)' }
            ];
            const OTTANIO_SHADES = [
                { hex: '#14b8a6', glow: 'rgba(20, 184, 166, 0.5)' },
                { hex: '#0d7377', glow: 'rgba(13, 115, 119, 0.5)' }
            ];

            const getRandomColor = () => {
                // Rare ottanio accent (~8-10% chance)
                if (Math.random() < 0.09) {
                    return OTTANIO_SHADES[Math.floor(Math.random() * OTTANIO_SHADES.length)];
                }
                return ORANGE_SHADES[Math.floor(Math.random() * ORANGE_SHADES.length)];
            };

            const igniteCell = (c, r, colSpec = null) => {
                if (c < 0 || c >= cols || r < 0 || r >= rows) return;
                const key = `${c},${r}`;
                const existing = st.activeCells.get(key);
                if (existing) return;
                const col = colSpec || getRandomColor();
                st.activeCells.set(key, {
                    c, r,
                    color: col.hex,
                    glow: col.glow,
                    alpha: 0.05,
                    targetAlpha: 0.7 + Math.random() * 0.25,
                    state: 'in',
                    decay: 0.18 + Math.random() * 0.12
                });
            };

            // 2. Slow, Sporadic Ambient Square Lighting (Very sober: max ~10-12 tiles active on screen)
            st.spawnTimer = (st.spawnTimer || 1.5) - dt;
            if (st.spawnTimer <= 0) {
                st.spawnTimer = 1.6 + Math.random() * 2.2;
                if (st.activeCells.size < 12) {
                    const sc = Math.floor(Math.random() * cols);
                    const sr = Math.floor(Math.random() * rows);
                    igniteCell(sc, sr);
                }
            }

            // 3. Subtle, Discreet Mouse Proximity (lights up a single hovered cell occasionally)
            if (this.mouse.active) {
                const mc = Math.floor((this.mouse.x - originX) / step);
                const mr = Math.floor((this.mouse.y - originY) / step);
                if (mc !== st.lastMouseCol || mr !== st.lastMouseRow) {
                    st.lastMouseCol = mc;
                    st.lastMouseRow = mr;
                    if (Math.random() < 0.35 && st.activeCells.size < 14) {
                        igniteCell(mc, mr);
                    }
                }
            }

            // 4. DRAW BACKGROUND IDLE TILES (Single Fast Batch Path)
            ctx.fillStyle = '#111114';
            ctx.beginPath();
            for (let c = 0; c < cols; c++) {
                const x = originX + c * step;
                for (let r = 0; r < rows; r++) {
                    const y = originY + r * step;
                    if (ctx.roundRect) ctx.roundRect(x, y, tileSize, tileSize, radius);
                    else ctx.rect(x, y, tileSize, tileSize);
                }
            }
            ctx.fill();

            // Subtle cell grid border
            ctx.strokeStyle = '#1a1a20';
            ctx.lineWidth = 1;
            ctx.stroke();

            // 5. DRAW ACTIVE GLOWING TILES (Smooth fade-in and slow fade-out)
            for (const [key, cell] of st.activeCells.entries()) {
                if (cell.state === 'in') {
                    cell.alpha += dt * 0.85;
                    if (cell.alpha >= cell.targetAlpha) {
                        cell.alpha = cell.targetAlpha;
                        cell.state = 'out';
                    }
                } else {
                    cell.alpha -= cell.decay * dt;
                    if (cell.alpha <= 0.01) {
                        st.activeCells.delete(key);
                        continue;
                    }
                }

                const x = originX + cell.c * step;
                const y = originY + cell.r * step;

                ctx.save();
                ctx.globalAlpha = Math.min(1, Math.max(0, cell.alpha));
                ctx.shadowColor = cell.glow;
                ctx.shadowBlur = 8 * cell.alpha;
                ctx.fillStyle = cell.color;

                ctx.beginPath();
                if (ctx.roundRect) ctx.roundRect(x, y, tileSize, tileSize, radius);
                else ctx.rect(x, y, tileSize, tileSize);
                ctx.fill();

                // Discreet soft highlight core for high brightness
                if (cell.alpha > 0.65) {
                    ctx.fillStyle = '#ffffff';
                    ctx.globalAlpha = (cell.alpha - 0.65) * 0.35;
                    ctx.shadowBlur = 0;
                    ctx.beginPath();
                    if (ctx.roundRect) ctx.roundRect(x + 3, y + 3, tileSize - 6, tileSize - 6, radius * 0.5);
                    else ctx.rect(x + 3, y + 3, tileSize - 6, tileSize - 6);
                    ctx.fill();
                }

                ctx.restore();
            }

            ctx.restore();
        }

        // =====================================================================
        // 8. PAC-MAN (1980 Arcade Maze, Power Pellets & Chasing Ghosts)
        // =====================================================================
        _renderPacman(ctx, w, h, dt) {
            ctx.clearRect(0, 0, w, h);
            const st = this.state;
            if (!st.actors) return;

            ctx.save();
            const cell = st.cell;

            // 1. Subtle Neon Blue Arcade Maze Walls
            ctx.strokeStyle = '#2121ff';
            ctx.lineWidth = 2;
            ctx.globalAlpha = 0.22;
            for (let r = 2; r < st.rows - 1; r += 2) {
                for (let c = 2; c < st.cols - 1; c += 2) {
                    const bx = c * cell - cell * 0.35;
                    const by = r * cell - cell * 0.35;
                    const bw = cell * 0.7;
                    ctx.strokeRect(bx, by, bw, bw);
                }
            }

            // 2. Pellets & Flashing Power Pellets
            const powerBlink = Math.sin(this.time * 7) > -0.2;
            for (const p of st.pellets) {
                if (p.eatenTimer > 0) {
                    p.eatenTimer = Math.max(0, p.eatenTimer - dt);
                    continue;
                }
                if (p.power) {
                    if (!powerBlink) continue;
                    ctx.fillStyle = '#ffb8ae';
                    ctx.globalAlpha = 0.75;
                    ctx.beginPath();
                    ctx.arc(p.x, p.y, 6, 0, Math.PI * 2);
                    ctx.fill();
                } else {
                    ctx.fillStyle = '#ffb8ae';
                    ctx.globalAlpha = 0.45;
                    ctx.fillRect(p.x - 2, p.y - 2, 4, 4);
                }
            }

            // 3. Update & Draw Pac-Man + 4 Ghosts along corridor intersections
            for (const a of st.actors) {
                a.x += a.vx * dt;
                a.y += a.vy * dt;

                // Wrap around screen edges (arcade tunnel)
                if (a.x < -cell) a.x = w + cell;
                if (a.x > w + cell) a.x = -cell;
                if (a.y < -cell) a.y = h + cell;
                if (a.y > h + cell) a.y = -cell;

                // Turn at odd-grid intersections occasionally
                const colIdx = Math.round(a.x / cell);
                const rowIdx = Math.round(a.y / cell);
                if (colIdx % 2 === 1 && rowIdx % 2 === 1) {
                    const snapX = colIdx * cell;
                    const snapY = rowIdx * cell;
                    if (Math.hypot(a.x - snapX, a.y - snapY) < 2.2 && Math.random() < 0.08) {
                        a.x = snapX;
                        a.y = snapY;
                        const speed = Math.hypot(a.vx, a.vy) || 95;
                        const dirs = [
                            { vx: speed, vy: 0 },
                            { vx: -speed, vy: 0 },
                            { vx: 0, vy: speed },
                            { vx: 0, vy: -speed }
                        ];
                        // If mouse is active, Pac-Man steers toward cursor
                        if (a.type === 'pacman' && this.mouse.active) {
                            const dx = this.mouse.x - a.x;
                            const dy = this.mouse.y - a.y;
                            if (Math.abs(dx) > Math.abs(dy)) {
                                a.vx = Math.sign(dx) * speed;
                                a.vy = 0;
                            } else {
                                a.vx = 0;
                                a.vy = Math.sign(dy) * speed;
                            }
                        } else {
                            const pick = dirs[Math.floor(Math.random() * dirs.length)];
                            a.vx = pick.vx;
                            a.vy = pick.vy;
                        }
                    }
                }

                if (a.type === 'pacman') {
                    // Eat nearby pellets
                    for (const p of st.pellets) {
                        if (p.eatenTimer <= 0 && Math.hypot(p.x - a.x, p.y - a.y) < 12) {
                            p.eatenTimer = 8; // respawns after 8s
                        }
                    }

                    const angle = Math.atan2(a.vy, a.vx);
                    const chomp = 0.05 + Math.abs(Math.sin(this.time * 11)) * 0.28;
                    ctx.fillStyle = '#ffff00';
                    ctx.globalAlpha = 0.85;
                    ctx.beginPath();
                    ctx.moveTo(a.x, a.y);
                    ctx.arc(a.x, a.y, 13, angle + chomp * Math.PI, angle + (2 - chomp) * Math.PI);
                    ctx.closePath();
                    ctx.fill();
                } else {
                    // Scared blue mode when close to user's mouse cursor!
                    let scared = false;
                    if (this.mouse.active && Math.hypot(a.x - this.mouse.x, a.y - this.mouse.y) < 150) {
                        scared = true;
                    }
                    this._drawGhost(ctx, a.x, a.y, 13, scared ? '#2121ff' : a.color, a.vx, a.vy, scared);
                }
            }

            ctx.restore();
        }

        _drawGhost(ctx, x, y, r, color, vx, vy, scared) {
            ctx.save();
            ctx.translate(x, y);
            ctx.fillStyle = color;
            ctx.globalAlpha = 0.82;

            ctx.beginPath();
            ctx.arc(0, -2, r, Math.PI, 0, false);
            ctx.lineTo(r, r);
            // Wavy skirt bottom
            const waves = 3;
            const step = (r * 2) / waves;
            for (let i = 0; i < waves; i++) {
                const wx = r - i * step;
                ctx.quadraticCurveTo(wx - step * 0.25, r - 4, wx - step * 0.5, r);
                ctx.quadraticCurveTo(wx - step * 0.75, r + 3, wx - step, r);
            }
            ctx.lineTo(-r, -2);
            ctx.closePath();
            ctx.fill();

            // Eyes
            if (scared) {
                ctx.fillStyle = '#ffb8ae';
                ctx.fillRect(-5, -4, 3, 3);
                ctx.fillRect(2, -4, 3, 3);
            } else {
                const ex = Math.sign(vx) * 2;
                const ey = Math.sign(vy) * 2;
                ctx.fillStyle = '#ffffff';
                ctx.beginPath();
                ctx.arc(-4 + ex * 0.5, -3 + ey * 0.5, 3.5, 0, Math.PI * 2);
                ctx.arc(4 + ex * 0.5, -3 + ey * 0.5, 3.5, 0, Math.PI * 2);
                ctx.fill();

                ctx.fillStyle = '#0000aa';
                ctx.beginPath();
                ctx.arc(-4 + ex, -3 + ey, 1.8, 0, Math.PI * 2);
                ctx.arc(4 + ex, -3 + ey, 1.8, 0, Math.PI * 2);
                ctx.fill();
            }
            ctx.restore();
        }

        // =========================================================================
        // 9. PIP-BOY 3000 / VAULT-TEC (Fallout Phosphor Green CRT & V.A.T.S. HUD)
        // =========================================================================
        _renderPipBoy(ctx, w, h, dt) {
            ctx.clearRect(0, 0, w, h);
            const st = this.state;

            ctx.save();
            const green = '#1afc40';
            const greenDim = 'rgba(26, 252, 64, 0.4)';
            const amber = '#ffbf00';

            // 1. Subtle Phosphor Scan Grid
            ctx.fillStyle = green;
            ctx.globalAlpha = 0.04;
            const step = 42;
            for (let x = 20; x < w; x += step) {
                for (let y = 20; y < h; y += step) {
                    ctx.fillRect(x, y, 1.5, 1.5);
                }
            }

            // 2. CRT Curved Corner Bezel Brackets
            ctx.strokeStyle = green;
            ctx.globalAlpha = 0.35;
            ctx.lineWidth = 1.6;
            const bz = 32;
            const m = 18;
            // Top-left
            ctx.beginPath();
            ctx.moveTo(m, m + bz); ctx.arcTo(m, m, m + bz, m, 12); ctx.lineTo(m + bz, m);
            // Top-right
            ctx.moveTo(w - m - bz, m); ctx.arcTo(w - m, m, w - m, m + bz, 12); ctx.lineTo(w - m, m + bz);
            // Bottom-left
            ctx.moveTo(m, h - m - bz); ctx.arcTo(m, h - m, m + bz, h - m, 12); ctx.lineTo(m + bz, h - m);
            // Bottom-right
            ctx.moveTo(w - m - bz, h - m); ctx.arcTo(w - m, h - m, w - m, h - m - bz, 12); ctx.lineTo(w - m, h - m - bz);
            ctx.stroke();

            // 3. RobCo / Vault-Tec Header Readouts
            ctx.font = '11px monospace';
            ctx.fillStyle = green;
            ctx.globalAlpha = 0.75;
            ctx.fillText('[ AMIKO-TEC OS V1.04 ]', m + 14, m + 18);
            ctx.fillStyle = greenDim;
            ctx.fillText('HP 185/185  |  AP 90/90', m + 180, m + 18);

            ctx.fillStyle = amber;
            ctx.globalAlpha = 0.85;
            ctx.fillText('RADS: ☢ 0.04 RAD/S', w - m - 240, m + 18);
            ctx.fillStyle = green;
            ctx.fillText('[ STIMPAK: x8 ]', w - m - 95, m + 18);

            // Header separator bar
            ctx.strokeStyle = green;
            ctx.globalAlpha = 0.3;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(m + 10, m + 28);
            ctx.lineTo(w - m - 10, m + 28);
            ctx.stroke();

            // 4. Interactive Symmetric V.A.T.S. Reticle (No text, symmetric 4-corner brackets + leader line)
            if (this.mouse.active) {
                const mx = this.mouse.x;
                const my = this.mouse.y;
                const box = 22;
                const cLen = 7;

                // Target bracket surrounding cursor (symmetric 4 corners)
                ctx.strokeStyle = green;
                ctx.shadowColor = green;
                ctx.shadowBlur = 6;
                ctx.globalAlpha = 0.85;
                ctx.lineWidth = 1.6;

                ctx.beginPath();
                // Top-left
                ctx.moveTo(mx - box, my - box + cLen); ctx.lineTo(mx - box, my - box); ctx.lineTo(mx - box + cLen, my - box);
                // Top-right
                ctx.moveTo(mx + box - cLen, my - box); ctx.lineTo(mx + box, my - box); ctx.lineTo(mx + box, my - box + cLen);
                // Bottom-left
                ctx.moveTo(mx - box, my + box - cLen); ctx.lineTo(mx - box, my + box); ctx.lineTo(mx - box + cLen, my + box);
                // Bottom-right
                ctx.moveTo(mx + box - cLen, my + box); ctx.lineTo(mx + box, my + box); ctx.lineTo(mx + box, my + box - cLen);
                ctx.stroke();

                // Center target crosshair
                ctx.beginPath();
                ctx.moveTo(mx - 6, my); ctx.lineTo(mx + 6, my);
                ctx.moveTo(mx, my - 6); ctx.lineTo(mx, my + 6);
                ctx.stroke();
                ctx.shadowBlur = 0;

                // Dotted leader line to bottom telemetry
                ctx.strokeStyle = greenDim;
                ctx.setLineDash([4, 4]);
                ctx.beginPath();
                ctx.moveTo(mx, my + box);
                ctx.lineTo(mx, h - 52);
                ctx.stroke();
                ctx.setLineDash([]);
            }

            // 5. Bottom Geiger Counter / Vitals Waveform Bar
            const barY = h - 38;
            ctx.strokeStyle = green;
            ctx.globalAlpha = 0.25;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(m + 10, barY - 14);
            ctx.lineTo(w - m - 10, barY - 14);
            ctx.stroke();

            // Radiation ECG pulse wave
            st.pulsePhase = (st.pulsePhase + dt * 4.5) % (Math.PI * 2);
            ctx.strokeStyle = green;
            ctx.globalAlpha = 0.65;
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            for (let x = m + 14; x < w - m - 14; x += 5) {
                const spike = Math.sin(x * 0.04 - st.pulsePhase) * 6;
                const geigerTick = Math.sin(x * 0.18 + this.time * 7) > 0.92 ? 14 : 0;
                const y = barY + spike + geigerTick;
                if (x === m + 14) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.stroke();

            ctx.restore();
        }

        // =========================================================================
        // 10. SPACE INVADERS (1978 8-Bit Arcade Fleet, Mystery Saucer & Cannon)
        // =========================================================================
        _renderSpaceInvaders(ctx, w, h, dt) {
            ctx.clearRect(0, 0, w, h);
            const st = this.state;
            if (!st.invaders) return;

            ctx.save();

            // 1. Update Fleet Movement
            st.fleetX += st.fleetDir * st.fleetSpeed * dt;
            st.frameTimer += dt;
            if (st.frameTimer > 0.42) {
                st.frame = 1 - st.frame;
                st.frameTimer = 0;
            }

            // Fleet wall bounce & descent
            if (st.fleetDir === 1 && st.fleetX + st.totalFleetW >= w - 30) {
                st.fleetDir = -1;
                st.fleetY += 16;
            } else if (st.fleetDir === -1 && st.fleetX <= 30) {
                st.fleetDir = 1;
                st.fleetY += 16;
            }
            if (st.fleetY > h - 140) {
                st.fleetY = 70;
            }

            // 2. Draw 8-Bit Space Invaders
            for (const inv of st.invaders) {
                const ix = st.fleetX + inv.relX;
                const iy = st.fleetY + inv.relY;
                const color = inv.type === 'squid' ? '#55ff55' : (inv.type === 'crab' ? '#ff3366' : '#ffff44');
                this._drawPixelAlien(ctx, ix, iy, inv.type, st.frame, color);
            }

            // 3. Mystery Flying Saucer UFO
            const ufo = st.ufo;
            ufo.timer -= dt;
            if (ufo.timer <= 0 && !ufo.active) {
                ufo.active = true;
                ufo.x = w + 40;
                ufo.timer = 12 + Math.random() * 8;
            }
            if (ufo.active) {
                ufo.x -= 140 * dt;
                this._drawPixelSaucer(ctx, ufo.x, 34, '#ff2255');
                ctx.font = 'bold 10px monospace';
                ctx.fillStyle = '#ff2255';
                ctx.globalAlpha = 0.85;
                ctx.fillText('? 300 PTS ?', ufo.x - 20, 24);
                if (ufo.x < -60) ufo.active = false;
            }

            // 4. Defensive Bunkers (Shields)
            const bunkerY = h - 90;
            const bunkerSpacing = w / 4;
            for (let b = 1; b <= 3; b++) {
                this._drawPixelBunker(ctx, b * bunkerSpacing - 22, bunkerY, '#55ff55');
            }

            // 5. Player Cannon at bottom
            const targetX = this.mouse.active ? this.mouse.x : w * 0.5;
            st.cannonX = (st.cannonX || w * 0.5) + (targetX - (st.cannonX || w * 0.5)) * Math.min(1, dt * 8);
            this._drawPixelCannon(ctx, st.cannonX, h - 45, '#55ff55');

            ctx.restore();
        }

        _drawPixelAlien(ctx, x, y, type, frame, color) {
            ctx.save();
            ctx.fillStyle = color;
            ctx.globalAlpha = 0.78;
            const p = 2.4; // pixel block size

            if (type === 'squid') {
                const r1 = [
                    '00011000',
                    '00111100',
                    '01111110',
                    '11011011',
                    '11111111',
                    '00100100',
                    '01011010',
                    '10100101'
                ];
                const r2 = [
                    '00011000',
                    '00111100',
                    '01111110',
                    '11011011',
                    '11111111',
                    '01011010',
                    '10000001',
                    '01000010'
                ];
                const grid = frame === 0 ? r1 : r2;
                for (let r = 0; r < 8; r++) {
                    for (let c = 0; c < 8; c++) {
                        if (grid[r][c] === '1') ctx.fillRect(x + c * p, y + r * p, p, p);
                    }
                }
            } else if (type === 'crab') {
                const r1 = [
                    '00100000100',
                    '00010001000',
                    '00111111100',
                    '01101110110',
                    '11111111111',
                    '10111111101',
                    '10100000101',
                    '00011011000'
                ];
                const r2 = [
                    '00100000100',
                    '10010001001',
                    '10111111101',
                    '11101110111',
                    '01111111110',
                    '00111111100',
                    '00100000100',
                    '01000000010'
                ];
                const grid = frame === 0 ? r1 : r2;
                for (let r = 0; r < 8; r++) {
                    for (let c = 0; c < 11; c++) {
                        if (grid[r][c] === '1') ctx.fillRect(x + c * p, y + r * p, p, p);
                    }
                }
            } else {
                const r1 = [
                    '000011110000',
                    '011111111110',
                    '111111111111',
                    '111001100111',
                    '111111111111',
                    '000110011000',
                    '001101101100',
                    '110000000011'
                ];
                const r2 = [
                    '000011110000',
                    '011111111110',
                    '111111111111',
                    '111001100111',
                    '111111111111',
                    '001100001100',
                    '011001100110',
                    '001100001100'
                ];
                const grid = frame === 0 ? r1 : r2;
                for (let r = 0; r < 8; r++) {
                    for (let c = 0; c < 12; c++) {
                        if (grid[r][c] === '1') ctx.fillRect(x + c * p, y + r * p, p, p);
                    }
                }
            }
            ctx.restore();
        }

        _drawPixelSaucer(ctx, x, y, color) {
            ctx.save();
            ctx.fillStyle = color;
            ctx.globalAlpha = 0.9;
            const p = 2.4;
            const saucer = [
                '0000011111100000',
                '0001111111111000',
                '0011111111111100',
                '0110110110110110',
                '1111111111111111',
                '0001110000111000',
                '0000100000010000'
            ];
            for (let r = 0; r < 7; r++) {
                for (let c = 0; c < 16; c++) {
                    if (saucer[r][c] === '1') ctx.fillRect(x + c * p, y + r * p, p, p);
                }
            }
            ctx.restore();
        }

        _drawPixelBunker(ctx, x, y, color) {
            ctx.save();
            ctx.fillStyle = color;
            ctx.globalAlpha = 0.45;
            ctx.fillRect(x, y + 8, 44, 20);
            ctx.fillRect(x + 6, y, 32, 10);
            ctx.clearRect(x + 14, y + 16, 16, 14);
            ctx.restore();
        }

        _drawPixelCannon(ctx, x, y, color) {
            ctx.save();
            ctx.fillStyle = color;
            ctx.globalAlpha = 0.9;
            ctx.fillRect(x - 14, y + 6, 28, 10);
            ctx.fillRect(x - 8, y + 2, 16, 6);
            ctx.fillRect(x - 2, y - 6, 4, 8);
            ctx.restore();
        }

        // =========================================================================
        // 11. HIGH CONTRAST (WCAG AAA Precision Drafting Grid & Hairline Cursor HUD)
        // =========================================================================
        _renderHighContrast(ctx, w, h, dt) {
            ctx.clearRect(0, 0, w, h);

            ctx.save();
            const white = '#ffffff';
            const orange = '#ff8800';

            // 1. Precision 1px Orthogonal Hairline Grid
            ctx.strokeStyle = white;
            ctx.globalAlpha = 0.08;
            ctx.lineWidth = 1;
            const step = 60;
            for (let x = 0; x < w; x += step) {
                ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
            }
            for (let y = 0; y < h; y += step) {
                ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
            }

            // Precision ruler tick marks on screen boundaries
            ctx.globalAlpha = 0.35;
            for (let x = 0; x < w; x += 10) {
                const len = x % 50 === 0 ? 8 : 4;
                ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, len); ctx.stroke();
                ctx.beginPath(); ctx.moveTo(x, h); ctx.lineTo(x, h - len); ctx.stroke();
            }
            for (let y = 0; y < h; y += 10) {
                const len = y % 50 === 0 ? 8 : 4;
                ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(len, y); ctx.stroke();
                ctx.beginPath(); ctx.moveTo(w, y); ctx.lineTo(w - len, y); ctx.stroke();
            }

            // 2. Precision Hairline Crosshair tracking cursor
            if (this.mouse.active) {
                const mx = this.mouse.x;
                const my = this.mouse.y;

                ctx.strokeStyle = white;
                ctx.lineWidth = 1;
                ctx.globalAlpha = 0.22;
                ctx.beginPath();
                ctx.moveTo(0, my); ctx.lineTo(w, my);
                ctx.moveTo(mx, 0); ctx.lineTo(mx, h);
                ctx.stroke();

                // Coordinate HUD Badge next to cursor
                ctx.fillStyle = '#000000';
                ctx.fillRect(mx + 12, my + 12, 160, 24);
                ctx.strokeStyle = orange;
                ctx.globalAlpha = 0.85;
                ctx.strokeRect(mx + 12, my + 12, 160, 24);

                ctx.font = 'bold 10px monospace';
                ctx.fillStyle = orange;
                ctx.fillText(`X:${String(Math.floor(mx)).padStart(4,'0')}  Y:${String(Math.floor(my)).padStart(4,'0')}`, mx + 18, my + 28);
            }

            ctx.restore();
        }

        // =========================================================================
        // 12. NORDIC SLATE (Clean, Sober Workplace Topographic Waves)
        // =========================================================================
        _renderNordic(ctx, w, h, dt) {
            ctx.clearRect(0, 0, w, h);
            const st = this.state;

            ctx.save();
            const slateBlue = '#2563eb';
            const graphite = '#0f172a';

            // 1. Discreet Architectural Drafting Dot Grid
            ctx.fillStyle = graphite;
            ctx.globalAlpha = 0.04;
            const step = 36;
            for (let x = 18; x < w; x += step) {
                for (let y = 18; y < h; y += step) {
                    ctx.fillRect(x, y, 1.2, 1.2);
                }
            }

            // 2. Harmonic Topographic Sinusoidal Waves
            st.offset = (st.offset || 0) + dt * 0.18;
            const waveCount = 5;
            const baseY = h * 0.52;

            ctx.lineWidth = 1.2;
            for (let i = 0; i < waveCount; i++) {
                const freq = 0.0032 + i * 0.0008;
                const speed = 0.45 + i * 0.12;
                const amp = 32 + i * 16;
                const yOffset = (i - 2) * 44;

                ctx.strokeStyle = i % 2 === 0 ? slateBlue : graphite;
                ctx.globalAlpha = 0.06 + (i / waveCount) * 0.12;

                ctx.beginPath();
                for (let x = 0; x <= w; x += 12) {
                    const y = baseY + yOffset + Math.sin(x * freq + st.offset * speed) * amp +
                              Math.cos(x * 0.0018 - st.offset * 0.3) * (amp * 0.5);
                    if (x === 0) ctx.moveTo(x, y);
                    else ctx.lineTo(x, y);
                }
                ctx.stroke();
            }

            ctx.restore();
        }
    }

    // Boot engine once DOM is ready
    document.addEventListener('DOMContentLoaded', () => {
        window.amikoBackground = new AmikoBackgroundEngine();
    });
})();

