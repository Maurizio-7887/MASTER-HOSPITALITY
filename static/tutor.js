// Tutor laterale del corso: un passo alla volta, con verifica automatica sui dati dell'hotel dell'allievo.
let T = null, inVerifica = false;
const leggiT = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
const scriviT = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };

async function apriTutor(forza) {
  if (document.body.classList.contains('con-tutor') && !forza) { document.body.classList.remove('con-tutor'); scriviT('mh_tutor', '0'); return; }
  const r = await api('GET', '/api/tutor');
  if (!r.ok) { toast(r.errore); return; }
  T = r.dati; document.body.classList.add('con-tutor'); scriviT('mh_tutor', '1');
  disegnaTutor();
}

function disegnaTutor(esito) {
  const el = document.getElementById('tutor');
  if (!T) return;
  const pct = Math.round(100 * T.indice / T.totale);
  let h = `<div class="tu-testa"><b>🎓 Tutor del corso</b><button class="tu-x" onclick="apriTutor()" title="Chiudi">✕</button></div>
    <div class="tu-barra"><i style="width:${pct}%"></i></div><div class="tu-mute" style="padding:0 12px">${T.indice} di ${T.totale} passi · ${pct}%</div>`;
  if (T.finito) {
    h += `<div class="tu-corpo tu-fine"><div style="font-size:46px">🏆</div><h3>Corso completato!</h3>
      <p>Hai configurato la struttura, venduto, gestito arrivi e partenze, deciso i prezzi con DynaPrice e letto i numeri del controllo di gestione.</p>
      <p class="tu-mute">Comunica al docente che hai finito per ricevere l'attestato.</p></div>`;
  } else {
    const p = T.passo, lez = T.lezioni[p.lezione];
    const qui = p.dove && location.pathname + location.search === p.dove;
    h += `<div class="tu-corpo"><div class="tu-lez">Lezione ${esc(lez.titolo)}</div><h3>${esc(p.titolo)}</h3><div class="tu-testo">${p.testo}</div>
      ${p.domanda ? `<input id="tu-risposta" style="width:100%;margin-top:8px" placeholder="La tua risposta" onkeydown="if(event.key==='Enter')verificaTutor(true)">` : ''}
      ${esito && !esito.riuscito && esito.messaggio ? `<div class="tu-msg no">${esc(esito.messaggio)}</div>` : ''}
      ${esito && esito.riuscito ? '<div class="tu-msg si">✓ Ottimo, passo completato!</div>' : ''}
      <div class="tu-az">${p.dove && !qui ? `<a class="btn sec" href="${p.dove}">👉 Vai alla pagina</a>` : ''}
      <button onclick="verificaTutor(true)">${p.info ? 'Fatto, avanti' : p.domanda ? 'Rispondi' : 'Verifica'}</button></div></div>`;
  }
  let base = 0;
  h += `<details class="tu-indice"><summary>Indice delle lezioni</summary>${T.lezioni.map(l => {
    const r = `<div class="tu-l">${esc(l.titolo)}</div>` + l.passi.map((t, j) => { const n = base + j, fatto = n < T.indice, cor = n === T.indice;
      return `<div class="tu-p${fatto ? ' fatto' : ''}${cor ? ' cor' : ''}${n <= T.massimo ? ' vai' : ''}" ${n <= T.massimo ? `onclick="vaiPasso(${n})"` : ''}>${fatto ? '✓' : cor ? '▸' : '○'} ${esc(t)}</div>`; }).join('');
    base += l.passi.length; return r; }).join('')}
    <button class="sec mini" style="margin-top:8px" onclick="ricominciaTutor()">↺ Ricomincia da capo (hotel come all'inizio)</button></details>`;
  el.innerHTML = h;
}

async function verificaTutor(manuale) {
  if (!T || T.finito || inVerifica) return;
  inVerifica = true;
  const inp = document.getElementById('tu-risposta');
  const r = await api('POST', '/api/tutor/verifica', inp ? {risposta: inp.value} : {});
  inVerifica = false;
  if (!r.ok) { if (manuale) toast(r.errore); return; }
  const prima = T.indice;
  T = r.dati;
  if (T.indice > prima) { disegnaTutor({riuscito: true}); setTimeout(() => disegnaTutor(), 1500); }
  else if (manuale) disegnaTutor(T.esito);
}
// dopo un'azione fatta con le API (planner, housekeeping, DynaPrice) il tutor ricontrolla
window.tutorDopoModifica = async () => {
  if (!document.body.classList.contains('con-tutor')) return;
  const r = await api('GET', '/api/tutor');
  if (r.ok) { const prima = T ? T.indice : 0; T = r.dati; disegnaTutor(T.indice > prima ? {riuscito: true} : undefined); }
};
async function vaiPasso(n) { const r = await api('POST', '/api/tutor/vai', {indice: n}); if (r.ok) { T = r.dati; disegnaTutor(); } }
async function ricominciaTutor() {
  if (!confirm('Ricominciare il corso? Il tuo hotel torna com\'era all\'inizio e il tutor riparte dal primo passo.')) return;
  const r = await api('POST', '/api/tutor/ricomincia', {});
  if (!r.ok) return toast(r.errore);
  location.href = '/oggi';
}

if (leggiT('mh_tutor') !== '0') setTimeout(() => apriTutor(true), 150);
