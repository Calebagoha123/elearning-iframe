(() => {
  document.querySelectorAll('[data-flexible-scatter]').forEach(host => {
    const data = document.getElementById(host.dataset.pointsId);
    if (!host || !data) return;
    const points = JSON.parse(data.textContent);
    if (!points.length) { host.hidden = true; return; }
    const svg = host.querySelector('svg');
    const ns = 'http://www.w3.org/2000/svg';
    const make = (tag, attrs, text) => {
      const node = document.createElementNS(ns, tag);
      Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
      if (text !== undefined) node.textContent = text;
      svg.appendChild(node); return node;
    };
    const bounds = points.reduce((b, p) => ({xmin: Math.min(b.xmin, p.x), xmax: Math.max(b.xmax, p.x), ymin: Math.min(b.ymin, p.y), ymax: Math.max(b.ymax, p.y)}), {xmin: Infinity, xmax: -Infinity, ymin: Infinity, ymax: -Infinity});
    const {xmin, xmax, ymin, ymax} = bounds;
    const dx = xmax - xmin || 1, dy = ymax - ymin || 1;
    const xlo = xmin - dx * .08, xhi = xmax + dx * .08, ylo = ymin - dy * .08, yhi = ymax + dy * .08;
    const x = v => 80 + (v-xlo)/(xhi-xlo)*600, y = v => 390 - (v-ylo)/(yhi-ylo)*350;
    for (let i=0; i<=5; i++) {
      const xv = xlo+(xhi-xlo)*i/5, yv = ylo+(yhi-ylo)*i/5;
      make('line', {x1:80, x2:680, y1:y(yv), y2:y(yv), stroke:'#e1d5b8'});
      make('text', {x:x(xv), y:413, 'text-anchor':'middle', 'font-size':12, fill:'#6c5b73'}, Number(xv.toPrecision(3)));
      make('text', {x:68, y:y(yv)+4, 'text-anchor':'end', 'font-size':12, fill:'#6c5b73'}, Number(yv.toPrecision(3)));
    }
    make('line', {x1:80,x2:680,y1:390,y2:390,stroke:'#6c5b73'});
    make('line', {x1:80,x2:80,y1:40,y2:390,stroke:'#6c5b73'});
    make('text', {x:380,y:450,'text-anchor':'middle','font-size':14}, host.dataset.xLabel);
    make('text', {x:20,y:215,transform:'rotate(-90 20 215)','text-anchor':'middle','font-size':14}, host.dataset.yLabel);
    points.sort((a,b) => Number(a.is_yours)-Number(b.is_yours)).forEach(point => {
      const circle = make('circle', {cx:x(point.x),cy:y(point.y),r:point.is_yours ? 6 : 4.5,fill:point.is_yours ? '#e3aa00' : '#802bb1',opacity:point.is_yours ? 1 : .5});
      const title = document.createElementNS(ns,'title');
      title.textContent = `${host.dataset.xLabel}: ${point.x}; ${host.dataset.yLabel}: ${point.y}${point.is_yours ? ' (your entry)' : ''}`;
      circle.appendChild(title);
    });
  });
})();
