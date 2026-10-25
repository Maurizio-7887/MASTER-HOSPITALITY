"""DynaPrice: il modulo di revenue management di MASTER HOSPITALITY.

Per ogni notte e tipologia:
- on the books (OTB): camere già vendute; pickup: camere vendute negli ultimi 7 giorni;
- previsione dell'occupazione finale con la curva delle prenotazioni (quanta parte della domanda arriva a N giorni
  dall'arrivo), la domanda storica dello stesso giorno della settimana e l'impatto degli eventi;
- prezzo suggerito: listino × stagione × moltiplicatore che cresce con l'occupazione prevista, mai sotto la soglia di
  margine (costo variabile della camera e commissione del canale più caro devono lasciare il margine minimo);
- rischio di cancellazione di ogni prenotazione con un modello a punteggio spiegabile, e camere di overbooking
  suggerite per notte.

Ogni numero è spiegato in pagina: il modulo serve a decidere e a insegnare a decidere.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta

from hotel import camere_attive, giorni, par, prezzo_camera
from models import Canale, Evento, NotteVenduta, OCCUPANO, Prenotazione, Stagione, TariffaGiorno, TipoCamera, db

# quota della domanda finale già prenotata a N giorni dall'arrivo (curva tipica di un hotel urbano)
CURVA = [(0, 1.0), (3, 0.93), (7, 0.85), (14, 0.74), (30, 0.58), (60, 0.38), (90, 0.24), (180, 0.10)]
# moltiplicatore di prezzo per occupazione prevista
SCALA = [(0.0, 0.82), (0.40, 0.88), (0.55, 0.95), (0.65, 1.0), (0.75, 1.07), (0.85, 1.15), (0.93, 1.24), (1.0, 1.32)]


def _interp(tab, x):
    if x <= tab[0][0]:
        return tab[0][1]
    for (x0, y0), (x1, y1) in zip(tab, tab[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return tab[-1][1]


def quota_prenotata(giorni_all_arrivo):
    return _interp(CURVA, max(0, giorni_all_arrivo))


def moltiplicatore(occ):
    return _interp(SCALA, max(0.0, min(1.0, occ)))


def soglia_prezzo(tipo, commissione_pct=None):
    """Prezzo minimo (IVA inclusa) che lascia il margine minimo dopo IVA, commissione e costo variabile della camera.
    P/(1+iva)·(1−m) − c·P ≥ costo  →  P ≥ costo / ((1−m)/(1+iva) − c)"""
    iva = par('iva_alloggio') / 100
    m = par('margine_minimo') / 100
    if commissione_pct is None:
        commissione_pct = max([c.commissione_pct or 0 for c in Canale.query.filter_by(attivo=True).all()] + [0])
    c = commissione_pct / 100
    den = (1 - m) / (1 + iva) - c
    if den <= 0.05:
        den = 0.05
    soglia = (tipo.costo_variabile or 0) / den
    if tipo.prezzo_minimo:
        soglia = max(soglia, tipo.prezzo_minimo)
    return round(soglia, 2)


def impatto_eventi(d, eventi):
    imp, nomi = 0.0, []
    for e in eventi:
        if e.dal <= d <= e.al:
            imp += (e.impatto_pct or 0) / 100
            nomi.append(e.nome)
    return imp, nomi


def _storico_per_giorno_settimana(oggi, tipi_camere):
    """Occupazione media realizzata nelle ultime 8 settimane per giorno della settimana (0 = lunedì)."""
    da = oggi - timedelta(days=56)
    totale = sum(tipi_camere.values()) or 1
    righe = (db.session.query(NotteVenduta.data, db.func.count(NotteVenduta.id))
             .join(Prenotazione, Prenotazione.id == NotteVenduta.prenotazione_id)
             .filter(Prenotazione.stato.in_(OCCUPANO), NotteVenduta.data >= da, NotteVenduta.data < oggi)
             .group_by(NotteVenduta.data).all())
    per_gs = defaultdict(list)
    for d, n in righe:
        per_gs[d.weekday()].append(n / totale)
    return {gs: (sum(v) / 8) for gs, v in per_gs.items()}   # i giorni senza vendite contano zero


def griglia(da, n_giorni=28, oggi=None):
    """La tabella di DynaPrice: una riga per notte, con i dati per tipologia."""
    oggi = oggi or date.today()
    a = da + timedelta(days=n_giorni)
    tipi = TipoCamera.query.order_by(TipoCamera.ordine, TipoCamera.id).all()
    camere = {t.id: len(camere_attive(t.id)) for t in tipi}
    tot_camere = sum(camere.values())
    stagioni, eventi = Stagione.query.all(), Evento.query.filter(Evento.al >= da, Evento.dal < a).all()
    tariffe = {(t.tipo_id, t.data): t.prezzo for t in TariffaGiorno.query.filter(TariffaGiorno.data >= da, TariffaGiorno.data < a)}
    origine = {(t.tipo_id, t.data): t.origine for t in TariffaGiorno.query.filter(TariffaGiorno.data >= da, TariffaGiorno.data < a)}
    sette = datetime.combine(oggi - timedelta(days=7), datetime.min.time())
    notti = (db.session.query(NotteVenduta.data, NotteVenduta.prezzo, Prenotazione.tipo_id, Prenotazione.creato_il)
             .join(Prenotazione, Prenotazione.id == NotteVenduta.prenotazione_id)
             .filter(Prenotazione.stato.in_(OCCUPANO), NotteVenduta.data >= da, NotteVenduta.data < a).all())
    otb = defaultdict(int)
    ricavo = defaultdict(float)
    pickup = defaultdict(int)
    for d, prezzo, tid, creato in notti:
        otb[(d, tid)] += 1
        ricavo[d] += prezzo or 0
        if creato and creato >= sette:
            pickup[d] += 1
    storico = _storico_per_giorno_settimana(oggi, camere)
    soglie = {t.id: soglia_prezzo(t) for t in tipi}
    righe = []
    for d in giorni(da, a):
        lead = (d - oggi).days
        quota = quota_prenotata(lead)
        imp, nomi = impatto_eventi(d, eventi)
        coeff = 1.0
        for s in stagioni:
            if s.dal <= d <= s.al:
                coeff = s.coefficiente or 1.0
                break
        venduto = sum(otb[(d, t.id)] for t in tipi)
        base_storica = storico.get(d.weekday(), 0.6 if not storico else 0.0)
        base_storica = min(1.0, base_storica * (1 + imp))
        celle = []
        prev_tot = 0
        occ_hotel = venduto / tot_camere if tot_camere else 0
        for t in tipi:
            cap = camere[t.id] or 0
            v = otb[(d, t.id)]
            # le tipologie con poche camere oscillano molto: si media con l'occupazione dell'intero hotel
            peso = cap / (cap + 6) if cap else 0
            occ_otb = (v / cap if cap else 0) * peso + occ_hotel * (1 - peso)
            if lead < 0:
                prev = occ_otb
            elif v >= cap:   # tipologia esaurita
                prev = 1.0
            else:   # già venduto + domanda che deve ancora arrivare (meno, quanto più si è vicini all'arrivo)
                prev = min(1.0, occ_otb + (1 - quota) * max(base_storica, occ_otb))
            prev_tot += prev * cap
            attuale = prezzo_camera(t, d, stagioni, tariffe)
            listino = round(t.prezzo_base * coeff)
            sugg = listino * moltiplicatore(prev)
            if t.prezzo_massimo:
                sugg = min(sugg, t.prezzo_massimo)
            sugg = max(round(sugg), round(soglie[t.id] + 0.49))
            celle.append({'tipo_id': t.id, 'camere': cap, 'venduto': v, 'libere': cap - v, 'occ_otb': round(occ_otb, 3),
                          'previsione': round(prev, 3), 'prezzo': attuale, 'listino': listino, 'suggerito': sugg,
                          'soglia': soglie[t.id], 'origine': origine.get((t.id, d), 'listino'),
                          'scarto': round((sugg - attuale) / attuale * 100) if attuale else 0})
        righe.append({'data': d.isoformat(), 'giorno': d.weekday(), 'lead': lead, 'coeff': coeff, 'eventi': nomi,
                      'venduto': venduto, 'camere': tot_camere, 'occ': round(venduto / tot_camere, 3) if tot_camere else 0,
                      'previsione': round(prev_tot / tot_camere, 3) if tot_camere else 0,
                      'adr': round(ricavo[d] / venduto, 2) if venduto else None, 'pickup': pickup[d], 'celle': celle,
                      'overbooking': 0, 'cancellazioni_attese': 0})
    # cancellazioni attese e overbooking suggerito
    attese = cancellazioni_attese(da, a, oggi)
    massimo = par('max_overbooking')
    for r in righe:
        e = attese.get(r['data'], 0)
        r['cancellazioni_attese'] = round(e, 2)
        r['overbooking'] = min(massimo, int(e * 0.7)) if r['lead'] >= 0 else 0
    return {'tipi': [{'id': t.id, 'codice': t.codice, 'nome': t.nome, 'camere': camere[t.id], 'soglia': soglie[t.id],
                      'costo_variabile': t.costo_variabile} for t in tipi], 'righe': righe,
            'margine_minimo': par('margine_minimo'), 'iva': par('iva_alloggio')}


def applica(prezzi):
    """prezzi: [(tipo_id, data, prezzo)] → tariffe del giorno (origine dynaprice). Ritorna quante."""
    n = 0
    for tipo_id, d, prezzo in prezzi:
        t = db.session.get(TipoCamera, int(tipo_id))
        if not t or not prezzo or float(prezzo) <= 0:
            continue
        d = date.fromisoformat(d) if isinstance(d, str) else d
        r = TariffaGiorno.query.filter_by(tipo_id=t.id, data=d).first() or TariffaGiorno(tipo_id=t.id, data=d)
        r.prezzo, r.origine = round(float(prezzo), 2), 'dynaprice'
        db.session.add(r)
        n += 1
    return n


def torna_al_listino(tipo_id, da, a):
    q = TariffaGiorno.query.filter(TariffaGiorno.data >= da, TariffaGiorno.data <= a)
    if tipo_id:
        q = q.filter(TariffaGiorno.tipo_id == tipo_id)
    return q.delete(synchronize_session=False)


# ---------------- rischio di cancellazione ----------------

def rischio(p, oggi=None):
    """Probabilità stimata che la prenotazione venga cancellata o non si presenti, con i motivi.
    Modello a punteggio: ogni fattore aggiunge o toglie punti percentuali (pesi tipici del settore, modificabili)."""
    oggi = oggi or date.today()
    punti, motivi = 6.0, ['base 6%']
    anticipo = (p.arrivo - (p.creato_il.date() if p.creato_il else oggi)).days
    if anticipo > 90:
        punti += 14; motivi.append('prenotata con più di 90 giorni di anticipo +14')
    elif anticipo > 45:
        punti += 9; motivi.append('anticipo 45–90 giorni +9')
    elif anticipo > 20:
        punti += 4; motivi.append('anticipo 20–45 giorni +4')
    if p.canale and p.canale.tipo == 'ota':
        punti += 10; motivi.append('portale online +10')
    if p.piano and not p.piano.rimborsabile:
        punti -= 9; motivi.append('non rimborsabile −9')
    elif p.piano and (p.piano.giorni_cancellazione or 0) <= 1:
        punti += 5; motivi.append('cancellazione gratuita fino all\'ultimo +5')
    pagato = sum(x.importo for x in p.pagamenti)
    if pagato > 0:
        punti -= 7; motivi.append('caparra o acconto versati −7')
    if p.gruppo:
        punti += 6; motivi.append('gruppo +6')
    if p.notti >= 5:
        punti += 3; motivi.append('soggiorno lungo +3')
    if p.ospite and p.ospite.id and Prenotazione.query.filter(Prenotazione.ospite_id == p.ospite_id, Prenotazione.stato == 'partita').count():
        punti -= 6; motivi.append('ospite già venuto −6')
    if p.ospite and not (p.ospite.telefono or p.ospite.email):
        punti += 4; motivi.append('nessun contatto +4')
    prob = max(2.0, min(65.0, punti)) / 100
    return round(prob, 3), motivi


def cancellazioni_attese(da, a, oggi=None):
    oggi = oggi or date.today()
    attese = defaultdict(float)
    q = Prenotazione.query.filter(Prenotazione.stato.in_(('opzione', 'confermata')), Prenotazione.arrivo < a, Prenotazione.partenza > da,
                                  Prenotazione.arrivo >= oggi)
    for p in q.all():
        pr, _ = rischio(p, oggi)
        if p.stato == 'opzione':
            pr = max(pr, 0.35)
        for d in giorni(max(p.arrivo, da), min(p.partenza, a)):
            attese[d.isoformat()] += pr
    return attese


def a_rischio(oggi=None, limite=60):
    oggi = oggi or date.today()
    out = []
    for p in Prenotazione.query.filter(Prenotazione.stato.in_(('opzione', 'confermata')), Prenotazione.arrivo >= oggi).all():
        pr, motivi = rischio(p, oggi)
        out.append({'p': p, 'rischio': pr, 'motivi': motivi})
    out.sort(key=lambda x: -x['rischio'])
    return out[:limite]


# ---------------- simulatore dei canali ----------------

def simula_vendite(giorni_vendita=7, seme=None, oggi=None):
    """Simula le richieste che arrivano da portali, sito e telefono in `giorni_vendita` giorni, ai prezzi in vigore.
    La domanda segue stagione, fine settimana ed eventi; ogni richiesta accetta il prezzo con una probabilità che scende
    quando il prezzo sale sopra il listino di stagione (elasticità). Serve a vedere l'effetto delle decisioni di prezzo."""
    import random
    from hotel import Errore, crea_prenotazione, ospite_da_dati, prezzo_camera
    from models import PianoTariffario
    rnd = random.Random(seme)
    oggi = oggi or date.today()
    tipi = TipoCamera.query.order_by(TipoCamera.ordine).all()
    piani = PianoTariffario.query.filter_by(attivo=True).all()
    canali = Canale.query.filter_by(attivo=True).all()
    if not tipi or not piani or not canali:
        return {'errore': 'Servono tipologie, piani tariffari e canali attivi'}
    stagioni, eventi = Stagione.query.all(), Evento.query.filter(Evento.al >= oggi).all()
    cap = sum(len(camere_attive(t.id)) for t in tipi)
    pesi_canale = [(c, 40 if c.tipo == 'ota' else 20 if c.commissione_pct else 12) for c in canali]
    pesi_tipo = [(t, max(1, len(camere_attive(t.id)))) for t in tipi]
    esito = {'richieste': 0, 'prenotate': 0, 'perse_prezzo': 0, 'esaurito': 0, 'ricavo': 0.0, 'notti': 0, 'per_canale': defaultdict(int)}
    cognomi = ['Rossi', 'Smith', 'Müller', 'Dubois', 'García', 'Bianchi', 'Brown', 'Tanaka', 'Conti', 'Taylor', 'Fischer', 'Ricci']
    nomi = ['Anna', 'John', 'Marco', 'Sophie', 'Lucía', 'Paul', 'Elena', 'Kenji', 'Sara', 'Tom']
    for k in range(giorni_vendita):
        for lead in range(1, 120):
            d = oggi + timedelta(days=lead)
            coeff = 1.0
            for s in stagioni:
                if s.dal <= d <= s.al:
                    coeff = s.coefficiente or 1.0
                    break
            domanda = 0.45 + 0.35 * (coeff - 0.7) / 0.55 + (0.1 if d.weekday() in (4, 5) else 0)
            domanda += impatto_eventi(d, eventi)[0] / 2
            lam = max(0.0, domanda) * cap / 2.6 * (quota_prenotata(lead - k - 1) - quota_prenotata(lead - k)) * 1.15 if lead - k > 0 else 0
            n = int(lam) + (1 if rnd.random() < lam - int(lam) else 0)
            for _ in range(n):
                esito['richieste'] += 1
                tipo = rnd.choices([t for t, _ in pesi_tipo], weights=[w for _, w in pesi_tipo])[0]
                canale = rnd.choices([c for c, _ in pesi_canale], weights=[w for _, w in pesi_canale])[0]
                piano = rnd.choice(piani)
                notti = rnd.choices([1, 2, 3, 4, 5], weights=[18, 32, 26, 15, 9])[0]
                listino = tipo.prezzo_base * coeff
                rapporto = prezzo_camera(tipo, d, stagioni) / listino if listino else 1
                if rnd.random() > max(0.05, min(1.0, 1.6 - 0.75 * rapporto)):
                    esito['perse_prezzo'] += 1
                    continue
                adulti = 1 if tipo.posti_max == 1 else 2
                ospite = ospite_da_dati({'cognome': rnd.choice(cognomi), 'nome': rnd.choice(nomi),
                                         'email': f'simulato{rnd.randint(1, 10 ** 7)}@example.com'})
                try:
                    p = crea_prenotazione(ospite, tipo.id, piano.id, canale.id, d, d + timedelta(days=notti), adulti, 0, 'confermata',
                                          note='Simulata dal simulatore dei canali',
                                          rif_canale=str(rnd.randint(10 ** 9, 10 ** 10 - 1)) if canale.tipo == 'ota' else '')
                except Errore:
                    db.session.expunge(ospite) if ospite in db.session.new else None
                    esito['esaurito'] += 1
                    continue
                esito['prenotate'] += 1
                esito['ricavo'] += p.importo
                esito['notti'] += p.notti
                esito['per_canale'][canale.nome] += 1
    esito['per_canale'] = dict(esito['per_canale'])
    esito['ricavo'] = round(esito['ricavo'], 2)
    return esito
