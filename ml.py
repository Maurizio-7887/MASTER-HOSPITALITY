"""DynaPrice ML: modelli addestrati sullo storico dell'albergo, spiegabili e leggeri (solo numpy).

1. Previsione della domanda (pickup): occupazione finale di una notte stimata da quanto è già prenotato a N giorni
   dall'arrivo, giorno della settimana, stagione ed eventi (regressione ridge). Si confronta con la curva standard di
   DynaPrice sulle notti tenute da parte per la verifica (MAE in camere).
2. Probabilità di disdetta e no show: regressione logistica sulle prenotazioni concluse (come i modelli sul dataset
   «Hotel Booking Demand»): anticipo, canale, piano, notti, persone, prezzo, provenienza. Si misura con l'AUC e si
   confronta con il punteggio a regole di DynaPrice.
3. Elasticità al prezzo e prezzo ottimo: dalla variazione dei prezzi nello storico, se c'è abbastanza variazione;
   altrimenti dalla curva di risposta del mercato del simulatore (dichiarata come tale). Il prezzo ottimo massimizza il
   margine atteso della notte (prezzo netto meno costo della camera venduta) senza scendere sotto la soglia di margine.
4. Prezzi dei concorrenti (rate shopping): posizione del nostro prezzo rispetto alla mediana del compset.
5. Agente che impara il prezzo (bandit di Thompson): prova livelli di prezzo su un mercato simulato e converge sul
   migliore, come gli esempi di pricing con reinforcement learning.
"""
import math
import random
from collections import defaultdict
from datetime import date, timedelta

import numpy as np

import hotel as h
from dynaprice import impatto_eventi, quota_prenotata, soglia_prezzo
from models import OCCUPANO, Canale, Evento, NotteVenduta, Prenotazione, Stagione, TariffaConcorrente, TipoCamera, db

LEADS = (1, 3, 7, 14, 21, 30, 45, 60, 90)


# ---------------- strumenti ----------------

def ridge(X, y, alfa=1.0):
    n = X.shape[1]
    reg = alfa * np.eye(n)
    reg[0, 0] = 0
    return np.linalg.solve(X.T @ X + reg, X.T @ y)


def logistica(X, y, alfa=0.5, iterazioni=800, passo=0.3):
    w = np.zeros(X.shape[1])
    for _ in range(iterazioni):
        p = 1 / (1 + np.exp(-(X @ w)))
        grad = X.T @ (p - y) / len(y) + alfa * np.r_[0, w[1:]] / len(y)
        w -= passo * grad
    return w


