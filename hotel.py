"""Logica dell'albergo: parametri, prezzi, disponibilità, preventivo, prenotazioni, conto, check-in e check-out.

Le regole sono scritte qui una volta sola: le usano le pagine, le API, la demo e il corso.
"""
from datetime import date, datetime, timedelta

from models import (OCCUPANO, Addebito, Alloggiato, Camera, Canale, Impostazione, NotteVenduta, Ospite, Pagamento,
                    PianoTariffario, Prenotazione, Restrizione, Stagione, TariffaGiorno, TipoCamera, db)

PARAMETRI = {   # chiave: (etichetta, default, tipo)
    'nome_struttura': ('Nome della struttura', 'Il mio hotel', str),
    'categoria': ('Categoria (stelle o tipo: B&B, affittacamere…)', '3 stelle', str),
    'comune': ('Comune', 'Firenze', str),
    'iva_alloggio': ('IVA su alloggio e colazione (%)', 10, float),
    'tassa_importo': ('Imposta di soggiorno per persona a notte (€)', 5.0, float),
    'tassa_max_notti': ('Imposta: massimo notti consecutive tassate', 7, int),
    'tassa_eta_esenti': ('Imposta: esenti fino a anni (compresi)', 11, int),
    'ora_checkin': ('Check-in dalle', '14:00', str),
    'ora_checkout': ('Check-out entro le', '11:00', str),
    'margine_minimo': ('DynaPrice: margine minimo sul prezzo netto (%)', 25, float),
    'max_overbooking': ('DynaPrice: camere massime in overbooking per notte', 1, int),
    'giorni_opzione': ('Giorni di validità di un\'opzione', 3, int),
}


class Errore(ValueError):
    pass


def par(chiave):
    etichetta, default, tipo = PARAMETRI[chiave]
    v = Impostazione.leggi(chiave)
    if v in (None, ''):
        return default
    try:
        return tipo(v)
    except (TypeError, ValueError):
        return default


def giorni(da, a):
    """Date da `da` compreso ad `a` escluso."""
    return [da + timedelta(days=i) for i in range((a - da).days)]


