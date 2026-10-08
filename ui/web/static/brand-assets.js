// Static assets projected from the same solved cubies, scale and camera as the 3D mark.
(function(root, factory) {
  const common = typeof module === "object" && module.exports;
  const api = factory(common ? require("./logo-tokens.js") : root.EvaBrand,
    common ? require("./cube-state.js") : root.CubeLogic);
  if (common) module.exports = api; else root.EvaBrandAssets = api;
})(globalThis, function(brand, logic) {
  "use strict";
  function svg({order=2, variant="primary", size=256, view="iso"}={}) {
    if (!Number.isInteger(size) || size < 16 || size > 2048) throw new RangeError("Asset size must be 16–2048");
    brand.geometry(order);brand.camera(view);
    // Micro identity is canonical even when called outside the settings UI.
    if (variant === "favicon") { order=2;view="iso"; }
    const g = brand.geometry(order), colors = brand.palette(variant);
    const state = new logic.CubeState(order);
    const [ax, ay] = brand.camera(view).map(v => v * Math.PI / 180);
    function project([x,y,z]) {
      const x1 = x*Math.cos(ay) + z*Math.sin(ay), z1 = -x*Math.sin(ay) + z*Math.cos(ay);
      const y1 = y*Math.cos(ax) - z1*Math.sin(ax), z2 = y*Math.sin(ax) + z1*Math.cos(ax);
      return [x1,y1,z2];
    }
    const h = g.cubieSize/2, faces = [];
    const surfaces = [
      {name:"front",axis:2,side:1,points:[[-h,-h,h],[h,-h,h],[h,h,h],[-h,h,h]]},
      {name:"right",axis:0,side:1,points:[[h,-h,h],[h,-h,-h],[h,h,-h],[h,h,h]]},
      {name:"top",axis:1,side:-1,points:[[-h,-h,-h],[h,-h,-h],[h,-h,h],[-h,-h,h]]},
      {name:"back",axis:2,side:-1,points:[[h,-h,-h],[-h,-h,-h],[-h,h,-h],[h,h,-h]]},
      {name:"left",axis:0,side:-1,points:[[-h,-h,-h],[-h,-h,h],[-h,h,h],[-h,h,-h]]},
      {name:"bottom",axis:1,side:1,points:[[-h,h,h],[h,h,h],[h,h,-h],[-h,h,-h]]},
    ];
    const visible=surfaces.filter(face=>{
      const normal=[0,0,0];normal[face.axis]=face.side;
      return project(normal)[2]>1e-8;
    });
    for (const cubie of state.cubies) for (const face of visible) {
      if (cubie.position[face.axis] !== face.side) continue;
      const points = face.points.map(p => project(p.map((n,i)=>n+cubie.position[i]*g.spacing)));
      faces.push({name:face.name,points,depth:points.reduce((v,p)=>v+p[2],0)/4});
    }
    faces.sort((a,b)=>a.depth-b.depth);
    const all = faces.flatMap(f=>f.points), extent = Math.max(...all.map(p=>Math.max(Math.abs(p[0]),Math.abs(p[1]))));
    // 25% clear space on each side of the mark, preserved in every export.
    const scale = 100/(2*extent*(1+2*brand.tokens.usage.clearSpaceRatio));
    const stroke = variant === "favicon" ? 1.1 : .45;
    const point = p=>`${(50+p[0]*scale).toFixed(3)},${(50+p[1]*scale).toFixed(3)}`;
    function outline(points) {
      // Project the same corner radius; tiny marks simplify corners for clarity.
      const ratio=variant==="favicon"?0:g.radius/g.cubieSize;
      if(!ratio)return "M"+points.map(point).join(" L")+" Z";
      const inset=(a,b)=>a.map((v,i)=>v+(b[i]-v)*ratio);
      return points.map((corner,i)=>{
        const before=inset(corner,points[(i+3)%4]),after=inset(corner,points[(i+1)%4]);
        return `${i?"L":"M"}${point(before)} Q${point(corner)} ${point(after)}`;
      }).join(" ")+" Z";
    }
    const paths = faces.map(f=>`<path fill="${colors[f.name]}" d="${outline(f.points)}"/>`).join("");
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 100 100" role="img"><title>EVA ${order}×${order} modular cube</title><g stroke="${colors.edge}" stroke-width="${stroke}" stroke-linejoin="round">${paths}</g></svg>`;
  }
  function dataUrl(options) { return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg(options)); }
  return Object.freeze({svg, dataUrl});
});
