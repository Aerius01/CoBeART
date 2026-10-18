/**
 * Border Control - Magenta Neon Border Toggle
 *
 * Creates a magenta neon border around the window that can be toggled
 * on/off with the 'B' key.
 */

(function () {
    'use strict';

    let borderWrapper = null;
    let isVisible = false;

    function createBorder() {
        // Create wrapper element
        borderWrapper = document.createElement('div');
        borderWrapper.className = 'neon-border-wrapper';

        // Create inner border element
        const border = document.createElement('div');
        border.className = 'neon-border';

        borderWrapper.appendChild(border);
        document.body.appendChild(borderWrapper);

        console.log('[border-control] Magenta neon border created (hidden by default)');
    }

    function toggleBorder() {
        if (!borderWrapper) return;

        isVisible = !isVisible;

        if (isVisible) {
            borderWrapper.classList.add('visible');
            console.log('[border-control] Border visible');
        } else {
            borderWrapper.classList.remove('visible');
            console.log('[border-control] Border hidden');
        }
    }

    function onKeydown(e) {
        if (e.key === 'b' || e.key === 'B') {
            toggleBorder();
        }
    }

    function init() {
        // Create border on page load
        createBorder();

        // Listen for 'B' key to toggle, on the host page and inside the world iframe (same origin), so it works
        // whichever has focus. Only trusted events count in the world: host/keys.js forwards host keys into it.
        window.addEventListener('keydown', onKeydown);
        const frameEl = document.getElementById('worldFrame');
        const listenInWorld = () => frameEl.contentWindow.addEventListener('keydown', (e) => {
            if (e.isTrusted) onKeydown(e);
        });
        frameEl.addEventListener('load', listenInWorld);
        // The world may already have loaded before this ran
        if (frameEl.contentDocument?.readyState === 'complete' && frameEl.contentWindow.location.href !== 'about:blank') {
            listenInWorld();
        }
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
