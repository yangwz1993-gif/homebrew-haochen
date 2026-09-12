/* Small, bundled line-icon vocabulary. No network or host icon dependency. */
(function (global) {
  'use strict';
  const paths = {
    'arrow-up-right': ['M7 17 17 7', 'M7 7h10v10'],
    'arrow-right': ['M4 12h16', 'm14 6 6 6-6 6'],
    'arrow-left': ['M20 12H4', 'm10 6-6 6 6 6'],
    'chevron-right': ['m9 5 7 7-7 7'],
    'chevron-down': ['m5 9 7 7 7-7'],
    'plus': ['M12 5v14', 'M5 12h14'],
    'x': ['m6 6 12 12', 'M18 6 6 18'],
    'check': ['m5 12 4 4L19 6'],
    'pause': ['M8 5v14', 'M16 5v14'],
    'play': ['m8 5 11 7-11 7Z'],
    'minimize': ['M8 3v5H3', 'M16 3v5h5', 'M3 16h5v5', 'M21 16h-5v5'],
    'refresh': ['M20 7v5h-5', 'M4 17v-5h5', 'M6.2 6.2a8 8 0 0 1 13.3 2.4', 'M17.8 17.8A8 8 0 0 1 4.5 15.4'],
    'folder': ['M3 5h6l2 2h10v12H3Z'],
    'folder-plus': ['M3 5h6l2 2h10v12H3Z', 'M12 10v6', 'M9 13h6'],
    'notebook': ['M5 3h15v18H5Z', 'M3 7h4', 'M3 12h4', 'M3 17h4', 'M10 7h6', 'M10 12h6'],
    'calendar': ['M3 5h18v16H3Z', 'M3 10h18', 'M7 3v4', 'M17 3v4', 'M7 14h3', 'M14 14h3'],
    'bell': ['M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9', 'M10 21h4'],
    'settings': ['m10 3-1 3-3 1-3 3v4l3 3 3 1 1 3h4l1-3 3-1 3-3v-4l-3-3-3-1-1-3Z', 'M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0'],
    'link': ['m10 13 4-4', 'M8 16 6 18a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0', 'm16 8 2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0'],
    'globe': ['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0', 'M3 12h18', 'M12 3c5 5 5 13 0 18-5-5-5-13 0-18'],
    'terminal': ['M3 4h18v16H3Z', 'm7 8 4 4-4 4', 'M13 16h4'],
    'message': ['M3 4h18v13H9l-6 4Z', 'M7 9h10', 'M7 13h6'],
    'clock': ['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0', 'M12 7v5l3 2'],
    'sparkles': ['m12 3 2.7 6.3L21 12l-6.3 2.7L12 21l-2.7-6.3L3 12l6.3-2.7Z'],
    'target': ['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0', 'M17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0', 'M12 10v4', 'M10 12h4'],
    'edit': ['m14 4 6 6', 'm4 14 12-12 6 6-12 12-7 1Z'],
    'trash': ['M3 6h18', 'M9 6V3h6v3', 'm5 6 1 15h12l1-15', 'M10 10v7', 'M14 10v7'],
    'shield': ['m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z', 'm8 12 3 3 5-6'],
    'alert': ['M12 3 2 21h20Z', 'M12 9v5', 'M12 17v1'],
    'info': ['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0', 'M12 11v6', 'M12 7v1'],
    'inbox': ['M5 3h14l3 12v6H2v-6Z', 'M2 15h6l2 3h4l2-3h6'],
  };
  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    for (const [key, value] of Object.entries({viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', 'stroke-width':'1.65', 'stroke-linecap':'round', 'stroke-linejoin':'round', 'aria-hidden':'true', focusable:'false'})) svg.setAttribute(key, value);
    for (const value of paths[name] || paths.info) {
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', value); svg.appendChild(path);
    }
    return svg;
  }
  function hydrate(scope) {
    scope.querySelectorAll('[data-icon]').forEach(node => { node.replaceChildren(icon(node.dataset.icon)); node.removeAttribute('data-icon'); });
  }
  global.HaochenIcons = {icon, hydrate};
})(globalThis);
