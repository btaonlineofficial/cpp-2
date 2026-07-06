"""Rebuild virtual_tour.html using the Light Theme template and Pannellum CDN."""
import os

# ── Paths ──
BASE          = os.path.dirname(os.path.abspath(__file__))
B64_FILE      = os.path.join(BASE, '360_resized_b64.txt')
OUT_HTML      = os.path.join(BASE, '..', 'new_templates', 'virtual_tour.html')

# Load the base64 data of the new compressed image
with open(B64_FILE) as f:
    b64 = f.read().strip()

data_url = 'data:image/jpeg;base64,' + b64

# PART 1: Everything before the CAMPUS_PHOTO variable
PART1 = r'''<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>360° Virtual Tour — Contai Polytechnic</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap"
        rel="stylesheet">
    <style>
        *,
        *::before,
        *::after {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        html {
            scroll-behavior: smooth;
        }

        body {
            font-family: 'Inter', sans-serif;
            background: #f1f5f9;
            color: #0f172a;
            min-height: 100vh;
            padding-top: 72px;
        }

        ::-webkit-scrollbar {
            width: 6px;
        }

        ::-webkit-scrollbar-track {
            background: #f1f5f9;
        }

        ::-webkit-scrollbar-thumb {
            background: #cbd5e1;
            border-radius: 3px;
        }

        /* HEADER */
        .header {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            z-index: 1000;
            height: 72px;
            padding: 0 48px;
            background: rgba(255, 255, 255, 0.96);
            backdrop-filter: blur(20px) saturate(180%);
            border-bottom: 1px solid #e2e8f0;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
            display: flex;
            align-items: center;
        }

        .header-content {
            max-width: 1280px;
            margin: 0 auto;
            width: 100%;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .logo-section {
            display: flex;
            align-items: center;
            gap: 12px;
            text-decoration: none;
        }

        .logo-img {
            width: 44px;
            height: 44px;
            object-fit: contain;
        }

        .logo-section h2 {
            font-size: 18px;
            font-weight: 800;
            color: #0f172a;
            letter-spacing: -0.3px;
            margin: 0;
        }

        .logo-section h2 span {
            color: #1a56db;
        }

        .nav-center {
            display: flex;
            align-items: center;
            gap: 4px;
        }

        .nav-link {
            padding: 7px 14px;
            border-radius: 10px;
            color: #475569;
            font-size: 13px;
            font-weight: 600;
            text-decoration: none;
            border: 1px solid #e2e8f0;
            background: #f8fafc;
            transition: all 0.2s;
        }

        .nav-link:hover,
        .nav-link.active {
            color: #1a56db;
            background: #eff4ff;
            border-color: #c7d7fd;
        }

        .dept-dropdown-wrap {
            position: relative;
        }

        .dept-trigger {
            padding: 7px 14px;
            border-radius: 10px;
            color: #475569;
            font-size: 13px;
            font-weight: 600;
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            cursor: pointer;
            font-family: inherit;
            display: flex;
            align-items: center;
            gap: 5px;
            transition: all 0.2s;
        }

        .dept-trigger:hover {
            color: #1a56db;
            background: #eff4ff;
            border-color: #c7d7fd;
        }

        .dept-trigger svg {
            transition: transform 0.22s;
        }

        .dept-dropdown-wrap:hover .dept-trigger svg {
            transform: rotate(180deg);
        }

        .dept-dropdown {
            position: absolute;
            top: calc(100% + 10px);
            left: 50%;
            transform: translateX(-50%) translateY(-6px);
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.2s, transform 0.2s;
            z-index: 999;
            min-width: 210px;
        }

        .dept-dropdown-wrap:hover .dept-dropdown {
            opacity: 1;
            pointer-events: all;
            transform: translateX(-50%) translateY(0);
        }

        .dept-dropdown-inner {
            background: white;
            border: 1px solid #e2e8f0;
            border-radius: 14px;
            padding: 8px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.12);
            display: flex;
            flex-direction: column;
            gap: 2px;
            position: relative;
        }

        .dept-dropdown-inner::before {
            content: '';
            position: absolute;
            top: -5px;
            left: 50%;
            transform: translateX(-50%);
            width: 10px;
            height: 10px;
            background: white;
            border-left: 1px solid #e2e8f0;
            border-top: 1px solid #e2e8f0;
            rotate: 45deg;
        }

        .dd-item {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 9px 10px;
            border-radius: 8px;
            text-decoration: none;
            color: #475569;
            transition: all 0.15s;
        }

        .dd-item:hover {
            background: #eff4ff;
            color: #1a56db;
        }

        .dd-icon {
            width: 28px;
            height: 28px;
            border-radius: 7px;
            border: 1px solid #e2e8f0;
            flex-shrink: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            background: #f8fafc;
        }

        .dd-item strong {
            display: block;
            font-size: 12px;
            font-weight: 700;
            color: #0f172a;
        }

        .dd-item small {
            display: block;
            font-size: 9px;
            color: #94a3b8;
        }

        .header-right {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .btn-outline {
            padding: 8px 20px;
            border-radius: 10px;
            border: 1px solid #c7d7fd;
            background: #eff4ff;
            color: #1a56db;
            font-size: 13px;
            font-weight: 700;
            text-decoration: none;
            transition: all 0.2s;
        }

        .btn-outline:hover {
            background: #1a56db;
            color: white;
            border-color: #1a56db;
        }

        /* Virtual Tour Specific CSS */
        .page-hero {
            padding: 120px 24px 40px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-direction: column;
            text-align: center;
            position: relative;
            overflow: hidden
        }

        .page-hero::before {
            content: '';
            position: absolute;
            inset: 0;
            background: linear-gradient(rgba(99, 102, 241, .04) 1px, transparent 1px), linear-gradient(90deg, rgba(99, 102, 241, .04) 1px, transparent 1px);
            background-size: 60px 60px;
            mask-image: radial-gradient(ellipse 80% 100% at 50% 0%, black 40%, transparent 100%)
        }

        .orb-a {
            position: absolute;
            border-radius: 50%;
            filter: blur(90px);
            pointer-events: none;
            width: 400px;
            height: 400px;
            background: #4f46e5;
            opacity: .2;
            top: -100px;
            left: 50%;
            transform: translateX(-50%)
        }

        .page-hero-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(99, 102, 241, .10);
            border: 1px solid rgba(99, 102, 241, .25);
            color: #6366f1;
            font-size: 11px;
            font-weight: 800;
            letter-spacing: 2px;
            text-transform: uppercase;
            padding: 5px 18px;
            border-radius: 50px;
            margin-bottom: 20px;
            position: relative
        }

        .live-dot {
            width: 7px;
            height: 7px;
            background: #6366f1;
            border-radius: 50%;
            box-shadow: 0 0 8px #6366f1;
            animation: pd 1.8s ease-in-out infinite
        }

        @keyframes pd {
            0%, 100% { opacity: 1; transform: scale(1) }
            50% { opacity: .5; transform: scale(.8) }
        }

        .page-hero h1 {
            font-size: clamp(28px, 5vw, 54px);
            font-weight: 900;
            color: #0f172a;
            letter-spacing: -1.5px;
            line-height: 1.05;
            margin-bottom: 14px;
            position: relative
        }

        .page-hero h1 em {
            font-style: normal;
            color: transparent;
            background: linear-gradient(135deg, #1a56db, #7c3aed, #38bdf8);
            -webkit-background-clip: text;
            background-clip: text
        }

        .page-hero p {
            font-size: 15px;
            color: #64748b;
            max-width: 480px;
            line-height: 1.6;
            position: relative
        }

        .tour-section {
            padding: 0 24px 80px;
            max-width: 1400px;
            margin: 0 auto
        }

        .viewer-wrapper {
            position: relative;
            border-radius: 24px;
            overflow: hidden;
            border: 1px solid #e2e8f0;
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.1);
            background: #f8fafc;
            aspect-ratio: 16/9;
            min-height: 380px
        }

        #panorama {
            width: 100%;
            height: 100%;
            border-radius: 24px;
            overflow: hidden
        }

        .scenes-strip {
            margin-top: 20px;
            display: flex;
            gap: 14px;
            overflow-x: auto;
            padding-bottom: 6px;
            scrollbar-width: thin;
            scrollbar-color: #cbd5e1 transparent
        }

        .scenes-strip::-webkit-scrollbar { height: 4px }
        .scenes-strip::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 2px }

        .scene-thumb {
            flex-shrink: 0;
            width: 160px;
            border-radius: 14px;
            overflow: hidden;
            border: 1px solid #e2e8f0;
            cursor: pointer;
            transition: all .25s;
            background: white;
            position: relative
        }

        .scene-thumb:hover {
            border-color: #c7d7fd;
            transform: translateY(-3px);
            box-shadow: 0 8px 24px rgba(26, 86, 219, 0.08);
        }

        .scene-thumb.active {
            border-color: #1a56db;
            box-shadow: 0 0 0 2px #eff4ff;
        }

        .thumb-img {
            width: 100%;
            height: 90px;
            object-fit: cover;
            display: block;
            background: #f1f5f9;
        }

        .thumb-info { padding: 10px 12px }
        .thumb-name { font-size: 12px; font-weight: 700; color: #0f172a; margin-bottom: 2px }
        .thumb-sub { font-size: 10px; color: #64748b }

        .scene-thumb.active .thumb-name { color: #1a56db }
        .active-bar {
            position: absolute; bottom: 0; left: 0; right: 0; height: 2px;
            background: #1a56db; opacity: 0; transition: opacity .2s
        }
        .scene-thumb.active .active-bar { opacity: 1 }

        .info-grid {
            margin-top: 40px;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px
        }

        .info-card {
            background: white;
            border: 1px solid #e2e8f0;
            border-radius: 16px;
            padding: 22px;
            transition: all .25s
        }

        .info-card:hover {
            border-color: #c7d7fd;
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(26, 86, 219, 0.08);
        }

        .ic-icon { font-size: 28px; margin-bottom: 10px }
        .ic-title { font-size: 13px; font-weight: 700; color: #0f172a; margin-bottom: 4px }
        .ic-desc { font-size: 12px; color: #475569; line-height: 1.5 }

        .custom-hotspot {
            width: 34px;
            height: 34px;
            background: rgba(239, 68, 68, 0.85);
            border-radius: 50%;
            border: 3px solid white;
            box-shadow: 0 0 15px rgba(239, 68, 68, 0.8);
            cursor: pointer;
            transition: transform 0.2s;
            animation: pulse 2s infinite;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .custom-hotspot.back::after {
            transform: rotate(180deg);
        }
        .custom-hotspot::after {
            content: '↑';
            color: white;
            font-size: 20px;
            font-weight: bold;
        }
        .custom-hotspot:hover {
            transform: scale(1.2);
            background: rgba(220, 38, 38, 1);
        }
        .custom-hotspot span {
            visibility: hidden;
            position: absolute;
            top: -40px;
            left: 50%;
            transform: translateX(-50%);
            background: #0f172a;
            color: #fff;
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 13px;
            white-space: nowrap;
            font-family: 'Inter', sans-serif;
            font-weight: 600;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }
        .custom-hotspot span::after {
            content: '';
            position: absolute;
            bottom: -5px;
            left: 50%;
            transform: translateX(-50%);
            border-width: 5px 5px 0;
            border-style: solid;
            border-color: #0f172a transparent transparent transparent;
        }
        .custom-hotspot:hover span {
            visibility: visible;
        }
        @keyframes pulse {
            0% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7); }
            70% { box-shadow: 0 0 0 20px rgba(239, 68, 68, 0); }
            100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
        }

        /* FOOTER */
        .footer {
            background: #0f172a;
            padding: 56px 48px 36px;
            margin-top: 60px;
        }

        .footer-content {
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            flex-wrap: wrap;
            gap: 60px;
            justify-content: space-between;
        }

        .footer-left {
            flex: 1;
            min-width: 280px;
            max-width: 380px;
        }

        .footer-logo {
            display: flex;
            align-items: center;
            gap: 14px;
            margin-bottom: 20px;
        }

        .footer-logo h2 {
            margin: 0;
            font-size: 20px;
            color: white;
            font-weight: 900;
        }

        .footer-logo img {
            width: 48px;
            height: 48px;
            object-fit: contain;
        }

        .footer-desc {
            color: #94a3b8;
            font-size: 13px;
            line-height: 1.7;
        }

        .footer-card {
            flex: 2;
            min-width: 560px;
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 20px;
            padding: 36px;
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 32px;
        }

        .footer-col h4 {
            color: white;
            font-size: 11px;
            font-weight: 800;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            margin: 0 0 18px;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .footer-links li a,
        .footer-links li {
            color: #64748b;
            text-decoration: none;
            font-size: 12px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
            transition: 0.2s;
            text-transform: uppercase;
        }

        .footer-bottom {
            max-width: 1200px;
            margin: 56px auto 0;
            padding-top: 28px;
            border-top: 1px solid rgba(255, 255, 255, 0.06);
            display: flex;
            justify-content: space-between;
            align-items: center;
            color: #475569;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 2px;
            text-transform: uppercase;
        }

        @media (max-width: 768px) {
            .footer-card { min-width: 100%; grid-template-columns: 1fr; }
            .header { padding: 0 16px; }
            .nav-center { display: none; }
        }
    </style>
    <!-- Pannellum 360° viewer -->
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/pannellum@2.5.6/build/pannellum.css">
    <script src="https://cdn.jsdelivr.net/npm/pannellum@2.5.6/build/pannellum.js"></script>
</head>

<body>

    <div class="header">
        <div class="header-content">
            <a href="/" class="logo-section">
                <img src="{{ url_for('static', filename='logo.png') }}" alt="Logo" class="logo-img">
                <h2>Contai <span>Poly</span>technic</h2>
            </a>

            <nav class="nav-center">
                <a href="/" class="nav-link">Home</a>
                <a href="/virtual-tour" class="nav-link active">Virtual Tour</a>
            </nav>

            <div class="header-right">
                <a href="/login" class="btn-outline">Admin</a>
            </div>
        </div>
    </div>

    <section class="page-hero">
        <div class="orb-a"></div>
        <div class="page-hero-badge"><span class="live-dot"></span>360° Virtual Campus Tour</div>
        <h1>Explore Our <em>Campus</em><br>in Full 360°</h1>
        <p>Drag to look around. Use scroll to zoom. Switch locations below.</p>
    </section>

    <div class="tour-section">
        <div class="viewer-wrapper">
            <div id="panorama"></div>
        </div>
        <div class="scenes-strip" id="scenes-strip"></div>
        <div class="info-grid">
            <div class="info-card">
                <div class="ic-icon">🖱️</div>
                <div class="ic-title">Drag to Explore</div>
                <div class="ic-desc">Click and drag in any direction to look around the 360° panorama.</div>
            </div>
            <div class="info-card">
                <div class="ic-icon">🔍</div>
                <div class="ic-title">Zoom In &amp; Out</div>
                <div class="ic-desc">Use the mouse wheel or pinch gesture to zoom.</div>
            </div>
            <div class="info-card">
                <div class="ic-icon">⟳</div>
                <div class="ic-title">Auto Rotate</div>
                <div class="ic-desc">The campus view slowly auto-rotates for a passive tour experience.</div>
            </div>
            <div class="info-card">
                <div class="ic-icon">📍</div>
                <div class="ic-title">Multiple Scenes</div>
                <div class="ic-desc">Click any thumbnail below to jump to that campus location.</div>
            </div>
        </div>
    </div>

    <script>
        const CAMPUS_PHOTO = "'''

