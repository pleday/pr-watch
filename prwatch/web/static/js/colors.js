// The colours of the states (green: ready, orange: changes requested / to watch, red: failed), which the user can change:
// presets for colour-blind people, high contrast, or three colours of their own. Kept in this browser, applied before the first draw.
const KEY = 'pw-colors';

// Each value is {light, dark}: [green, orange, red]. The presets keep a contrast of at least 4.5:1 on the cards of their theme.
export const PRESETS = {
  default: null,
  'colour-blind': { light: ['#0072b2', '#9a5a00', '#a8195f'], dark: ['#56b4e9', '#e69f00', '#f08cc0'] },
  contrast: { light: ['#0a5c1f', '#8a3b00', '#a40e1a'], dark: ['#56d364', '#ffa657', '#ff7b72'] },
};

export const DEFAULTS = { preset: 'default', custom: ['#1a7f37', '#bc4c00', '#cf222e'], patterns: false };

export function readColors() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (saved && typeof saved === 'object') return { ...DEFAULTS, ...saved };
  } catch { /* the default colours */ }
  return { ...DEFAULTS };
}

const rule = ([green, orange, red]) => `:root { --green: ${green}; --orange: ${orange}; --red: ${red}; }`;

// A constructable stylesheet: the page's content security policy forbids inline <style> elements and style attributes.
const sheet = new CSSStyleSheet();

export function applyColors(choice = readColors()) {
  if (!document.adoptedStyleSheets.includes(sheet)) document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  const colours = choice.preset === 'custom' ? { light: choice.custom, dark: choice.custom } : PRESETS[choice.preset];
  sheet.replaceSync(colours ? `${rule(colours.light)}\n@media (prefers-color-scheme: dark) { ${rule(colours.dark)} }` : '');
  document.body.classList.toggle('frame-patterns', Boolean(choice.patterns));
}

export function saveColors(choice) {
  try { localStorage.setItem(KEY, JSON.stringify(choice)); } catch { /* applied for this page only */ }
  applyColors(choice);
}
