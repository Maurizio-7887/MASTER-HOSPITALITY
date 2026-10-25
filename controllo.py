"""Controllo di gestione dell'albergo: indicatori (occupazione, ADR, RevPAR, TRevPAR, GOP, GOPPAR, CPOR), conto
economico per reparti secondo lo schema USALI, margine per canale e per prenotazione.

Ricavi al netto dell'IVA. Costi variabili: costo della camera venduta (pulizia, biancheria, cortesie, energia),
colazioni, costo degli extra, commissioni dei canali. Costi fissi: mensili per reparto, ripartiti sui giorni del periodo.
"""
from collections import defaultdict
from datetime import date, timedelta

from hotel import camere_attive, par
from models import (REPARTI, Addebito, CostoFisso, NotteVenduta, OCCUPANO, Prenotazione, db)


def _netto(lordo, iva):
    return (lordo or 0) / (1 + (iva or 0) / 100)


def analisi(da, a):
    """Periodo da `da` compreso ad `a` escluso."""
    giorni = (a - da).days
    iva = par('iva_alloggio')
    disponibili = len(camere_attive()) * giorni
    prenotazioni = {}
    notti = (db.session.query(NotteVenduta, Prenotazione)
             .join(Prenotazione, Prenotazione.id == NotteVenduta.prenotazione_id)
             .filter(Prenotazione.stato.in_(OCCUPANO), NotteVenduta.data >= da, NotteVenduta.data < a).all())
    per_p = defaultdict(lambda: {'notti': 0, 'camera': 0.0, 'colazione': 0.0, 'lordo': 0.0, 'costo_camera': 0.0, 'costo_colazione': 0.0,
                                 'extra': 0.0, 'costo_extra': 0.0, 'penale': 0.0})
    for n, p in notti:
        prenotazioni[p.id] = p
        x = per_p[p.id]
        x['notti'] += 1
        x['lordo'] += n.prezzo or 0
        x['camera'] += _netto((n.prezzo or 0) - (n.colazione or 0), iva)
        x['colazione'] += _netto(n.colazione or 0, iva)
        x['costo_camera'] += p.tipo.costo_variabile or 0
        if n.colazione:
            x['costo_colazione'] += p.persone * (p.piano.costo_colazione or 0)
    for ad, p in (db.session.query(Addebito, Prenotazione).join(Prenotazione, Prenotazione.id == Addebito.prenotazione_id)
                  .filter(Addebito.data >= da, Addebito.data < a, Prenotazione.stato.in_(OCCUPANO)).all()):
        prenotazioni[p.id] = p
        x = per_p[p.id]
        x['extra'] += _netto(ad.totale, ad.iva_pct)
        x['lordo'] += ad.totale
        x['costo_extra'] += (ad.costo or 0) * (ad.quantita or 0)
        x.setdefault('reparti', defaultdict(float))[ad.reparto] += _netto(ad.totale, ad.iva_pct)
    for p in Prenotazione.query.filter(Prenotazione.stato.in_(('cancellata', 'no_show')), Prenotazione.penale > 0,
                                       Prenotazione.arrivo >= da, Prenotazione.arrivo < a).all():
        prenotazioni[p.id] = p
        per_p[p.id]['penale'] += _netto(p.penale, iva)
        per_p[p.id]['lordo'] += p.penale
    # commissioni: sul prezzo pagato dall'ospite per soggiorno e penali
    for pid, x in per_p.items():
        p = prenotazioni[pid]
        lordo_soggiorno = sum(n.prezzo or 0 for n in p.notti_vendute if da <= n.data < a) if p.stato in OCCUPANO else p.penale
        x['commissione'] = (lordo_soggiorno or 0) * (p.canale.commissione_pct or 0) / 100
        x['ricavi'] = x['camera'] + x['colazione'] + x['extra'] + x['penale']
        x['costi_variabili'] = x['costo_camera'] + x['costo_colazione'] + x['costo_extra'] + x['commissione']
        x['margine'] = x['ricavi'] - x['costi_variabili']

    vendute = sum(x['notti'] for x in per_p.values())
    r_notti = sum(x['camera'] for x in per_p.values())               # solo pernottamenti: base di ADR e RevPAR
    r_camere = r_notti + sum(x['penale'] for x in per_p.values())
    r_colazione = sum(x['colazione'] for x in per_p.values())
    r_rep = defaultdict(float)
    for x in per_p.values():
        for k, v in (x.get('reparti') or {}).items():
            r_rep[k] += v
    r_fb = r_colazione + r_rep['colazione'] + r_rep['bar'] + r_rep['ristorante']
    r_altri = r_rep['servizi'] + r_rep['extra']
    ricavi = r_camere + r_fb + r_altri
    costi_fb_var = sum(x['costo_colazione'] for x in per_p.values())
    costo_extra = sum(x['costo_extra'] for x in per_p.values())
    costo_camere_var = sum(x['costo_camera'] for x in per_p.values())
    commissioni = sum(x['commissione'] for x in per_p.values())

    quota = giorni / (365 / 12)
    fissi = defaultdict(float)
    for c in CostoFisso.query.all():
        fissi[c.reparto] += (c.importo_mensile or 0) * quota

    # USALI: reparti operativi, costi non distribuiti, GOP, oneri fissi, EBITDA
    rep_camere = {'ricavi': r_camere, 'costi': costo_camere_var + commissioni + fissi['camere']}
    rep_fb = {'ricavi': r_fb, 'costi': costi_fb_var + fissi['fb'] + costo_extra * _quota_fb(r_rep)}
    rep_altri = {'ricavi': r_altri, 'costi': costo_extra * (1 - _quota_fb(r_rep))}
    for r in (rep_camere, rep_fb, rep_altri):
        r['utile'] = r['ricavi'] - r['costi']
        r['pct'] = r['utile'] / r['ricavi'] * 100 if r['ricavi'] else None
    reparti_utile = rep_camere['utile'] + rep_fb['utile'] + rep_altri['utile']
    non_distribuiti = {k: fissi[k] for k in ('amministrazione', 'commerciale', 'manutenzione', 'utenze')}
    gop = reparti_utile - sum(non_distribuiti.values())
    ebitda = gop - fissi['oneri']

    def div(x, y):
        return x / y if y else None

    kpi = {'giorni': giorni, 'camere': len(camere_attive()), 'disponibili': disponibili, 'vendute': vendute,
           'occupazione': div(vendute, disponibili), 'adr': div(r_notti, vendute), 'revpar': div(r_notti, disponibili), 'penali': r_camere - r_notti,
           'trevpar': div(ricavi, disponibili), 'gop': gop, 'goppar': div(gop, disponibili), 'gop_pct': div(gop, ricavi),
           'cpor': div(costo_camere_var + fissi['camere'], vendute), 'ricavi': ricavi, 'commissioni': commissioni,
           'commissioni_pct': div(commissioni, r_camere + r_colazione), 'ebitda': ebitda,
           'arrivi': len({p.id for p in prenotazioni.values() if da <= p.arrivo < a and p.stato in OCCUPANO}),
           'durata_media': div(vendute, len({p.id for p in prenotazioni.values() if p.stato in OCCUPANO})),
           'cancellate': Prenotazione.query.filter(Prenotazione.stato.in_(('cancellata', 'no_show')), Prenotazione.arrivo >= da,
                                                   Prenotazione.arrivo < a).count()}
    usali = {'camere': rep_camere, 'fb': rep_fb, 'altri': rep_altri, 'reparti_utile': reparti_utile,
             'non_distribuiti': non_distribuiti, 'gop': gop, 'oneri': fissi['oneri'], 'ebitda': ebitda, 'ricavi': ricavi,
             'dettaglio_camere': {'variabili': costo_camere_var, 'commissioni': commissioni, 'fissi': fissi['camere']},
             'dettaglio_fb': {'variabili': costi_fb_var, 'fissi': fissi['fb']}}

    canali = defaultdict(lambda: {'prenotazioni': 0, 'notti': 0, 'ricavi': 0.0, 'commissione': 0.0, 'costi': 0.0, 'margine': 0.0})
    for pid, x in per_p.items():
        p = prenotazioni[pid]
        c = canali[p.canale.nome]
        c['colore'] = p.canale.colore
        c['commissione_pct'] = p.canale.commissione_pct
        c['prenotazioni'] += 1
        c['notti'] += x['notti']
        c['ricavi'] += x['ricavi']
        c['commissione'] += x['commissione']
        c['costi'] += x['costi_variabili']
        c['margine'] += x['margine']
    for c in canali.values():
        c['adr'] = div(c['ricavi'], c['notti'])
        c['margine_notte'] = div(c['margine'], c['notti'])
        c['margine_pct'] = div(c['margine'], c['ricavi'])
        c['quota'] = div(c['notti'], vendute)
    elenco = []
    for pid, x in per_p.items():
        p = prenotazioni[pid]
        elenco.append({'p': p, **{k: v for k, v in x.items() if k != 'reparti'},
                       'margine_notte': div(x['margine'], x['notti']), 'margine_pct': div(x['margine'], x['ricavi'])})
    elenco.sort(key=lambda e: (e['margine_notte'] if e['margine_notte'] is not None else e['margine']))
    return {'da': da, 'a': a, 'kpi': kpi, 'usali': usali, 'canali': dict(sorted(canali.items(), key=lambda kv: -kv[1]['ricavi'])),
            'prenotazioni': elenco, 'reparti': REPARTI, 'fissi': dict(fissi)}