def auc(y, s):
    """Area sotto la curva ROC: probabilità che una disdetta abbia punteggio più alto di un soggiorno regolare."""
    y, s = np.asarray(y), np.asarray(s)
    pos, neg = s[y == 1], s[y == 0]
    if not len(pos) or not len(neg):
        return None
    ordine = np.argsort(np.r_[pos, neg])
    rank = np.empty(len(ordine))
    rank[ordine] = np.arange(1, len(ordine) + 1)
    return float((rank[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def _coeff_stagione(d, stagioni):
    for s in stagioni:
        if s.dal <= d <= s.al:
            return s.coefficiente or 1.0
    return 1.0


# ---------------- 1. domanda ----------------

def _prenotazioni_storiche():
    return Prenotazione.query.filter(Prenotazione.stato.in_(OCCUPANO + ('cancellata', 'no_show'))).all()


def _feature_domanda(d, lead, otb_occ, stagioni, eventi):
    x = [1.0, otb_occ, lead / 90, otb_occ * (1 - quota_prenotata(lead)), 1 - quota_prenotata(lead)]
    x += [1.0 if d.weekday() == k else 0.0 for k in range(6)]
    x += [math.sin(2 * math.pi * d.month / 12), math.cos(2 * math.pi * d.month / 12), _coeff_stagione(d, stagioni) - 1,
          impatto_eventi(d, eventi)[0]]
    return x


def dati_domanda(oggi=None):
    """Campioni (notte passata, anticipo): occupazione prenotata a quell'anticipo → occupazione finale."""
    oggi = oggi or date.today()
    cap = len(h.camere_attive())
    if not cap:
        return [], cap
    pren = _prenotazioni_storiche()
    stagioni, eventi = Stagione.query.all(), Evento.query.all()
    per_notte = defaultdict(list)
    for p in pren:
        creato = p.creato_il.date() if p.creato_il else p.arrivo
        canc = p.cancellata_il.date() if p.cancellata_il else None
        for d in h.giorni(p.arrivo, min(p.partenza, oggi)):
            per_notte[d].append((creato, canc, p.stato in OCCUPANO))
    campioni = []
    for d in sorted(per_notte):
        finale = sum(1 for _, _, ok in per_notte[d] if ok) / cap
        for lead in LEADS:
            t = d - timedelta(days=lead)
            otb = sum(1 for creato, canc, _ in per_notte[d] if creato <= t and (canc is None or canc > t)) / cap
            campioni.append((d, lead, otb, finale, _feature_domanda(d, lead, otb, stagioni, eventi)))
    return campioni, cap


def modello_domanda(oggi=None):
    campioni, cap = dati_domanda(oggi)
    date_ = sorted({c[0] for c in campioni})
    if len(date_) < 60:
        return {'pronto': False, 'motivo': f'Servono almeno 60 notti di storico (ora {len(date_)})'}
    taglio = date_[int(len(date_) * 0.75)]
    tr = [c for c in campioni if c[0] < taglio]
    te = [c for c in campioni if c[0] >= taglio]
    X, y = np.array([c[4] for c in tr]), np.array([c[3] for c in tr])
    w = ridge(X, y, 2.0)
    Xt, yt = np.array([c[4] for c in te]), np.array([c[3] for c in te])
    pred = np.clip(Xt @ w, 0, 1)
    # base di confronto: la curva standard di DynaPrice (prenotato ÷ quota tipica a quell'anticipo)
    base = np.array([min(1.0, c[2] / max(quota_prenotata(c[1]), 0.05)) for c in te])
    per_lead = {}
    for lead in LEADS:
        idx = [i for i, c in enumerate(te) if c[1] == lead]
        if idx:
            per_lead[lead] = {'ml': float(np.mean(np.abs(pred[idx] - yt[idx])) * cap),
                              'curva': float(np.mean(np.abs(base[idx] - yt[idx])) * cap)}
    return {'pronto': True, 'w': w, 'cap': cap, 'notti_addestramento': len({c[0] for c in tr}), 'notti_verifica': len({c[0] for c in te}),
            'mae_ml': float(np.mean(np.abs(pred - yt)) * cap), 'mae_curva': float(np.mean(np.abs(base - yt)) * cap), 'per_lead': per_lead,
            'dal_verifica': taglio}


def previsione(modello, da, giorni=30, oggi=None):
    """Occupazione prevista dal modello per le prossime notti, con quanto è già prenotato oggi."""
    oggi = oggi or date.today()
    if not modello.get('pronto'):
        return []
    cap = modello['cap']
    stagioni, eventi = Stagione.query.all(), Evento.query.all()
    otb = defaultdict(int)
    a = da + timedelta(days=giorni)
    for n in (NotteVenduta.query.join(Prenotazione, Prenotazione.id == NotteVenduta.prenotazione_id)
              .filter(Prenotazione.stato.in_(OCCUPANO), NotteVenduta.data >= da, NotteVenduta.data < a).all()):
        otb[n.data] += 1
    out = []
    for d in h.giorni(da, a):
        lead = max(1, (d - oggi).days)
        occ = otb[d] / cap
        x = np.array(_feature_domanda(d, lead, occ, stagioni, eventi))
        prev = float(np.clip(x @ modello['w'], occ, 1))
        out.append({'data': d, 'lead': lead, 'prenotate': otb[d], 'occ_otb': occ, 'prevista': prev,
                    'camere_previste': round(prev * cap, 1), 'pickup_atteso': round(max(0.0, prev - occ) * cap, 1)})
    return out


# ---------------- 2. disdette ----------------

NOMI_DISDETTA = ['costante', 'anticipo (log giorni)', 'portale online', 'agenzia', 'non rimborsabile', 'cancellazione gratuita fino all\'ultimo',
                 'notti', 'persone', 'gruppo', 'prezzo medio a notte (centinaia di €)', 'ospite straniero', 'arrivo nel fine settimana', 'ospite già venuto']


def _x_disdetta(p, venuti):
    creato = p.creato_il.date() if p.creato_il else p.arrivo
    anticipo = max(0, (p.arrivo - creato).days)
    c, pi = p.canale, p.piano
    paese = (p.ospite.paese or '').upper() if p.ospite else ''
    return [1.0, math.log1p(anticipo), 1.0 if c and c.tipo == 'ota' else 0.0, 1.0 if c and c.tipo == 'agenzia' else 0.0,
            0.0 if pi is None or pi.rimborsabile else 1.0, 1.0 if pi and pi.rimborsabile and (pi.giorni_cancellazione or 0) <= 1 else 0.0,
            min(p.notti, 14) / 7, p.persone / 2, 1.0 if p.gruppo else 0.0, (p.importo / max(1, p.notti)) / 100,
            0.0 if paese in ('', 'IT', 'ITALIA') else 1.0, 1.0 if p.arrivo.weekday() in (4, 5) else 0.0,
            1.0 if p.ospite_id in venuti else 0.0]


def modello_disdette(oggi=None):
    oggi = oggi or date.today()
    concluse = (Prenotazione.query.filter(Prenotazione.arrivo < oggi, Prenotazione.stato.in_(('partita', 'cancellata', 'no_show', 'in_casa')))
                .order_by(Prenotazione.creato_il).all())
    if len(concluse) < 80:
        return {'pronto': False, 'motivo': f'Servono almeno 80 prenotazioni concluse (ora {len(concluse)})'}
    venuti = set()
    X, y = [], []
    for p in concluse:      # «già venuto» solo con i soggiorni precedenti: niente informazioni dal futuro
        X.append(_x_disdetta(p, venuti))
        y.append(1.0 if p.stato in ('cancellata', 'no_show') else 0.0)
        if p.stato == 'partita':
            venuti.add(p.ospite_id)
    X, y = np.array(X), np.array(y)
    taglio = int(len(y) * 0.75)
    mu, sd = X[:taglio, 1:].mean(0), X[:taglio, 1:].std(0) + 1e-9
    Z = np.c_[np.ones(len(X)), (X[:, 1:] - mu) / sd]
    w = logistica(Z[:taglio], y[:taglio])
    s = 1 / (1 + np.exp(-(Z[taglio:] @ w)))
    from dynaprice import rischio
    regole = [rischio(p, p.creato_il.date() if p.creato_il else p.arrivo)[0] for p in concluse[taglio:]]
    pesi = sorted(zip(NOMI_DISDETTA[1:], w[1:]), key=lambda x: -abs(x[1]))
    return {'pronto': True, 'w': w, 'mu': mu, 'sd': sd, 'n': len(y), 'tasso': float(y.mean()), 'auc_ml': auc(y[taglio:], s),
            'auc_regole': auc(y[taglio:], regole), 'pesi': [(n, float(v)) for n, v in pesi],
            'accuratezza': float(np.mean((s > 0.5) == (y[taglio:] == 1)))}


def prob_disdetta(modello, p):
    venuti = {x.ospite_id for x in Prenotazione.query.filter_by(stato='partita', ospite_id=p.ospite_id).all()}
    x = np.array(_x_disdetta(p, venuti))
    z = np.r_[1.0, (x[1:] - modello['mu']) / modello['sd']]
    return float(1 / (1 + math.exp(-(z @ modello['w']))))


def disdette_future(modello, oggi=None, limite=40):
    oggi = oggi or date.today()
    if not modello.get('pronto'):
        return []
    out = [{'p': p, 'prob': prob_disdetta(modello, p)} for p in
           Prenotazione.query.filter(Prenotazione.stato.in_(('opzione', 'confermata')), Prenotazione.arrivo >= oggi).all()]
    out.sort(key=lambda x: -x['prob'])
    return out[:limite]


# ---------------- 3. elasticità e prezzo ottimo ----------------

def risposta_mercato(rapporto):
    """Probabilità che un ospite accetti un prezzo pari a `rapporto` × listino di stagione: la stessa curva del simulatore
    dei canali (DynaPrice), usata quando lo storico non ha abbastanza variazioni di prezzo."""
    return max(0.05, min(1.0, 1.6 - 0.75 * rapporto))


def elasticita(oggi=None):
    """Elasticità della domanda al prezzo: dallo storico se i prezzi sono variati abbastanza, altrimenti dal mercato simulato."""
    oggi = oggi or date.today()
    stagioni = Stagione.query.all()
    tipi = {t.id: t for t in TipoCamera.query.all()}
    cap = defaultdict(int)
    for c in h.camere_attive():
        cap[c.tipo_id] += 1
    righe = (db.session.query(NotteVenduta.data, NotteVenduta.prezzo, NotteVenduta.colazione, Prenotazione.tipo_id)
             .join(Prenotazione, Prenotazione.id == NotteVenduta.prenotazione_id)
             .filter(Prenotazione.stato.in_(OCCUPANO), NotteVenduta.data < oggi, NotteVenduta.data >= oggi - timedelta(days=365)).all())
    per = defaultdict(lambda: [0, 0.0])
    for d, prezzo, col, tid in righe:
        per[(d, tid)][0] += 1
        per[(d, tid)][1] += (prezzo or 0) - (col or 0)
    X, y = [], []
    for (d, tid), (n, ric) in per.items():
        t = tipi.get(tid)
        if not t or not cap[tid]:
            continue
        listino = t.prezzo_base * _coeff_stagione(d, stagioni)
        r = (ric / n) / listino if listino else 1
        X.append([1.0, math.log(max(r, 0.3))] + [1.0 if d.weekday() == k else 0.0 for k in range(6)] + [_coeff_stagione(d, stagioni) - 1])
        y.append(math.log(max(n / cap[tid], 0.02)))
    if len(X) >= 120:
        X, y = np.array(X), np.array(y)
        variazione = float(np.std(X[:, 1]))
        if variazione >= 0.05:
            w = ridge(X, y, 1.0)
            return {'valore': round(float(w[1]), 2), 'fonte': 'storico', 'variazione_prezzi': variazione, 'campioni': len(y)}
        motivo = f'nello storico i prezzi variano poco (±{variazione * 100:.1f}% sul listino)'
    else:
        motivo = 'storico troppo corto'
    e = (math.log(risposta_mercato(1.05)) - math.log(risposta_mercato(0.95))) / (math.log(1.05) - math.log(0.95))
    return {'valore': round(e, 2), 'fonte': 'mercato simulato', 'motivo': motivo}


def prezzo_ottimo(tipo, d, pickup_atteso, libere, ela=None, comm_media=None, stagioni=None):
    """Prezzo che massimizza il margine atteso della notte per la tipologia: (prezzo netto − costo camera) × camere vendute,
    con le camere vendute = min(libere, domanda attesa al listino × risposta al prezzo). Mai sotto la soglia di margine."""
    stagioni = stagioni if stagioni is not None else Stagione.query.all()
    listino = tipo.prezzo_base * _coeff_stagione(d, stagioni)
    iva = h.par('iva_alloggio') / 100
    comm = comm_media if comm_media is not None else 0.12
    soglia = soglia_prezzo(tipo)
    curva = []
    for k in range(70, 161, 2):
        r = k / 100
        prezzo = round(listino * r)
        if tipo.prezzo_massimo and prezzo > tipo.prezzo_massimo:
            continue
        risposta = (r ** ela['valore']) if ela and ela['fonte'] == 'storico' else risposta_mercato(r) / risposta_mercato(1.0)
        vendute = min(libere, pickup_atteso * risposta)
        netto = prezzo / (1 + iva) * (1 - comm)
        curva.append({'prezzo': prezzo, 'vendute': round(vendute, 2), 'margine': round(vendute * (netto - (tipo.costo_variabile or 0)), 2),
                      'ricavo': round(vendute * prezzo, 2), 'sotto_soglia': prezzo < soglia})
    valide = [c for c in curva if not c['sotto_soglia']] or curva
    best = max(valide, key=lambda c: c['margine'])
    al_listino = min(curva, key=lambda c: abs(c['prezzo'] - listino))
    return {'listino': round(listino), 'ottimo': best['prezzo'], 'vendute': best['vendute'], 'margine': best['margine'],
            'margine_listino': al_listino['margine'], 'soglia': soglia, 'curva': curva}


def commissione_media():
    """Commissione media pesata sui canali delle prenotazioni degli ultimi 12 mesi."""
    q = (db.session.query(Canale.commissione_pct, db.func.count(Prenotazione.id)).join(Prenotazione, Prenotazione.canale_id == Canale.id)
         .filter(Prenotazione.stato.in_(OCCUPANO)).group_by(Canale.commissione_pct).all())
    n = sum(c for _, c in q)
    return (sum((p or 0) * c for p, c in q) / n / 100) if n else 0.12


def raccomandazioni(modello, giorni=21, oggi=None):
    """Per le prossime notti: previsione del modello ripartita sulle tipologie e prezzo ottimo di ciascuna."""
    oggi = oggi or date.today()
    prev = previsione(modello, oggi, giorni, oggi)
    if not prev:
        return [], None
    ela = elasticita(oggi)
    comm = commissione_media()
    stagioni = Stagione.query.all()
    tipi = TipoCamera.query.order_by(TipoCamera.ordine, TipoCamera.id).all()
    cap_tipo = {t.id: len(h.camere_attive(t.id)) for t in tipi}
    tot = sum(cap_tipo.values()) or 1
    otb = defaultdict(int)
    for n in (db.session.query(NotteVenduta.data, Prenotazione.tipo_id).join(Prenotazione, Prenotazione.id == NotteVenduta.prenotazione_id)
              .filter(Prenotazione.stato.in_(OCCUPANO), NotteVenduta.data >= oggi, NotteVenduta.data < oggi + timedelta(days=giorni)).all()):
        otb[(n[0], n[1])] += 1
    out = []
    for r in prev:
        celle = []
        for t in tipi:
            if not cap_tipo[t.id]:
                continue
            libere = max(0, cap_tipo[t.id] - otb[(r['data'], t.id)])
            pickup_t = r['pickup_atteso'] * cap_tipo[t.id] / tot
            po = prezzo_ottimo(t, r['data'], pickup_t, libere, ela, comm, stagioni)
            celle.append({'tipo': t, 'libere': libere, 'attuale': h.prezzo_camera(t, r['data'], stagioni), **{k: po[k] for k in ('listino', 'ottimo', 'margine', 'margine_listino', 'soglia')}})
        out.append(dict(r, celle=celle))
    return out, ela


# ---------------- 4. concorrenti ----------------

def compset(da, giorni=14):
    a = da + timedelta(days=giorni)
    tariffe = TariffaConcorrente.query.filter(TariffaConcorrente.data >= da, TariffaConcorrente.data < a).all()
    per = defaultdict(dict)
    for t in tariffe:
        per[t.data][t.concorrente] = t.prezzo
    nomi = sorted({t.concorrente for t in tariffe})
    tipo = TipoCamera.query.order_by(TipoCamera.ordine, TipoCamera.id).first()
    stagioni = Stagione.query.all()
    righe = []
    for d in h.giorni(da, a):
        prezzi = list(per[d].values())
        mediana = float(np.median(prezzi)) if prezzi else None
        nostro = h.prezzo_camera(tipo, d, stagioni) if tipo else None
        righe.append({'data': d, 'prezzi': per[d], 'mediana': mediana, 'nostro': nostro,
                      'indice': round(nostro / mediana * 100) if nostro and mediana else None})
    return {'nomi': nomi, 'righe': righe, 'tipo': tipo}


def importa_compset(testo):
    """Righe «concorrente;data;prezzo» (data gg/mm/aaaa o aaaa-mm-gg). Ritorna quante righe salvate."""
    n = 0
    for riga in (testo or '').splitlines():
        parti = [x.strip() for x in riga.replace('\t', ';').split(';')]
        if len(parti) < 3 or not parti[0]:
            continue
        try:
            d = date.fromisoformat(parti[1]) if '-' in parti[1] else date(int(parti[1][6:10]), int(parti[1][3:5]), int(parti[1][:2]))
            prezzo = float(parti[2].replace('€', '').replace('.', '').replace(',', '.')) if ',' in parti[2] else float(parti[2].replace('€', ''))
        except (ValueError, IndexError):
            continue
        t = TariffaConcorrente.query.filter_by(concorrente=parti[0][:80], data=d).first() or TariffaConcorrente(concorrente=parti[0][:80], data=d)
        t.prezzo, t.rilevato_il = prezzo, date.today()
        db.session.add(t)
        n += 1
    return n


# ---------------- 5. agente che impara il prezzo ----------------

def agente_prezzo(listino=120.0, costo=25.0, livelli=(0.8, 0.9, 1.0, 1.1, 1.2, 1.3), richieste=600, seme=1):
    """Bandit di Thompson: a ogni richiesta l'agente sceglie un livello di prezzo campionando la sua stima della probabilità
    di vendita (Beta), osserva se l'ospite compra e aggiorna la stima. Il «mercato» è quello del simulatore."""
    rnd = random.Random(seme)
    a = [1.0] * len(livelli)
    b = [1.0] * len(livelli)
    scelte = [0] * len(livelli)
    guadagno, storia = 0.0, []
    for i in range(1, richieste + 1):
        stime = [rnd.betavariate(a[k], b[k]) * (listino * livelli[k] - costo) for k in range(len(livelli))]
        k = max(range(len(livelli)), key=lambda j: stime[j])
        compra = rnd.random() < risposta_mercato(livelli[k])
        a[k] += compra
        b[k] += not compra
        scelte[k] += 1
        guadagno += (listino * livelli[k] - costo) if compra else 0
        if i % 25 == 0:
            storia.append({'richieste': i, 'margine_medio': round(guadagno / i, 2), 'livello': livelli[k]})
    vero = [risposta_mercato(l) * (listino * l - costo) for l in livelli]
    migliore = max(range(len(livelli)), key=lambda j: vero[j])
    scelto = max(range(len(livelli)), key=lambda j: scelte[j])
    return {'livelli': [{'rapporto': l, 'prezzo': round(listino * l), 'scelte': scelte[k], 'prob_stimata': round(a[k] / (a[k] + b[k]), 3),
                         'prob_vera': round(risposta_mercato(l), 3), 'margine_atteso': round(vero[k], 2)} for k, l in enumerate(livelli)],
            'migliore': round(listino * livelli[migliore]), 'scelto': round(listino * livelli[scelto]), 'storia': storia,
            'margine_medio': round(guadagno / richieste, 2), 'margine_listino': round(vero[livelli.index(1.0)] if 1.0 in livelli else 0, 2),
            'margine_ottimo': round(vero[migliore], 2), 'richieste': richieste}
