const express = require('express');
const cors = require('cors');
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode');
const fs = require('fs');
const path = require('path');

const app = express();
app.use(cors());
app.use(express.json());

let botStatus = 'disconnected';
let qrCodeData = null;
let client = null;
let initRetryCount = 0;
const MAX_RETRIES = 5;        // increased from 3
let initTimer = null;          // moved to outer scope so /restart can clear it
let isShuttingDown = false;

let stats = {
    pending: 0,
    sent: 0,
    failed: 0
};

// Find best available Chrome executable
function getChromePath() {
    const paths = [
        'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
        process.env.LOCALAPPDATA + '\\Google\\Chrome\\Application\\chrome.exe',
        'C:\\Program Files\\Chromium\\Application\\chromium.exe',
    ];
    for (const p of paths) {
        try { if (fs.existsSync(p)) return p; } catch(e) {}
    }
    return null; // fallback to bundled
}

// Clean up stale session lock files that can prevent re-init
function cleanStaleLocks() {
    const sessionDir = path.join(__dirname, 'wa_session');
    try {
        if (fs.existsSync(sessionDir)) {
            const walk = (dir) => {
                const entries = fs.readdirSync(dir, { withFileTypes: true });
                for (const entry of entries) {
                    const full = path.join(dir, entry.name);
                    if (entry.isDirectory()) {
                        walk(full);
                    } else if (entry.name === 'SingletonLock' || entry.name === 'SingletonCookie' || entry.name === 'SingletonSocket') {
                        try { fs.unlinkSync(full); console.log('Cleaned stale lock:', full); } catch(e) {}
                    }
                }
            };
            walk(sessionDir);
        }
    } catch(e) {
        console.log('Lock cleanup warning:', e.message);
    }
}

// Clean stale web version cache to force fresh download
function cleanWebCache() {
    const cacheDir = path.join(__dirname, '.wwebjs_cache');
    try {
        if (fs.existsSync(cacheDir)) {
            fs.rmSync(cacheDir, { recursive: true, force: true });
            console.log('Cleaned stale web version cache.');
        }
    } catch(e) {
        console.log('Cache cleanup warning:', e.message);
    }
}

function destroyClient() {
    return new Promise((resolve) => {
        if (!client) return resolve();
        try {
            client.destroy().then(resolve).catch(() => resolve());
        } catch(e) {
            resolve();
        }
        // Force resolve after 10 seconds if destroy hangs
        setTimeout(resolve, 10000);
    });
}