def _quota_fb(r_rep):
    fb = r_rep['colazione'] + r_rep['bar'] + r_rep['ristorante']
    tot = fb + r_rep['servizi'] + r_rep['extra']
    return fb / tot if tot else 0


def andamento(da, a):
    """Per giorno: occupazione, ADR, RevPAR (per il grafico)."""
    cam = len(camere_attive()) or 1
    iva = par('iva_alloggio')
    righe = (db.session.query(NotteVenduta.data, db.func.count(NotteVenduta.id), db.func.sum(NotteVenduta.prezzo - NotteVenduta.colazione))
             .join(Prenotazione, Prenotazione.id == NotteVenduta.prenotazione_id)
             .filter(Prenotazione.stato.in_(OCCUPANO), NotteVenduta.data >= da, NotteVenduta.data < a)
             .group_by(NotteVenduta.data).all())
    per = {d: (n, _netto(s, iva)) for d, n, s in righe}
    out = []
    d = da
    while d < a:
        n, s = per.get(d, (0, 0))
        out.append({'data': d.isoformat(), 'occ': n / cam, 'adr': s / n if n else None, 'revpar': s / cam})
        d += timedelta(days=1)
    return out


def periodo(args, oggi=None):
    """Legge da/a dalla richiesta; di default il mese in corso."""
    oggi = oggi or date.today()
    try:
        da = date.fromisoformat(args.get('da')) if args.get('da') else oggi.replace(day=1)
        a = date.fromisoformat(args.get('a')) + timedelta(days=1) if args.get('a') else None
    except ValueError:
        da, a = oggi.replace(day=1), None
    if not a:
        a = (da.replace(day=28) + timedelta(days=4)).replace(day=1)
    if a <= da:
        a = da + timedelta(days=1)
    if (a - da).days > 731:
        a = da + timedelta(days=731)
    return da, a
