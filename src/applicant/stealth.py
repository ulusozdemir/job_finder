"""Shared browser stealth utilities for all adapters.

Provides anti-detection measures so headless Chromium looks like a regular
Chrome browser to bot-detection systems (LinkedIn, hCaptcha, etc.).
"""

from __future__ import annotations

from pathlib import Path

from playwright.async_api import BrowserContext, Playwright

from config import settings

SESSION_PATH = Path("linkedin_session.json")

_CHROME_VERSION = "124.0.0.0"

_USER_AGENT = (
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    f"AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{_CHROME_VERSION} Safari/537.36"
)

_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-infobars",
    "--disable-background-timer-throttling",
    "--disable-popup-blocking",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--window-size=1280,800",
]

_STEALTH_JS = """
// --- webdriver ---------------------------------------------------------------
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// --- chrome runtime ----------------------------------------------------------
window.chrome = {
    runtime: { onConnect: { addListener: () => {}, removeListener: () => {} },
               onMessage: { addListener: () => {}, removeListener: () => {} },
               sendMessage: () => {},
               connect: () => ({ onMessage: { addListener: () => {} } }) },
    loadTimes: () => ({ commitLoadTime: Date.now() / 1000 }),
    csi: () => ({ startE: Date.now(), onloadT: Date.now() }),
    app: { isInstalled: false, getDetails: () => null, getIsInstalled: () => false,
           installState: () => "disabled", runningState: () => "cannot_run" },
};

// --- plugins -----------------------------------------------------------------
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const plugins = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer',
              description: 'Portable Document Format',
              length: 1, item: () => ({type: 'application/pdf'}) },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
              description: '', length: 1, item: () => ({type: 'application/pdf'}) },
            { name: 'Native Client', filename: 'internal-nacl-plugin',
              description: '', length: 2, item: () => ({type: 'application/x-nacl'}) },
        ];
        plugins.refresh = () => {};
        return plugins;
    }
});

// --- mimeTypes ---------------------------------------------------------------
Object.defineProperty(navigator, 'mimeTypes', {
    get: () => {
        const mimes = [
            { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format',
              enabledPlugin: { name: 'Chrome PDF Plugin' } },
        ];
        mimes.refresh = () => {};
        return mimes;
    }
});

// --- languages ---------------------------------------------------------------
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'tr'] });
Object.defineProperty(navigator, 'language',  { get: () => 'en-US' });

// --- permissions -------------------------------------------------------------
const origQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
window.navigator.permissions.query = (params) =>
    params.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : origQuery(params);

// --- WebGL -------------------------------------------------------------------
const getParam = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return getParam.call(this, p);
};

// --- connection --------------------------------------------------------------
Object.defineProperty(navigator, 'connection', {
    get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false })
});

// --- hardware concurrency & device memory ------------------------------------
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory',        { get: () => 8 });

// --- headless detection via iframe -------------------------------------------
// Some sites create an iframe and check navigator.webdriver inside it.
const _attachShadow = Element.prototype.attachShadow;
Element.prototype.attachShadow = function() {
    return _attachShadow.apply(this, arguments);
};

// --- screen dimensions (match viewport) --------------------------------------
Object.defineProperty(screen, 'width',    { get: () => 1280 });
Object.defineProperty(screen, 'height',   { get: () => 800 });
Object.defineProperty(screen, 'availWidth',  { get: () => 1280 });
Object.defineProperty(screen, 'availHeight', { get: () => 800 });
Object.defineProperty(screen, 'colorDepth',  { get: () => 24 });
Object.defineProperty(screen, 'pixelDepth',  { get: () => 24 });

// --- Notification ------------------------------------------------------------
if (!window.Notification) {
    window.Notification = { permission: 'default' };
}
"""


async def create_stealth_context(
    pw: Playwright,
    *,
    locale: str = "en-US",
    timezone: str = "Europe/Istanbul",
) -> tuple:
    """Launch browser + context with full stealth.

    If linkedin_session.json exists, loads saved cookies/localStorage
    so LinkedIn recognises the browser as a returning device.

    Returns (browser, context) — caller is responsible for closing.
    """
    browser = await pw.chromium.launch(
        headless=settings.headless,
        args=_LAUNCH_ARGS,
    )

    ctx_kwargs: dict = dict(
        user_agent=_USER_AGENT,
        viewport={"width": 1280, "height": 800},
        locale=locale,
        timezone_id=timezone,
        color_scheme="light",
        java_script_enabled=True,
        bypass_csp=False,
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
        },
    )
    if SESSION_PATH.exists():
        ctx_kwargs["storage_state"] = str(SESSION_PATH)

    context: BrowserContext = await browser.new_context(**ctx_kwargs)
    await context.add_init_script(_STEALTH_JS)
    return browser, context
