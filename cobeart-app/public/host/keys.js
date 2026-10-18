/**
 * Operator keys
 *
 * Forwards keydown events the host page receives (focus outside the world iframe) into the world iframe
 * (same origin), so the world's keys (Splat: P pause, G backgrounds) work whichever document has focus.
 * Keys pressed inside the world reach it directly; this never forwards them twice.
 */

(function () {
    'use strict';

    function init() {
        const frameEl = document.getElementById('worldFrame');
        window.addEventListener('keydown', (e) => {
            const world = frameEl.contentWindow;
            if (!world) return;
            const { key, code, shiftKey, altKey, ctrlKey, metaKey, repeat } = e;
            world.dispatchEvent(new world.KeyboardEvent('keydown', { key, code, shiftKey, altKey, ctrlKey, metaKey, repeat }));
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
