// Planner delle camere: righe = camere (per tipologia), colonne = giorni, barre = soggiorni (trascinabili).
const W = 46, LAB = 120;
let D = null;
const GG = ['dom', 'lun', 'mar', 'mer', 'gio', 'ven', 'sab'];
const MESI = ['gen', 'feb', 'mar', 'apr', 'mag', 'giu', 'lug', 'ago', 'set', 'ott', 'nov', 'dic'];
const giornoDa = (iso, n) => { const d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
const diff = (a, b) => Math.round((new Date(b + 'T12:00:00') - new Date(a + 'T12:00:00')) / 86400000);

async function carica(da) {
  da = da || document.getElementById('pl-da').value;
  const n = document.getElementById('pl-giorni').value;
  const r = await api('GET', `/api/planner?da=${da}&giorni=${n}`);
  if (!r.ok) return toast(r.errore);
  D = r.dati; document.getElementById('pl-da').value = D.da;
  history.replaceState(null, '', '/planner?da=' + D.da);
  disegna();
}
function sposta(n) { carica(giornoDa(document.getElementById('pl-da').value, n)); }
function vaiOggi() { carica(giornoDa(new Date().toISOString().slice(0, 10), -2)); }

function disegna() {
  const el = document.getElementById('pl');
  const giorni = [...Array(D.giorni).keys()].map(i => giornoDa(D.da, i));
  el.style.width = (LAB + W * D.giorni) + 'px';
  const cls = g => { const d = new Date(g + 'T12:00:00').getDay(); return (d === 0 || d === 6 ? ' we' : '') + (g === D.oggi ? ' oggi' : ''); };
  let h = `<div class="pl-row pl-head"><div class="pl-lab">Camera</div>${giorni.map(g => { const d = new Date(g + 'T12:00:00'); const o = D.occupazione[g], p = D.totale ? Math.round(100 * o / D.totale) : 0;
    return `<div class="pl-g${cls(g)}" style="width:${W}px">${GG[d.getDay()]}<b>${d.getDate()}</b><span class="pl-occ ${p >= 90 ? 'critico' : p >= 70 ? 'attenzione' : ''}">${p}%</span>${d.getDate() === 1 || g === D.da ? '' : ''}</div>`; }).join('')}</div>`;
  const celle = cam => giorni.map(g => `<div class="pl-g cella${cls(g)}" style="width:${W}px" data-g="${g}"></div>`).join('');
  for (const t of D.tipi) {
    h += `<div class="pl-row tipo"><div class="pl-lab">${esc(t.nome)}</div>${giorni.map(g => `<div class="pl-g${cls(g)}" style="width:${W}px"></div>`).join('')}</div>`;
    for (const c of D.camere.filter(c => c.tipo_id === t.id))
      h += `<div class="pl-row" data-camera="${c.id}"><div class="pl-lab"><span class="pallino s-${c.stato}" title="${c.stato}"></span>${esc(c.numero)} <span class="mute">${esc(c.tipo)}</span></div>${celle(c)}</div>`;
  }
  // senza camera: corsie che non si sovrappongono
  const senza = D.prenotazioni.filter(p => !p.camera_id), corsie = [];
  for (const p of senza.sort((a, b) => a.arrivo.localeCompare(b.arrivo))) {
    let k = corsie.findIndex(f => f <= p.arrivo); if (k < 0) { corsie.push(''); k = corsie.length - 1; }
    corsie[k] = p.partenza; p._corsia = k;
  }
  corsie.forEach((_, k) => { h += `<div class="pl-row" data-corsia="${k}"><div class="pl-lab critico">Da assegnare</div>${giorni.map(g => `<div class="pl-g${cls(g)}" style="width:${W}px"></div>`).join('')}</div>`; });
  el.innerHTML = h;
  for (const p of D.prenotazioni) {
    const riga = p.camera_id ? el.querySelector(`.pl-row[data-camera="${p.camera_id}"]`) : el.querySelector(`.pl-row[data-corsia="${p._corsia}"]`);
    if (!riga) continue;
    const i0 = diff(D.da, p.arrivo), i1 = diff(D.da, p.partenza);
    const a = Math.max(i0, -0.5), b = Math.min(i1, D.giorni - 0.5);
    if (b <= a) continue;
    const bar = document.createElement('div');
    bar.className = 'pl-bar ' + p.stato;
    bar.style.cssText = `left:${LAB + (a + 0.5) * W + 1}px;top:4px;width:${Math.max(10, (b - a) * W - 3)}px;background-color:${p.colore}`;
    bar.textContent = `${p.ospite}${p.gruppo ? ' · ' + p.gruppo : ''}`;
    bar.title = `${p.codice} · ${p.ospite}\n${p.arrivo} → ${p.partenza} · ${p.persone} pers. · ${p.tipo} · ${p.piano}\n${p.canale} · ${euro(p.importo)} · ${p.stato}`;
    bar.dataset.id = p.id;
    riga.style.position = 'relative';
    riga.appendChild(bar);
    trascinabile(bar, p);
  }
  el.querySelectorAll('.pl-row[data-camera] .cella').forEach(c => c.addEventListener('click', () =>
    location.href = `/prenotazioni/nuova?camera_id=${c.parentElement.dataset.camera}&arrivo=${c.dataset.g}`));
}

function trascinabile(bar, p) {
  bar.addEventListener('pointerdown', ev => {
    ev.preventDefault();
    const x0 = ev.clientX, y0 = ev.clientY, r = bar.getBoundingClientRect();
    let fantasma = null, mosso = false;
    const muovi = e => {
      if (!mosso && Math.abs(e.clientX - x0) + Math.abs(e.clientY - y0) < 6) return;
      if (p.stato === 'partita') return;
      if (!fantasma) { fantasma = bar.cloneNode(true); fantasma.classList.add('fantasma'); fantasma.style.position = 'fixed'; fantasma.style.width = r.width + 'px'; document.body.appendChild(fantasma); bar.classList.add('trascinata'); }
      mosso = true;
      fantasma.style.left = (r.left + e.clientX - x0) + 'px'; fantasma.style.top = (r.top + e.clientY - y0) + 'px';
    };
    const fine = async e => {
      document.removeEventListener('pointermove', muovi); document.removeEventListener('pointerup', fine);
      if (fantasma) fantasma.remove(); bar.classList.remove('trascinata');
      if (!mosso) { location.href = '/prenotazioni/' + p.id; return; }
      const sotto = document.elementFromPoint(e.clientX, e.clientY);
      const riga = sotto && sotto.closest('.pl-row[data-camera]');
      if (!riga) return toast('Rilascia la barra sulla riga di una camera');
      const giorniSpost = Math.round((e.clientX - x0) / W);
      const corpo = {camera_id: +riga.dataset.camera};
      if (giorniSpost) corpo.arrivo = giornoDa(p.arrivo, giorniSpost);
      if (corpo.camera_id === p.camera_id && !corpo.arrivo) return;
      const res = await api('POST', `/api/prenotazioni/${p.id}/sposta`, corpo);
      if (!res.ok) return toast(res.errore);
      toast('Prenotazione spostata'); await carica(); tutorDopoModifica();
    };
    document.addEventListener('pointermove', muovi); document.addEventListener('pointerup', fine);
  });
}

carica();