def _d(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        raise Errore('Data non valida')


# ---------------- prezzi ----------------

def coefficiente(data, stagioni=None):
    stagioni = stagioni if stagioni is not None else Stagione.query.all()
    for s in stagioni:
        if s.dal <= data <= s.al:
            return s.coefficiente or 1.0
    return 1.0


def prezzo_camera(tipo, data, stagioni=None, tariffe=None):
    """Prezzo della camera per la notte (piano base, occupazione base): tariffa del giorno o listino × stagione."""
    if tariffe is not None:
        t = tariffe.get((tipo.id, data))
    else:
        r = TariffaGiorno.query.filter_by(tipo_id=tipo.id, data=data).first()
        t = r.prezzo if r else None
    if t is not None:
        return round(t, 2)
    return round(tipo.prezzo_base * coefficiente(data, stagioni))


def prezzo_notte(tipo, piano, data, adulti, bambini=0, stagioni=None, tariffe=None):
    """(prezzo totale della notte, di cui colazione), IVA inclusa."""
    base = prezzo_camera(tipo, data, stagioni, tariffe) * (1 + (piano.variazione_pct or 0) / 100)
    extra = max(0, adulti + bambini - tipo.posti_base) * (tipo.supplemento_persona or 0)
    colaz = (adulti + bambini) * (piano.prezzo_colazione or 0) if piano.colazione else 0
    return round(base + extra + colaz, 2), round(colaz, 2)


# ---------------- disponibilità ----------------

def _sovrapposte(arrivo, partenza, escludi_id=None):
    q = Prenotazione.query.filter(Prenotazione.stato.in_(OCCUPANO), Prenotazione.arrivo < partenza, Prenotazione.partenza > arrivo)
    if escludi_id:
        q = q.filter(Prenotazione.id != escludi_id)
    return q.all()


def camere_attive(tipo_id=None):
    q = Camera.query.filter(Camera.attiva.is_(True), Camera.stato != 'fuori_servizio')
    if tipo_id:
        q = q.filter_by(tipo_id=tipo_id)
    return q.order_by(Camera.numero).all()


def disponibili_per_notte(tipo_id, arrivo, partenza, escludi_id=None):
    """{data: camere ancora vendibili} per la tipologia (le prenotazioni senza camera assegnata contano lo stesso)."""
    totale = len(camere_attive(tipo_id))
    occ = {d: 0 for d in giorni(arrivo, partenza)}
    for p in _sovrapposte(arrivo, partenza, escludi_id):
        if p.tipo_id != tipo_id:
            continue
        for d in giorni(max(p.arrivo, arrivo), min(p.partenza, partenza)):
            occ[d] += 1
    return {d: totale - n for d, n in occ.items()}


def camere_libere(tipo_id, arrivo, partenza, escludi_id=None):
    """Camere della tipologia (tutte se tipo_id è None) senza prenotazioni assegnate nel periodo."""
    prese = {p.camera_id for p in _sovrapposte(arrivo, partenza, escludi_id) if p.camera_id}
    return [c for c in camere_attive(tipo_id) if c.id not in prese]


def restrizioni(tipo_id, arrivo, partenza):
    """Messaggi di blocco per il soggiorno (soggiorno minimo, chiuso all'arrivo, chiuso alla vendita)."""
    msg = []
    notti = (partenza - arrivo).days
    for r in Restrizione.query.filter(Restrizione.data >= arrivo, Restrizione.data < partenza).all():
        if r.tipo_id and r.tipo_id != tipo_id:
            continue
        if r.chiuso:
            msg.append(f'{r.data.strftime("%d/%m")}: chiuso alla vendita')
        if r.data == arrivo and r.chiuso_arrivo:
            msg.append(f'{r.data.strftime("%d/%m")}: arrivi chiusi')
        if r.data == arrivo and (r.min_notti or 1) > notti:
            msg.append(f'arrivo il {r.data.strftime("%d/%m")}: soggiorno minimo {r.min_notti} notti')
    return msg


# ---------------- preventivo e prenotazione ----------------

def preventivo(tipo_id, piano_id, arrivo, partenza, adulti=2, bambini=0, escludi_id=None):
    arrivo, partenza = _d(arrivo), _d(partenza)
    tipo, piano = db.session.get(TipoCamera, tipo_id), db.session.get(PianoTariffario, piano_id)
    if not tipo or not piano:
        raise Errore('Scegli tipologia e piano tariffario')
    if partenza <= arrivo:
        raise Errore('La partenza deve essere dopo l\'arrivo')
    if (partenza - arrivo).days > 60:
        raise Errore('Soggiorno troppo lungo (massimo 60 notti)')
    if adulti < 1:
        raise Errore('Serve almeno un adulto')
    if adulti + bambini > tipo.posti_max:
        raise Errore(f'{tipo.nome}: massimo {tipo.posti_max} persone')
    stagioni = Stagione.query.all()
    notti = []
    for d in giorni(arrivo, partenza):
        p, c = prezzo_notte(tipo, piano, d, adulti, bambini, stagioni)
        notti.append({'data': d.isoformat(), 'prezzo': p, 'colazione': c})
    disp = disponibili_per_notte(tipo.id, arrivo, partenza, escludi_id)
    totale = round(sum(n['prezzo'] for n in notti), 2)
    return {'tipo': tipo.nome, 'piano': piano.nome, 'notti': notti, 'n_notti': len(notti), 'totale': totale,
            'caparra': round(totale * (piano.caparra_pct or 0) / 100, 2),
            'disponibile': min(disp.values()) > 0, 'minimo_disponibili': min(disp.values()),
            'restrizioni': restrizioni(tipo.id, arrivo, partenza),
            'tassa': tassa_stimata(adulti, len(notti)),
            'condizioni': condizioni(piano)}


def condizioni(piano):
    if not piano.rimborsabile:
        return 'Non rimborsabile: in caso di cancellazione o mancato arrivo è dovuto l\'intero soggiorno.'
    return (f'Cancellazione gratuita fino a {piano.giorni_cancellazione} giorni prima dell\'arrivo; poi, e in caso di mancato '
            f'arrivo, è dovuta la prima notte.')


def nuovo_codice(anno=None):
    anno = anno or date.today().year
    pref = f'H{anno}-'
    ultimo = (Prenotazione.query.filter(Prenotazione.codice.like(pref + '%')).order_by(Prenotazione.codice.desc()).first())
    n = int(ultimo.codice.split('-')[1]) + 1 if ultimo else 1
    return f'{pref}{n:05d}'


def crea_prenotazione(ospite, tipo_id, piano_id, canale_id, arrivo, partenza, adulti=2, bambini=0, stato='confermata',
                      camera_id=None, note='', gruppo='', rif_canale='', creato_il=None, forza=False, prezzo_totale=None):
    """Crea la prenotazione con le notti vendute. Senza `forza` controlla disponibilità e restrizioni."""
    arrivo, partenza = _d(arrivo), _d(partenza)
    if stato not in ('preventivo', 'opzione', 'confermata'):
        raise Errore('Stato iniziale non valido')
    if not db.session.get(Canale, canale_id):
        raise Errore('Scegli il canale di vendita')
    q = preventivo(tipo_id, piano_id, arrivo, partenza, adulti, bambini)
    if not forza and stato != 'preventivo':
        if not q['disponibile']:
            raise Errore('Nessuna camera disponibile di questa tipologia per tutte le notti')
        if q['restrizioni']:
            raise Errore('Restrizioni: ' + '; '.join(q['restrizioni']))
    if camera_id:
        cam = db.session.get(Camera, camera_id)
        if not cam or cam.tipo_id != tipo_id and not forza:
            raise Errore('La camera scelta non è della tipologia prenotata')
        if stato != 'preventivo' and cam not in camere_libere(None, arrivo, partenza):
            raise Errore(f'La camera {cam.numero} è già occupata in quelle date')
    p = Prenotazione(codice=nuovo_codice((creato_il or datetime.utcnow()).year), ospite=ospite, arrivo=arrivo, partenza=partenza,
                     adulti=adulti, bambini=bambini, tipo_id=tipo_id, piano_id=piano_id, canale_id=canale_id, stato=stato,
                     camera_id=camera_id or None, note=note[:500], gruppo=(gruppo or '')[:80], rif_canale=(rif_canale or '')[:40],
                     importo=q['totale'], caparra=q['caparra'], creato_il=creato_il or datetime.utcnow())
    if stato == 'opzione':
        p.opzione_fino = (creato_il.date() if creato_il else date.today()) + timedelta(days=par('giorni_opzione'))
    fattore = (prezzo_totale / q['totale']) if prezzo_totale and q['totale'] else 1
    for n in q['notti']:
        p.notti_vendute.append(NotteVenduta(data=_d(n['data']), prezzo=round(n['prezzo'] * fattore, 2),
                                            colazione=round(n['colazione'] * fattore, 2)))
    p.importo = round(sum(n.prezzo for n in p.notti_vendute), 2)
    db.session.add(p)
    db.session.flush()
    if stato in ('opzione', 'confermata') and not p.camera_id:
        assegna_camera(p)
    return p


def assegna_camera(p):
    """Prima camera libera della tipologia (preferendo quelle pulite). Ritorna la camera o None."""
    libere = camere_libere(p.tipo_id, p.arrivo, p.partenza, p.id)
    libere.sort(key=lambda c: (c.stato not in ('pulita', 'ispezionata'), c.numero))
    if libere:
        p.camera_id = libere[0].id
        p.camera = libere[0]
        return libere[0]
    return None


def sposta(p, camera_id=None, arrivo=None):
    """Planner: sposta la prenotazione su un'altra camera e/o un'altra data (stessa durata, stesso prezzo per notte)."""
    if p.stato in ('partita', 'cancellata', 'no_show'):
        raise Errore('Prenotazione chiusa: non si può spostare')
    cam = db.session.get(Camera, camera_id) if camera_id else p.camera
    nuovo_arrivo = _d(arrivo) if arrivo else p.arrivo
    if p.stato == 'in_casa' and nuovo_arrivo != p.arrivo:
        raise Errore('Ospite già in casa: si può cambiare solo la camera')
    nuova_partenza = nuovo_arrivo + timedelta(days=p.notti)
    tipo_dest = cam.tipo_id if cam else p.tipo_id
    if cam and cam not in camere_libere(None, nuovo_arrivo, nuova_partenza, p.id):
        raise Errore(f'La camera {cam.numero} è occupata in quelle date')
    if nuovo_arrivo != p.arrivo or tipo_dest != p.tipo_id:
        disp = disponibili_per_notte(tipo_dest, nuovo_arrivo, nuova_partenza, p.id)
        if min(disp.values()) <= 0:
            raise Errore('Nessuna disponibilità di quella tipologia nelle date scelte')
    if tipo_dest != p.tipo_id:   # cambio camera su un'altra tipologia (upgrade): il prezzo resta quello venduto
        p.tipo = cam.tipo
        p.note = ((p.note or '') + f' [spostata su {cam.tipo.nome}]').strip()[:500]
    if nuovo_arrivo != p.arrivo:
        delta = nuovo_arrivo - p.arrivo
        for n in p.notti_vendute:
            n.data = n.data + delta
        p.arrivo, p.partenza = nuovo_arrivo, nuova_partenza
    if cam:
        p.camera_id = cam.id
        p.camera = cam
    return p


# ---------------- conto ----------------

def eta(nascita, al):
    if not nascita:
        return None
    return al.year - nascita.year - ((al.month, al.day) < (nascita.month, nascita.day))


def tassa_stimata(adulti, notti):
    return round(adulti * min(notti, par('tassa_max_notti')) * par('tassa_importo'), 2)


def tassa(p):
    """Imposta di soggiorno: per ogni alloggiato non esente, fino al massimo di notti. Senza alloggiati: sugli adulti."""
    notti = min(p.notti, par('tassa_max_notti'))
    if p.alloggiati:
        limite = par('tassa_eta_esenti')
        tassati = 0
        for a in p.alloggiati:
            e = eta(a.data_nascita, p.arrivo)
            if a.esente_tassa or (e is not None and e <= limite):
                continue
            tassati += 1
    else:
        tassati = p.adulti
    return {'persone': tassati, 'notti': notti, 'importo': par('tassa_importo'), 'totale': round(tassati * notti * par('tassa_importo'), 2)}


def conto(p):
    righe = []
    if p.stato in ('cancellata', 'no_show'):
        if p.penale:
            righe.append({'data': (p.cancellata_il.date() if p.cancellata_il else p.arrivo).isoformat(), 'descrizione': 'Penale di cancellazione' if p.stato == 'cancellata' else 'Penale per mancato arrivo',
                          'quantita': 1, 'prezzo': p.penale, 'totale': p.penale, 'iva': par('iva_alloggio'), 'tipo': 'penale'})
    else:
        for n in p.notti_vendute:
            righe.append({'data': n.data.isoformat(), 'descrizione': f'Pernottamento {p.tipo.nome}' + (' con colazione' if n.colazione else ''),
                          'quantita': 1, 'prezzo': n.prezzo, 'totale': n.prezzo, 'iva': par('iva_alloggio'), 'tipo': 'notte'})
    for a in p.addebiti:
        righe.append({'id': a.id, 'data': a.data.isoformat(), 'descrizione': a.descrizione, 'quantita': a.quantita, 'prezzo': a.prezzo,
                      'totale': a.totale, 'iva': a.iva_pct, 'tipo': 'addebito'})
    t = tassa(p) if p.stato not in ('cancellata', 'no_show', 'preventivo') else {'totale': 0, 'persone': 0, 'notti': 0, 'importo': 0}
    imponibile = sum(r['totale'] for r in righe)
    iva = sum(r['totale'] - r['totale'] / (1 + (r['iva'] or 0) / 100) for r in righe)
    totale = round(imponibile + t['totale'], 2)
    pagato = round(sum(x.importo for x in p.pagamenti), 2)
    return {'righe': righe, 'tassa': t, 'servizi': round(imponibile, 2), 'iva': round(iva, 2), 'totale': totale,
            'pagato': pagato, 'saldo': round(totale - pagato, 2)}


# ---------------- ciclo di vita ----------------

def conferma(p):
    if p.stato not in ('preventivo', 'opzione'):
        raise Errore('Solo preventivi e opzioni si possono confermare')
    if p.stato == 'preventivo':
        disp = disponibili_per_notte(p.tipo_id, p.arrivo, p.partenza, p.id)
        if min(disp.values()) <= 0:
            raise Errore('Non c\'è più disponibilità per queste date')
    p.stato, p.opzione_fino = 'confermata', None
    if not p.camera_id:
        assegna_camera(p)


def penale_cancellazione(p, il=None):
    il = il or date.today()
    if not p.piano.rimborsabile:
        return p.importo
    if (p.arrivo - il).days >= (p.piano.giorni_cancellazione or 0):
        return 0.0
    return p.notti_vendute[0].prezzo if p.notti_vendute else 0.0


def cancella(p, penale=None, il=None):
    if p.stato not in ('preventivo', 'opzione', 'confermata'):
        raise Errore('Questa prenotazione non si può cancellare')
    if p.stato in ('preventivo', 'opzione'):
        penale = 0
    p.penale = round(penale_cancellazione(p, il) if penale is None else float(penale), 2)
    p.stato = 'cancellata'
    p.cancellata_il = datetime.combine(il, datetime.min.time()) if il else datetime.utcnow()
    p.camera_id = None


def no_show(p):
    if p.stato != 'confermata':
        raise Errore('Solo una prenotazione confermata può diventare no show')
    if p.arrivo > date.today():
        raise Errore('Il no show si registra dal giorno di arrivo')
    p.penale = round(p.importo if not p.piano.rimborsabile else (p.notti_vendute[0].prezzo if p.notti_vendute else 0), 2)
    p.stato = 'no_show'
    p.cancellata_il = datetime.utcnow()
    p.camera_id = None


def check_in(p, oggi=None):
    oggi = oggi or date.today()
    if p.stato != 'confermata':
        raise Errore('Il check-in si fa su una prenotazione confermata')
    if p.arrivo > oggi:
        raise Errore(f'L\'arrivo è previsto il {p.arrivo.strftime("%d/%m/%Y")}')
    if not p.camera_id:
        raise Errore('Assegna una camera prima del check-in')
    if not p.alloggiati:
        raise Errore('Registra gli alloggiati (dati del documento) prima del check-in')
    if len(p.alloggiati) < p.persone:
        raise Errore(f'Registrati {len(p.alloggiati)} alloggiati su {p.persone} persone')
    p.stato, p.checkin_il = 'in_casa', datetime.utcnow()


def check_out(p, forza=False):
    if p.stato != 'in_casa':
        raise Errore('Il check-out si fa su un ospite in casa')
    c = conto(p)
    if c['saldo'] > 0.009 and not forza:
        raise Errore(f'Il conto non è saldato: restano {c["saldo"]:.2f} €')
    p.stato, p.checkout_il = 'partita', datetime.utcnow()
    if p.camera:
        p.camera.stato = 'sporca'


def scadi_opzioni(oggi=None):
    """Le opzioni oltre la scadenza tornano disponibili (cancellate senza penale)."""
    oggi = oggi or date.today()
    n = 0
    for p in Prenotazione.query.filter(Prenotazione.stato == 'opzione', Prenotazione.opzione_fino < oggi).all():
        p.stato, p.cancellata_il, p.camera_id, p.penale = 'cancellata', datetime.utcnow(), None, 0
        p.note = ((p.note or '') + ' [opzione scaduta]').strip()[:500]
        n += 1
    return n


def ospite_da_dati(d):
    """Trova l'ospite per email (se c'è) o lo crea."""
    email = (d.get('email') or '').strip().lower()
    o = Ospite.query.filter(db.func.lower(Ospite.email) == email).first() if email else None
    if not o:
        cognome = (d.get('cognome') or '').strip()
        if not cognome:
            raise Errore('Indica il cognome dell\'ospite')
        o = Ospite(cognome=cognome[:60], nome=(d.get('nome') or '').strip()[:60], email=email[:200],
                   telefono=(d.get('telefono') or '').strip()[:40], paese=(d.get('paese') or '').strip()[:60],
                   lingua=(d.get('lingua') or 'it')[:20])
        if d.get('consenso_marketing'):
            o.consenso_marketing, o.consenso_il = True, datetime.utcnow()
        db.session.add(o)
    return o


def alloggiato_da_ospite(p, o, tipo=None):
    return Alloggiato(prenotazione_id=p.id, tipo=tipo or ('16' if p.persone == 1 else '17'), cognome=o.cognome, nome=o.nome or '-',
                      sesso=o.sesso, data_nascita=o.data_nascita, luogo_nascita=o.luogo_nascita, provincia_nascita=o.provincia_nascita,
                      stato_nascita=o.stato_nascita, cittadinanza=o.cittadinanza, tipo_documento=o.tipo_documento,
                      numero_documento=o.numero_documento, luogo_rilascio=o.luogo_rilascio)


def registra_pagamento(p, importo, metodo='carta', nota='', il=None):
    importo = round(float(importo), 2)
    if abs(importo) < 0.01:
        raise Errore('Importo non valido')
    pg = Pagamento(prenotazione_id=p.id, data=il or date.today(), importo=importo, metodo=metodo if metodo else 'carta', nota=nota[:120])
    p.pagamenti.append(pg)
    return pg


def aggiungi_addebito(p, descrizione, prezzo, quantita=1, reparto='extra', iva=10, costo=0, il=None):
    if p.stato not in ('confermata', 'in_casa', 'partita'):
        raise Errore('Gli addebiti si registrano su prenotazioni confermate o in casa')
    if not descrizione:
        raise Errore('Scrivi la descrizione')
    a = Addebito(prenotazione_id=p.id, data=il or date.today(), descrizione=descrizione[:120], prezzo=round(float(prezzo), 2),
                 quantita=float(quantita or 1), reparto=reparto, iva_pct=float(iva), costo=float(costo or 0))
    p.addebiti.append(a)
    return a


def modifica(p, tipo_id, piano_id, arrivo, partenza, adulti, bambini, forza=False):
    """Cambia date, persone, tipologia o piano: ricalcola le notti al prezzo di oggi e controlla la disponibilità."""
    if p.stato not in ('preventivo', 'opzione', 'confermata'):
        raise Errore('Si modificano solo preventivi, opzioni e prenotazioni confermate (per gli ospiti in casa: cambia camera dal planner)')
    q = preventivo(tipo_id, piano_id, arrivo, partenza, adulti, bambini, escludi_id=p.id)
    if p.stato != 'preventivo' and not forza and not q['disponibile']:
        raise Errore('Nessuna disponibilità per le nuove condizioni')
    p.notti_vendute.clear()
    db.session.flush()
    for n in q['notti']:
        p.notti_vendute.append(NotteVenduta(data=_d(n['data']), prezzo=n['prezzo'], colazione=n['colazione']))
    p.tipo_id, p.piano_id = tipo_id, piano_id
    p.tipo, p.piano = db.session.get(TipoCamera, tipo_id), db.session.get(PianoTariffario, piano_id)
    p.arrivo, p.partenza, p.adulti, p.bambini = _d(arrivo), _d(partenza), adulti, bambini
    p.importo, p.caparra = q['totale'], q['caparra']
    if p.camera_id and (p.camera.tipo_id != tipo_id or p.camera not in camere_libere(None, p.arrivo, p.partenza, p.id)):
        p.camera_id, p.camera = None, None
    if p.stato != 'preventivo' and not p.camera_id:
        assegna_camera(p)
    return p


def occupazione_notte(d):
    tot = len(camere_attive())
    n = (db.session.query(db.func.count(NotteVenduta.id)).join(Prenotazione, Prenotazione.id == NotteVenduta.prenotazione_id)
         .filter(Prenotazione.stato.in_(OCCUPANO), NotteVenduta.data == d).scalar() or 0)
    return n, tot
