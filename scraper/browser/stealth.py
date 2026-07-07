"""Playwright-Stealth setup and fingerprint masking.

Every new browser context must be passed through :func:`apply_stealth`
before any page interaction happens. Scraping code must never touch a raw
Playwright context that skipped this step.
"""

from __future__ import annotations

from playwright.async_api import BrowserContext

try:
    # playwright-stealth >= 1.0 API
    from playwright_stealth import Stealth

    async def _apply_lib_stealth(context: BrowserContext) -> None:
        await Stealth().apply_stealth_async(context)

except ImportError:  # pragma: no cover - compatibility with older releases
    from playwright_stealth import stealth_async  # type: ignore[no-redef]

    async def _apply_lib_stealth(context: BrowserContext) -> None:
        await stealth_async(context)

# Belt-and-suspenders overrides on top of playwright-stealth's own patches.
# playwright-stealth already masks navigator.webdriver, chrome runtime,
# permissions, plugins, etc. These add canvas/webgl noise which stealth
# does not cover in all versions.
_EXTRA_FINGERPRINT_SCRIPT = """
(() => {
    // Canvas fingerprint noise: perturb a handful of pixels per read so
    // repeated canvas fingerprint hashes are not perfectly stable.
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function (...args) {
        const ctx = this.getContext('2d');
        if (ctx) {
            const shift = () => Math.floor(Math.random() * 2) - 1;
            const imageData = ctx.getImageData(0, 0, this.width, this.height);
            for (let i = 0; i < imageData.data.length; i += 97) {
                imageData.data[i] = Math.max(0, Math.min(255, imageData.data[i] + shift()));
            }
            ctx.putImageData(imageData, 0, 0);
        }
        return origToDataURL.apply(this, args);
    };

    // WebGL vendor/renderer strings: report common consumer hardware
    // instead of the headless/software renderer signature.
    const getParameterProxyHandler = {
        apply(target, ctx, args) {
            const [parameter] = args;
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return Reflect.apply(target, ctx, args);
        },
    };
    for (const proto of [
        window.WebGLRenderingContext && window.WebGLRenderingContext.prototype,
        window.WebGL2RenderingContext && window.WebGL2RenderingContext.prototype,
    ]) {
        if (proto) {
            proto.getParameter = new Proxy(proto.getParameter, getParameterProxyHandler);
        }
    }
})();
"""


async def apply_stealth(context: BrowserContext) -> None:
    """Apply playwright-stealth patches plus extra fingerprint noise to
    every page created in this context, including pages opened after this
    call (via ``add_init_script``)."""
    await _apply_lib_stealth(context)
    await context.add_init_script(_EXTRA_FINGERPRINT_SCRIPT)
