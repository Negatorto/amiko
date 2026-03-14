/**
 * AMIKO Theme Manager
 * 
 * Maps CSS custom properties (variables) defined in main.css 
 * to a globally exposed JS object. This allows modules (like Xterm.js) 
 * to remain agnostic to hardcoded colors and react to global CSS theme changes.
 */

window.AMIKO_THEME = (function () {
    const style = getComputedStyle(document.documentElement);
    const getVar = (name, fallback) => style.getPropertyValue(name).trim() || fallback;

    return {
        // Backgrounds
        bg_primary: getVar('--bg-primary', '#0a0a0a'),
        bg_secondary: getVar('--bg-secondary', '#1a1a1a'),
        bg_tertiary: getVar('--bg-tertiary', '#2a2a2a'),

        // Text & Outlines
        text_primary: getVar('--text-primary', '#00ff41'),
        text_secondary: getVar('--text-secondary', '#00cc33'),
        text_dim: getVar('--text-dim', '#006622'),

        // Semantic Accents
        accent_warning: getVar('--accent-warning', '#ffaa00'),
        accent_danger: getVar('--accent-danger', '#ff3333'),
        accent_info: getVar('--accent-info', '#00aaff'),
    };
})();

/**
 * Refresh AMIKO_THEME after a dynamic CSS theme swap.
 * Called by SettingsPanel.applyTheme() after the new stylesheet loads.
 */
function refreshThemeVars() {
    const style = getComputedStyle(document.documentElement);
    const getVar = (name, fallback) => style.getPropertyValue(name).trim() || fallback;

    window.AMIKO_THEME = {
        bg_primary: getVar('--bg-primary', '#0a0a0a'),
        bg_secondary: getVar('--bg-secondary', '#1a1a1a'),
        bg_tertiary: getVar('--bg-tertiary', '#2a2a2a'),
        text_primary: getVar('--text-primary', '#00ff41'),
        text_secondary: getVar('--text-secondary', '#00cc33'),
        text_dim: getVar('--text-dim', '#006622'),
        accent_warning: getVar('--accent-warning', '#ffaa00'),
        accent_danger: getVar('--accent-danger', '#ff3333'),
        accent_info: getVar('--accent-info', '#00aaff'),
    };

    // Dispatch event so modules (like xterm) can react
    window.dispatchEvent(new CustomEvent('amikoThemeChanged', { detail: window.AMIKO_THEME }));
}