function initBot() {
    if (isShuttingDown) return;
    
    botStatus = 'initializing';
    qrCodeData = null;

    // Clean up stale Chromium lock files and old web cache
    cleanStaleLocks();
    cleanWebCache();

    const chromePath = getChromePath();
    const puppeteerConfig = {
        headless: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--no-first-run',
            '--no-zygote',
            '--disable-gpu',
            '--disable-extensions',
            '--disable-background-networking',
            '--disable-default-apps',
            '--mute-audio',
        ]
    };
    if (chromePath) {
        puppeteerConfig.executablePath = chromePath;
        console.log('Using system Chrome:', chromePath);
    } else {
        console.log('Using bundled Chromium.');
    }

    client = new Client({
        authStrategy: new LocalAuth({ dataPath: './wa_session' }),
        puppeteer: puppeteerConfig
    });

    client.on('qr', async (qr) => {
        console.log('QR Code generated - please scan! (attempt', (qrCodeData ? 'refresh' : 'first') + ')');
        botStatus = 'qr_ready';
        qrCodeData = await qrcode.toDataURL(qr);
        initRetryCount = 0; // reset on success
        if (initTimer) { clearTimeout(initTimer); initTimer = null; }
    });

    client.on('ready', () => {
        console.log('WhatsApp Bot is ready!');
        botStatus = 'connected';
        qrCodeData = null;
        initRetryCount = 0;
        if (initTimer) { clearTimeout(initTimer); initTimer = null; }
    });

    client.on('disconnected', async (reason) => {
        console.log('Bot disconnected:', reason);
        botStatus = 'disconnected';
        if (initTimer) { clearTimeout(initTimer); initTimer = null; }
        await destroyClient();
        // Auto-reconnect after 5 seconds
        if (!isShuttingDown) {
            setTimeout(() => { initBot(); }, 5000);
        }
    });

    client.on('auth_failure', async (msg) => {
        console.log('Bot auth failed:', msg);
        botStatus = 'disconnected';
        if (initTimer) { clearTimeout(initTimer); initTimer = null; }
        await destroyClient();
        // Clear session on auth failure so QR is shown fresh
        const sessionDir = path.join(__dirname, 'wa_session');
        try {
            fs.rmSync(sessionDir, { recursive: true, force: true });
            console.log('Cleared stale session after auth failure.');
        } catch(e) {}
        // Auto-reconnect after 5 seconds
        if (!isShuttingDown) {
            setTimeout(() => { initBot(); }, 5000);
        }
    });

    // 120-second watchdog (increased from 90s): if no QR or connected, restart
    initTimer = setTimeout(async () => {
        if (botStatus === 'initializing') {
            console.log('Init timeout - restarting bot...');
            await destroyClient();
            botStatus = 'disconnected';
            initRetryCount++;
            if (initRetryCount < MAX_RETRIES) {
                console.log(`Retry ${initRetryCount}/${MAX_RETRIES} in 5s...`);
                setTimeout(() => { initBot(); }, 5000);
            } else {
                console.log('Max retries reached. Use /restart to try again.');
                botStatus = 'error';
            }
        }
    }, 120000);

    client.initialize().catch(async (err) => {
        console.error('Bot init error:', err.message || err);
        if (initTimer) { clearTimeout(initTimer); initTimer = null; }
        
        // If it's a session-related error, clear session
        const errMsg = (err.message || err.toString()).toLowerCase();
        if (errMsg.includes('session') || errMsg.includes('protocol') || errMsg.includes('target closed') || errMsg.includes('browser')) {
            console.log('Session-related error detected — clearing session...');
            const sessionDir = path.join(__dirname, 'wa_session');
            try { fs.rmSync(sessionDir, { recursive: true, force: true }); } catch(e) {}
        }
        
        await destroyClient();
        botStatus = 'disconnected';
        initRetryCount++;
        if (initRetryCount < MAX_RETRIES) {
            console.log(`Retrying (${initRetryCount}/${MAX_RETRIES}) in 5s...`);
            setTimeout(() => { initBot(); }, 5000);
        } else {
            console.log('Max retries reached. Use /restart to try again.');
            botStatus = 'error';
        }
    });
}

// Start the bot initially
initBot();

// ─── API Routes ────────────────────────────────────────────────

app.get('/status', (req, res) => {
    res.json({
        status: botStatus,
        qr: qrCodeData,
        phone: client && client.info ? client.info.wid.user : null,
        stats: stats
    });
});

// Helper: check if the underlying Puppeteer page is still usable
async function isClientPageAlive() {
    try {
        if (!client || !client.pupPage) return false;
        // A simple evaluate will throw if the frame/page is detached
        await client.pupPage.evaluate(() => true);
        return true;
    } catch(e) {
        return false;
    }
}

app.post('/send', async (req, res) => {
    if (botStatus !== 'connected') {
        return res.status(400).json({ success: false, error: 'Bot not connected' });
    }
    
    const { phone, message } = req.body;
    if (!phone || !message) {
        return res.status(400).json({ success: false, error: 'Phone and message required' });
    }

    let number = phone.replace(/[^0-9]/g, '');
    if (!number.startsWith('91')) {
        number = '91' + number;
    }

    const MAX_SEND_RETRIES = 2;
    let lastError = null;

    for (let attempt = 0; attempt <= MAX_SEND_RETRIES; attempt++) {
        try {
            // Verify the browser page is still alive before sending
            if (!(await isClientPageAlive())) {
                throw new Error('Browser page detached — need reconnect');
            }

            await client.sendMessage(`${number}@c.us`, message);
            stats.sent++;
            return res.json({ success: true });
        } catch (err) {
            lastError = err;
            const errMsg = (err.message || err.toString()).toLowerCase();
            const isDetachedError = errMsg.includes('detached') || errMsg.includes('target closed') 
                                 || errMsg.includes('protocol error') || errMsg.includes('session closed')
                                 || errMsg.includes('page has been closed') || errMsg.includes('execution context');
            
            console.error(`Send error (attempt ${attempt + 1}/${MAX_SEND_RETRIES + 1}):`, err.message || err);

            if (isDetachedError && attempt < MAX_SEND_RETRIES) {
                // The browser frame is broken — trigger a full reconnect
                console.log('Detached frame detected — reconnecting bot...');
                botStatus = 'disconnected';
                if (initTimer) { clearTimeout(initTimer); initTimer = null; }
                await destroyClient();
                initRetryCount = 0;
                
                // Reinitialize and wait for it to become ready
                await new Promise((resolve) => {
                    initBot();
                    const checkReady = setInterval(() => {
                        if (botStatus === 'connected') {
                            clearInterval(checkReady);
                            resolve();
                        }
                    }, 1000);
                    // Give up after 60 seconds
                    setTimeout(() => { clearInterval(checkReady); resolve(); }, 60000);
                });

                if (botStatus !== 'connected') {
                    stats.failed++;
                    return res.status(500).json({ 
                        success: false, 
                        error: 'Bot reconnection failed. Please try again after the bot reconnects.' 
                    });
                }
                // Loop will retry the send
                continue;
            }

            // Non-detached error or last attempt — fail immediately
            break;
        }
    }

    stats.failed++;
    res.status(500).json({ success: false, error: lastError ? lastError.message || lastError.toString() : 'Unknown send error' });
});