# PART 2: Everything after the Base64 image string
PART2 = r'''";

        const HOSTEL_PHOTO = "{{ url_for('static', filename='Girls_hostel_.jpg.jpeg') }}";
        const NEW_BUILDING_PHOTO = "{{ url_for('static', filename='New_building_.jpg.jpeg') }}";
        const ENTRANCE_RECEPTION_PHOTO = "{{ url_for('static', filename='Entrance_reception_.jpg.jpeg') }}";
        const OLD_BUILDING_PHOTO = "{{ url_for('static', filename='Old_building_.jpg.jpeg') }}";

        const SCENES = [
            { id: 'campus', name: 'Campus 360°', icon: '🏫', sub: 'Main Gate', photo: true, src: CAMPUS_PHOTO },
            { id: 'hostel', name: 'Girls Hostel', icon: '🏢', sub: 'Hostel View', photo: true, src: HOSTEL_PHOTO },
            { id: 'new_building', name: 'New Building', icon: '🏛️', sub: 'New Academic Bldg', photo: true, src: NEW_BUILDING_PHOTO },
            { id: 'reception', name: 'Reception', icon: '🏢', sub: 'Entrance Reception', photo: true, src: ENTRANCE_RECEPTION_PHOTO },
            { id: 'old_building', name: 'Old Building', icon: '🏫', sub: 'Old Academic Bldg', photo: true, src: OLD_BUILDING_PHOTO }
        ];

        let viewer = null;
        let currentIdx = 0;

        function initViewer(src) {
            if (viewer) { viewer.destroy(); viewer = null; }
            
            let hotSpots = [];
            if (src === CAMPUS_PHOTO) {
                hotSpots.push({
                    pitch: -9,
                    yaw: -12,
                    type: "custom",
                    createTooltipFunc: function(hotSpotDiv, args) {
                        hotSpotDiv.classList.add('custom-hotspot');
                        var tooltip = document.createElement('span');
                        tooltip.innerHTML = args;
                        hotSpotDiv.appendChild(tooltip);
                        hotSpotDiv.onclick = function() {
                            switchScene( SCENES.findIndex(s => s.id === 'hostel') );
                        };
                    },
                    createTooltipArgs: "Go to Girls Hostel"
                });
            } else if (src === HOSTEL_PHOTO) {
                hotSpots.push({
                    pitch: -8,
                    yaw: 160,
                    type: "custom",
                    createTooltipFunc: function(hotSpotDiv, args) {
                        hotSpotDiv.classList.add('custom-hotspot', 'back');
                        var tooltip = document.createElement('span');
                        tooltip.innerHTML = args;
                        hotSpotDiv.appendChild(tooltip);
                        hotSpotDiv.onclick = function() {
                            switchScene( SCENES.findIndex(s => s.id === 'campus') );
                        };
                    },
                    createTooltipArgs: "Back to Main Gate"
                });
                hotSpots.push({
                    pitch: -5,
                    yaw: -20,
                    type: "custom",
                    createTooltipFunc: function(hotSpotDiv, args) {
                        hotSpotDiv.classList.add('custom-hotspot');
                        var tooltip = document.createElement('span');
                        tooltip.innerHTML = args;
                        hotSpotDiv.appendChild(tooltip);
                        hotSpotDiv.onclick = function() {
                            switchScene( SCENES.findIndex(s => s.id === 'new_building') );
                        };
                    },
                    createTooltipArgs: "Go to New Building"
                });
            } else if (src === NEW_BUILDING_PHOTO) {
                hotSpots.push({
                    pitch: -5,
                    yaw: 217,
                    type: "custom",
                    createTooltipFunc: function(hotSpotDiv, args) {
                        hotSpotDiv.classList.add('custom-hotspot', 'back');
                        var tooltip = document.createElement('span');
                        tooltip.innerHTML = args;
                        hotSpotDiv.appendChild(tooltip);
                        hotSpotDiv.onclick = function() {
                            switchScene( SCENES.findIndex(s => s.id === 'hostel') );
                        };
                    },
                    createTooltipArgs: "Back to Girls Hostel"
                });
                hotSpots.push({
                    pitch: -7,
                    yaw: 70,
                    type: "custom",
                    createTooltipFunc: function(hotSpotDiv, args) {
                        hotSpotDiv.classList.add('custom-hotspot');
                        var tooltip = document.createElement('span');
                        tooltip.innerHTML = args;
                        hotSpotDiv.appendChild(tooltip);
                        hotSpotDiv.onclick = function() {
                            switchScene( SCENES.findIndex(s => s.id === 'reception') );
                        };
                    },
                    createTooltipArgs: "Go to Reception"
                });
            } else if (src === ENTRANCE_RECEPTION_PHOTO) {
                hotSpots.push({
                    pitch: -7,
                    yaw: 290,
                    type: "custom",
                    createTooltipFunc: function(hotSpotDiv, args) {
                        hotSpotDiv.classList.add('custom-hotspot', 'back');
                        var tooltip = document.createElement('span');
                        tooltip.innerHTML = args;
                        hotSpotDiv.appendChild(tooltip);
                        hotSpotDiv.onclick = function() {
                            switchScene( SCENES.findIndex(s => s.id === 'new_building') );
                        };
                    },
                    createTooltipArgs: "Back to New Building"
                });
                hotSpots.push({
                    pitch: -7,
                    yaw: 65,
                    type: "custom",
                    createTooltipFunc: function(hotSpotDiv, args) {
                        hotSpotDiv.classList.add('custom-hotspot');
                        var tooltip = document.createElement('span');
                        tooltip.innerHTML = args;
                        hotSpotDiv.appendChild(tooltip);
                        hotSpotDiv.onclick = function() {
                            switchScene( SCENES.findIndex(s => s.id === 'old_building') );
                        };
                    },
                    createTooltipArgs: "Go to Old Building"
                });
            } else if (src === OLD_BUILDING_PHOTO) {
                hotSpots.push({
                    pitch: -7,
                    yaw: 110,
                    type: "custom",
                    createTooltipFunc: function(hotSpotDiv, args) {
                        hotSpotDiv.classList.add('custom-hotspot', 'back');
                        var tooltip = document.createElement('span');
                        tooltip.innerHTML = args;
                        hotSpotDiv.appendChild(tooltip);
                        hotSpotDiv.onclick = function() {
                            switchScene( SCENES.findIndex(s => s.id === 'reception') );
                        };
                    },
                    createTooltipArgs: "Back to Reception"
                });
            }

            viewer = pannellum.viewer('panorama', {
                type: 'equirectangular',
                panorama: src,
                autoLoad: true,
                autoRotate: -2,
                autoRotateInactivityDelay: 2000,
                compass: true,
                showControls: true,
                mouseZoom: true,
                hfov: 100,
                minHfov: 40,
                maxHfov: 120,
                hotSpots: hotSpots,
                strings: { loadButtonLabel: 'Click to Load 360° View' }
            });
        }

        function makePlaceholder(sc) {
            const c = document.createElement('canvas');
            c.width = 320; c.height = 180;
            const ctx = c.getContext('2d');
            const g = ctx.createLinearGradient(0, 0, 0, 180);
            const col = sc.color || '#1a56db';
            g.addColorStop(0, col + '40'); g.addColorStop(1, '#f8fafc');
            ctx.fillStyle = g; ctx.fillRect(0, 0, 320, 180);
            ctx.font = '52px serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            ctx.fillText(sc.icon || sc.thumb, 160, 90);
            return c.toDataURL();
        }

        function buildThumbs() {
            const strip = document.getElementById('scenes-strip');
            strip.innerHTML = '';
            SCENES.forEach((sc, i) => {
                const el = document.createElement('div');
                el.className = 'scene-thumb' + (i === 0 ? ' active' : '');
                const imgSrc = sc.photo ? sc.src : makePlaceholder(sc);
                el.innerHTML = `<img class="thumb-img" src="${imgSrc}" alt="${sc.name}"><div class="thumb-info"><div class="thumb-name">${sc.icon} ${sc.name}</div><div class="thumb-sub">${sc.sub}</div></div><div class="active-bar"></div>`;
                el.addEventListener('click', () => switchScene(i));
                strip.appendChild(el);
            });
        }

        function switchScene(idx) {
            if (idx === currentIdx) return;
            currentIdx = idx;
            document.querySelectorAll('.scene-thumb').forEach((el, i) => el.classList.toggle('active', i === idx));
            const sc = SCENES[idx];
            if (sc.photo) {
                initViewer(sc.src);
            } else {
                const c = document.createElement('canvas');
                c.width = 2048; c.height = 1024;
                const ctx = c.getContext('2d');
                const g = ctx.createLinearGradient(0, 0, 0, 1024);
                g.addColorStop(0, sc.color + '80'); g.addColorStop(1, '#f1f5f9');
                ctx.fillStyle = g; ctx.fillRect(0, 0, 2048, 1024);
                ctx.font = '180px serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
                ctx.fillText(sc.icon, 1024, 512);
                initViewer(c.toDataURL());
            }
        }

        buildThumbs();
        initViewer(CAMPUS_PHOTO);
    </script>

    <div class="footer">
        <div class="footer-content">
            <div class="footer-left">
                <div class="footer-logo">
                    <img src="{{ url_for('static', filename='logo.png') }}" alt="Logo">
                    <h2>Contai<br>Polytechnic</h2>
                </div>
                <p class="footer-desc">Leading excellence in technical education since its inception.</p>
            </div>
            <div class="footer-card">
                <div class="footer-col">
                    <h4>Quick Links</h4>
                    <ul class="footer-links">
                        <li><a href="/">🏠 Home</a></li>
                        <li><a href="/notice">📋 Notice Board</a></li>
                    </ul>
                </div>
            </div>
        </div>
        <div class="footer-bottom">
            <span>© 2025 Contai Polytechnic — All Rights Reserved</span>
        </div>
    </div>
</body>
</html>'''

html = PART1 + data_url + PART2

with open(OUT_HTML, 'w', encoding='utf-8') as f:
    f.write(html)

print(f'DONE! Generated Light Theme Tour: {round(len(html)/1024/1024, 2)} MB')
