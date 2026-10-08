// EVA brand system: one source of truth for the cube mark, its variants and motion.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else {
    root.EvaBrand = api;
    api.applyCss(root.document?.documentElement, root.document?.documentElement.getAttribute("data-theme") || "light");
  }
})(globalThis, function () {
  "use strict";

  const heroAngle = Object.freeze([-24, -36]);
  const tokens = Object.freeze({
    geometry: Object.freeze({
      envelope: 128,
      heroAngle,
      views: Object.freeze({front:Object.freeze([0,0]),side:Object.freeze([0,-90]),top:Object.freeze([-90,0]),
        iso:heroAngle,oblique:Object.freeze([-18,-58]),bottom:Object.freeze([90,0])}),
      perspective: 1100,
      orders: Object.freeze({
        2: Object.freeze({ cubieSize: 62, gap: 4, radius: 5, spacing: 33 }),
        3: Object.freeze({ cubieSize: 40, gap: 4, radius: 3.3, spacing: 44 }),
      }),
    }),
    color: Object.freeze({
      light: Object.freeze({ white: "#f8f8f6", whiteShade: "#f0f0ed", graphite: "#343436", graphiteShade: "#28282a", edge: "rgba(35,35,38,.1)" }),
      dark: Object.freeze({ white: "#ededeb", whiteShade: "#d9d9d7", graphite: "#414144", graphiteShade: "#313134", edge: "rgba(0,0,0,.28)" }),
    }),
    motion: Object.freeze({ intro: 500, hold:700, settle:650, move: 280, solve: 220, reduced: 0, cadence: Object.freeze([1.04, .98, 1, .96]),
      modes: Object.freeze({
        normal: Object.freeze({order:2, scrambleLength:10, moveDuration:280, solveDuration:220}),
        deep: Object.freeze({order:3, scrambleLength:14, moveDuration:340, solveDuration:270}),
      }),
    }),
    variants: Object.freeze(["primary", "monochrome", "dark", "light", "favicon"]),
    usage: Object.freeze({ clearSpaceRatio: 0.25, minimumMasterPx: 160, minimumProductPx: 48, minimumMicroPx: 16 }),
  });

  function geometry(order = 2) {
    if (!tokens.geometry.orders[order]) throw new RangeError("Cube order must be 2 or 3.");
    return tokens.geometry.orders[order];
  }

  function camera(view = "iso") {
    if (!Object.hasOwn(tokens.geometry.views, view)) throw new RangeError("Unknown cube view");
    return tokens.geometry.views[view];
  }

  function applyCss(rootElement, theme = "light") {
    if (!rootElement?.style) return;
    const color = tokens.color[theme] || tokens.color.light;
    const g = geometry(2);
    const vars = {
      "--brand-cube-size": `${g.cubieSize}px`, "--brand-cube-gap": `${g.gap}px`,
      "--brand-cube-radius": `${g.radius}px`, "--brand-perspective": `${tokens.geometry.perspective}px`,
      "--brand-cube-white": color.white, "--brand-cube-white-shade": color.whiteShade,
      "--brand-cube-graphite": color.graphite, "--brand-cube-graphite-shade": color.graphiteShade,
      "--brand-cube-edge": color.edge,
    };
    Object.entries(vars).forEach(([key, value]) => rootElement.style.setProperty(key, value));
  }

  function palette(variant = "primary") {
    if (!tokens.variants.includes(variant)) throw new RangeError("Unknown logo variant");
    const dark = variant === "dark";
    const c = tokens.color[dark ? "dark" : "light"];
    if (variant === "favicon") return Object.freeze({front:"#eeeeec",top:"#ffffff",right:"#1d1d1f",back:"#eeeeec",left:"#eeeeec",bottom:"#eeeeec",edge:"#1d1d1f",background:"#f8f8f6"});
    return Object.freeze({front: variant === "monochrome" ? "#8a8a8a" : c.whiteShade,
      top: variant === "monochrome" ? "#b4b4b4" : c.white,
      right: variant === "monochrome" ? "#252528" : c.graphite,
      back:variant === "monochrome" ? "#707070" : "#d8d8d5",
      left:variant === "monochrome" ? "#a0a0a0" : "#e5e5e2",
      bottom:variant === "monochrome" ? "#525252" : "#c3c3c0",
      edge: dark ? "#646467" : "#aaaab0", background:dark ? "#1d1d1f" : "#f8f8f6"});
  }
  return Object.freeze({ tokens, geometry, camera, applyCss, palette, variants: tokens.variants });
});