app.post('/logout', async (req, res) => {
    if (client) {
        try {
            await client.logout();
            botStatus = 'disconnected';
            qrCodeData = null;
            setTimeout(initBot, 2000);
            res.json({ success: true });
        } catch(err) {
            await destroyClient();
            botStatus = 'disconnected';
            setTimeout(initBot, 2000);
            res.json({ success: true });
        }
    } else {
        res.json({ success: true });
    }
});

// NEW: /restart endpoint — allows recovery from "error" state
// POST /restart?clearSession=true to also wipe session data (forces new QR)
app.post('/restart', async (req, res) => {
    console.log('Manual restart requested...');
    if (initTimer) { clearTimeout(initTimer); initTimer = null; }
    await destroyClient();
    
    // Optionally clear session for fresh QR
    const clearSession = req.query.clearSession === 'true' || req.body.clearSession === true;
    if (clearSession) {
        const sessionDir = path.join(__dirname, 'wa_session');
        try {
            fs.rmSync(sessionDir, { recursive: true, force: true });
            console.log('Session data cleared for fresh QR scan.');
        } catch(e) {
            console.log('Warning: Could not fully clear session:', e.message);
        }
    }
    
    botStatus = 'disconnected';
    qrCodeData = null;
    initRetryCount = 0;  // reset retry counter
    setTimeout(() => { initBot(); }, 2000);
    res.json({ success: true, message: clearSession ? 'Bot restarting with fresh session...' : 'Bot restarting...' });
});

// NEW: /regenerate-qr — clear session and get a fresh QR immediately
app.post('/regenerate-qr', async (req, res) => {
    console.log('QR regeneration requested — clearing session...');
    if (initTimer) { clearTimeout(initTimer); initTimer = null; }
    await destroyClient();
    const sessionDir = path.join(__dirname, 'wa_session');
    try {
        fs.rmSync(sessionDir, { recursive: true, force: true });
        console.log('Session cleared for fresh QR.');
    } catch(e) {
        console.log('Warning: could not fully clear session:', e.message);
    }
    botStatus = 'disconnected';
    qrCodeData = null;
    initRetryCount = 0;
    setTimeout(() => { initBot(); }, 1500);
    res.json({ success: true, message: 'Generating new QR code...' });
});

// NEW: /health endpoint — simple health check
app.get('/health', (req, res) => {
    res.json({ alive: true, status: botStatus, uptime: process.uptime() });
});

const PORT = 3001;
app.listen(PORT, () => {
    console.log(`WhatsApp Bot API running on port ${PORT}`);
});

// ─── Graceful Shutdown ─────────────────────────────────────────
async function gracefulShutdown(signal) {
    console.log(`\n${signal} received — shutting down gracefully...`);
    isShuttingDown = true;
    if (initTimer) { clearTimeout(initTimer); initTimer = null; }
    await destroyClient();
    console.log('Bot cleaned up. Exiting.');
    process.exit(0);
}

process.on('SIGINT', () => gracefulShutdown('SIGINT'));
process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
// Windows: handle Ctrl+C
process.on('SIGHUP', () => gracefulShutdown('SIGHUP'));
