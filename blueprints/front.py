"""Front office: la giornata, il planner delle camere, prenotazioni, scheda e conto, ospiti, housekeeping,
alloggiati e imposta di soggiorno."""
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

import hotel as h
from models import (METODI, REP_ADDEBITO, STATI, Addebito, Alloggiato, Camera, Canale, OCCUPANO, Ospite, PianoTariffario,
                    Prenotazione, TipoCamera, db)

bp = Blueprint('front', __name__)


def _csrf():
    from accesso import _csrf_ok
    if not _csrf_ok():
        flash('Sessione scaduta: riprova', 'err')
        return False
    return True


def _data(v, default=None):
    try:
        return date.fromisoformat(v) if v else default
    except ValueError:
        return default


def _tutor():
    """Dopo ogni operazione il tutor del corso controlla se il passo è fatto."""
    try:
        from corso import controlla_in_silenzio
        controlla_in_silenzio()
    except Exception:   # noqa: BLE001 — il tutor non deve mai bloccare il lavoro
        db.session.rollback()


# ---------------- la giornata ----------------

@bp.route('/oggi')
def cruscotto():
    oggi = date.today()
    if h.scadi_opzioni(oggi):
        db.session.commit()
    arrivi = Prenotazione.query.filter(Prenotazione.arrivo == oggi, Prenotazione.stato == 'confermata').order_by(Prenotazione.id).all()
    partenze = Prenotazione.query.filter(Prenotazione.partenza <= oggi, Prenotazione.stato == 'in_casa').order_by(Prenotazione.partenza).all()
    in_casa = Prenotazione.query.filter(Prenotazione.stato == 'in_casa').count()
    mancati = Prenotazione.query.filter(Prenotazione.arrivo < oggi, Prenotazione.stato == 'confermata').all()
    opzioni = Prenotazione.query.filter(Prenotazione.stato == 'opzione').order_by(Prenotazione.opzione_fino).limit(10).all()
    preventivi = Prenotazione.query.filter(Prenotazione.stato == 'preventivo', Prenotazione.arrivo >= oggi).order_by(Prenotazione.creato_il.desc()).limit(10).all()
    sporche = Camera.query.filter_by(stato='sporca', attiva=True).count()
    venduto, totale = h.occupazione_notte(oggi)
    prossimi = []
    for i in range(14):
        d = oggi + timedelta(days=i)
        v, t = h.occupazione_notte(d)
        prossimi.append({'data': d, 'venduto': v, 'totale': t, 'occ': v / t if t else 0})
    from controllo import analisi
    mese = analisi(oggi.replace(day=1), oggi + timedelta(days=1))['kpi'] if totale else None
    nuove = Prenotazione.query.filter(Prenotazione.creato_il >= datetime.combine(oggi - timedelta(days=1), datetime.min.time()),
                                      Prenotazione.stato != 'preventivo').order_by(Prenotazione.creato_il.desc()).limit(8).all()
    return render_template('front/oggi.html', arrivi=arrivi, partenze=partenze, in_casa=in_casa, mancati=mancati, opzioni=opzioni,
                           preventivi=preventivi, sporche=sporche, venduto=venduto, totale=totale, prossimi=prossimi, mese=mese,
                           nuove=nuove, oggi_d=oggi, vuoto=not TipoCamera.query.first())


# ---------------- planner ----------------

@bp.route('/planner')
def planner():
    return render_template('front/planner.html', da=_data(request.args.get('da'), date.today() - timedelta(days=2)).isoformat())


@bp.route('/api/planner')
def api_planner():
    da = _data(request.args.get('da'), date.today() - timedelta(days=2))
    n = max(7, min(62, request.args.get('giorni', type=int) or 21))
    a = da + timedelta(days=n)
    tipi = TipoCamera.query.order_by(TipoCamera.ordine, TipoCamera.id).all()
    camere = Camera.query.filter_by(attiva=True).all()
    camere.sort(key=lambda c: (c.tipo.ordine, c.tipo_id, c.numero))
    pren = Prenotazione.query.filter(Prenotazione.stato.in_(OCCUPANO + ('preventivo',)), Prenotazione.arrivo < a,
                                     Prenotazione.partenza > da).all()
    occ = {d.isoformat(): 0 for d in h.giorni(da, a)}
    for p in pren:
        if p.stato in OCCUPANO:
            for d in h.giorni(max(p.arrivo, da), min(p.partenza, a)):
                occ[d.isoformat()] += 1
    return jsonify({'ok': True, 'da': da.isoformat(), 'giorni': n, 'oggi': date.today().isoformat(),
                    'tipi': [{'id': t.id, 'codice': t.codice, 'nome': t.nome} for t in tipi],
                    'camere': [{'id': c.id, 'numero': c.numero, 'tipo_id': c.tipo_id, 'tipo': c.tipo.codice, 'stato': c.stato} for c in camere],
                    'occupazione': occ, 'totale': len(h.camere_attive()),
                    'prenotazioni': [{'id': p.id, 'codice': p.codice, 'ospite': p.ospite.nominativo, 'camera_id': p.camera_id, 'tipo_id': p.tipo_id,
                                      'tipo': p.tipo.codice, 'arrivo': p.arrivo.isoformat(), 'partenza': p.partenza.isoformat(), 'stato': p.stato,
                                      'canale': p.canale.nome, 'colore': p.canale.colore, 'persone': p.persone, 'gruppo': p.gruppo,
                                      'importo': p.importo, 'piano': p.piano.codice} for p in pren if p.stato != 'preventivo']})


@bp.route('/api/prenotazioni/<int:pid>/sposta', methods=['POST'])
def api_sposta(pid):
    p = db.get_or_404(Prenotazione, pid)
    d = request.get_json(silent=True) or {}
    try:
        h.sposta(p, d.get('camera_id'), d.get('arrivo'))
        db.session.commit()
    except h.Errore as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 400
    _tutor()
    return jsonify({'ok': True})


# ---------------- prenotazioni ----------------

@bp.route('/prenotazioni')
def prenotazioni():
    q = Prenotazione.query
    stato = request.args.get('stato', 'attive')
    if stato == 'attive':
        q = q.filter(Prenotazione.stato.in_(('preventivo', 'opzione', 'confermata', 'in_casa')))
    elif stato in STATI:
        q = q.filter(Prenotazione.stato == stato)
    da, a = _data(request.args.get('da')), _data(request.args.get('a'))
    if da:
        q = q.filter(Prenotazione.partenza > da)
    if a:
        q = q.filter(Prenotazione.arrivo <= a)
    testo = (request.args.get('q') or '').strip()
    if testo:
        like = f'%{testo.lower()}%'
        q = q.join(Ospite).filter(db.or_(db.func.lower(Ospite.cognome).like(like), db.func.lower(Ospite.nome).like(like),
                                         db.func.lower(Prenotazione.codice).like(like), db.func.lower(Prenotazione.gruppo).like(like),
                                         db.func.lower(Ospite.email).like(like)))
    canale = request.args.get('canale', type=int)
    if canale:
        q = q.filter(Prenotazione.canale_id == canale)
    ordine = Prenotazione.creato_il.desc() if request.args.get('ordine') == 'recenti' else Prenotazione.arrivo
    righe = q.order_by(ordine, Prenotazione.id).limit(400).all()
    return render_template('front/prenotazioni.html', righe=righe, STATI=STATI, stato=stato, canali=Canale.query.all(), args=request.args)


def _opzioni_form():
    return {'tipi': TipoCamera.query.order_by(TipoCamera.ordine, TipoCamera.id).all(),
            'piani': PianoTariffario.query.filter_by(attivo=True).all(), 'canali': Canale.query.filter_by(attivo=True).all(),
            'camere': Camera.query.filter_by(attiva=True).order_by(Camera.numero).all()}


@bp.route('/prenotazioni/nuova', methods=['GET', 'POST'])
def nuova():
    f = request.form if request.method == 'POST' else request.args
    if request.method == 'POST' and _csrf():
        try:
            camere = max(1, min(30, int(f.get('quante') or 1)))
            ospite = db.session.get(Ospite, int(f['ospite_id'])) if f.get('ospite_id') else h.ospite_da_dati(f)
            creati = []
            for i in range(camere):
                creati.append(h.crea_prenotazione(
                    ospite, int(f.get('tipo_id') or 0), int(f.get('piano_id') or 0), int(f.get('canale_id') or 0), f.get('arrivo'),
                    f.get('partenza'), int(f.get('adulti') or 1), int(f.get('bambini') or 0), f.get('stato') or 'confermata',
                    camera_id=(int(f['camera_id']) if f.get('camera_id') and i == 0 else None), note=f.get('note') or '',
                    gruppo=f.get('gruppo') or '', rif_canale=f.get('rif_canale') or ''))
            if f.get('caparra_incassata') and creati[0].caparra:
                for p in creati:
                    h.registra_pagamento(p, p.caparra, f.get('metodo') or 'carta', 'Caparra')
            db.session.commit()
            _tutor()
            if len(creati) > 1:
                flash(f'Gruppo creato: {len(creati)} prenotazioni ({", ".join(p.codice for p in creati)})', 'ok')
                return redirect(url_for('front.prenotazioni', q=creati[0].gruppo or creati[0].ospite.cognome))
            p = creati[0]
            flash(f'{STATI[p.stato]} {p.codice} registrata' + (f' — camera {p.camera.numero}' if p.camera else ''), 'ok')
            return redirect(url_for('front.scheda', pid=p.id))
        except (h.Errore, ValueError, KeyError) as e:
            db.session.rollback()
            flash(str(e) if isinstance(e, h.Errore) else 'Controlla i dati inseriti', 'err')
    oggi = date.today()
    arrivo = _data(f.get('arrivo'), oggi)
    dati = {'arrivo': arrivo.isoformat(), 'partenza': (_data(f.get('partenza')) or arrivo + timedelta(days=2)).isoformat(),
            'adulti': f.get('adulti') or 2, 'bambini': f.get('bambini') or 0, 'tipo_id': f.get('tipo_id', type=int),
            'camera_id': f.get('camera_id', type=int), 'piano_id': f.get('piano_id', type=int), 'canale_id': f.get('canale_id', type=int),
            **{k: f.get(k) or '' for k in ('cognome', 'nome', 'email', 'telefono', 'paese', 'note', 'gruppo', 'rif_canale', 'stato', 'quante')}}
    if dati['camera_id'] and not dati['tipo_id']:
        c = db.session.get(Camera, dati['camera_id'])
        dati['tipo_id'] = c.tipo_id if c else None
    ospite = db.session.get(Ospite, f.get('ospite_id', type=int)) if f.get('ospite_id') else None
    return render_template('front/nuova.html', d=dati, ospite=ospite, **_opzioni_form())


@bp.route('/api/preventivo')
def api_preventivo():
    a = request.args
    try:
        q = h.preventivo(a.get('tipo_id', type=int), a.get('piano_id', type=int), a.get('arrivo'), a.get('partenza'),
                         a.get('adulti', type=int) or 1, a.get('bambini', type=int) or 0, a.get('escludi', type=int))
    except h.Errore as e:
        return jsonify({'ok': False, 'error': str(e)})
    # disponibilità e prezzo di tutte le tipologie, per proporre alternative
    alternative = []
    for t in TipoCamera.query.order_by(TipoCamera.ordine).all():
        try:
            x = h.preventivo(t.id, a.get('piano_id', type=int), a.get('arrivo'), a.get('partenza'), a.get('adulti', type=int) or 1,
                             a.get('bambini', type=int) or 0)
            alternative.append({'tipo_id': t.id, 'nome': t.nome, 'totale': x['totale'], 'disponibili': x['minimo_disponibili']})
        except h.Errore:
            continue
    return jsonify({'ok': True, **q, 'alternative': alternative})


@bp.route('/api/ospiti')
def api_ospiti():
    t = (request.args.get('q') or '').strip().lower()
    if len(t) < 2:
        return jsonify({'ok': True, 'ospiti': []})
    like = f'%{t}%'
    r = Ospite.query.filter(db.or_(db.func.lower(Ospite.cognome).like(like), db.func.lower(Ospite.email).like(like))).limit(10).all()
    return jsonify({'ok': True, 'ospiti': [{'id': o.id, 'nome': o.nominativo, 'email': o.email, 'paese': o.paese} for o in r]})


@bp.route('/prenotazioni/<int:pid>', methods=['GET', 'POST'])
def scheda(pid):
    p = db.get_or_404(Prenotazione, pid)
    if request.method == 'POST' and _csrf():
        az = request.form.get('azione')
        f = request.form
        try:
            if az == 'conferma':
                h.conferma(p)
                msg = 'Prenotazione confermata'
            elif az == 'cancella':
                h.cancella(p, f.get('penale') if f.get('penale') not in (None, '') else None)
                msg = f'Prenotazione cancellata' + (f' con penale di {p.penale:.2f} €' if p.penale else ' senza penale')
            elif az == 'no_show':
                h.no_show(p)
                msg = f'No show registrato, penale {p.penale:.2f} €'
            elif az == 'check_in':
                h.check_in(p)
                msg = f'Check-in fatto: camera {p.camera.numero}'
            elif az == 'check_out':
                h.check_out(p, forza=bool(f.get('forza')) and g.utente.ruolo == 'admin')
                msg = 'Check-out fatto: la camera passa da rifare'
            elif az == 'camera':
                cid = f.get('camera_id', type=int)
                if cid:
                    h.sposta(p, cid)
                else:
                    if not h.assegna_camera(p):
                        raise h.Errore('Nessuna camera libera di questa tipologia')
                msg = f'Camera {p.camera.numero} assegnata'
            elif az == 'modifica':
                h.modifica(p, int(f['tipo_id']), int(f['piano_id']), f['arrivo'], f['partenza'], int(f.get('adulti') or 1), int(f.get('bambini') or 0))
                msg = f'Prenotazione aggiornata: nuovo totale {p.importo:.2f} €'
            elif az == 'note':
                p.note = (f.get('note') or '')[:500]
                p.gruppo = (f.get('gruppo') or '')[:80]
                p.rif_canale = (f.get('rif_canale') or '')[:40]
                if f.get('canale_id', type=int):
                    p.canale_id = f.get('canale_id', type=int)
                msg = 'Dati salvati'
            elif az == 'addebito':
                rep = f.get('reparto') if f.get('reparto') in REP_ADDEBITO else 'extra'
                h.aggiungi_addebito(p, (f.get('descrizione') or REP_ADDEBITO[rep]).strip(), float((f.get('prezzo') or '0').replace(',', '.')),
                                    float((f.get('quantita') or '1').replace(',', '.')), rep, float(f.get('iva') or 10),
                                    float((f.get('costo') or '0').replace(',', '.')))
                msg = 'Addebito registrato'
            elif az == 'elimina_addebito':
                a = db.get_or_404(Addebito, f.get('id', type=int))
                if a.prenotazione_id != p.id:
                    abort(404)
                db.session.delete(a)
                msg = 'Addebito eliminato'
            elif az == 'pagamento':
                imp = float((f.get('importo') or '0').replace(',', '.'))
                if f.get('rimborso'):
                    imp = -abs(imp)
                h.registra_pagamento(p, imp, f.get('metodo') if f.get('metodo') in METODI else 'carta', f.get('nota') or '')
                msg = 'Pagamento registrato'
            elif az == 'alloggiato':
                if not (f.get('cognome') and f.get('nome')):
                    raise h.Errore('Cognome e nome dell\'alloggiato sono obbligatori')
                a = Alloggiato(prenotazione_id=p.id, **{k: (f.get(k) or '').strip()[:80] for k in (
                    'tipo', 'cognome', 'nome', 'sesso', 'luogo_nascita', 'provincia_nascita', 'stato_nascita', 'cittadinanza',
                    'tipo_documento', 'numero_documento', 'luogo_rilascio', 'motivo_esenzione')},
                               data_nascita=_data(f.get('data_nascita')), esente_tassa=bool(f.get('esente_tassa')))
                if not a.tipo:
                    a.tipo = '16' if p.persone == 1 else ('17' if not p.alloggiati else '19')
                p.alloggiati.append(a)
                if not p.alloggiati[:-1] and f.get('aggiorna_ospite'):
                    o = p.ospite
                    for k in ('sesso', 'luogo_nascita', 'provincia_nascita', 'stato_nascita', 'cittadinanza', 'tipo_documento',
                              'numero_documento', 'luogo_rilascio'):
                        setattr(o, k, getattr(a, k))
                    o.data_nascita = a.data_nascita
                msg = f'Alloggiato {a.cognome} {a.nome} registrato'
            elif az == 'alloggiato_da_ospite':
                p.alloggiati.append(h.alloggiato_da_ospite(p, p.ospite))
                msg = 'Dati dell\'ospite copiati: completa il documento se manca'
            elif az == 'elimina_alloggiato':
                a = db.get_or_404(Alloggiato, f.get('id', type=int))
                if a.prenotazione_id != p.id:
                    abort(404)
                db.session.delete(a)
                msg = 'Alloggiato eliminato'
            else:
                raise h.Errore('Azione sconosciuta')
            db.session.commit()
            _tutor()
            flash(msg, 'ok')
        except h.Errore as e:
            db.session.rollback()
            flash(str(e), 'err')
        except (ValueError, KeyError):
            db.session.rollback()
            flash('Controlla i dati inseriti', 'err')
        return redirect(url_for('front.scheda', pid=p.id) + (request.form.get('ancora') or ''))
    c = h.conto(p)
    libere = h.camere_libere(None, p.arrivo, p.partenza, p.id) if p.stato in ('preventivo', 'opzione', 'confermata', 'in_casa') else []
    from dynaprice import rischio
    risk = rischio(p) if p.stato in ('opzione', 'confermata') else None
    return render_template('front/scheda.html', p=p, c=c, STATI=STATI, METODI=METODI, REP_ADDEBITO=REP_ADDEBITO, libere=libere,
                           penale=h.penale_cancellazione(p) if p.stato in ('confermata',) else 0, condizioni=h.condizioni(p.piano),
                           rischio=risk, oggi_d=date.today(), **_opzioni_form())


@bp.route('/prenotazioni/<int:pid>/conto')
def stampa_conto(pid):
    p = db.get_or_404(Prenotazione, pid)
    return render_template('front/conto.html', p=p, c=h.conto(p), METODI=METODI, struttura={k: h.par(k) for k in h.PARAMETRI})


@bp.route('/prenotazioni/<int:pid>/conferma-email')
def testo_conferma(pid):
    """Testo della conferma da copiare nell'email all'ospite."""
    p = db.get_or_404(Prenotazione, pid)
    return render_template('front/conferma.txt', p=p, condizioni=h.condizioni(p.piano), nome=h.par('nome_struttura'),
                           ci=h.par('ora_checkin'), co=h.par('ora_checkout'), tassa=h.par('tassa_importo')), 200, {'Content-Type': 'text/plain; charset=utf-8'}


# ---------------- ospiti ----------------

@bp.route('/ospiti')
def ospiti():
    t = (request.args.get('q') or '').strip().lower()
    q = Ospite.query
    if t:
        like = f'%{t}%'
        q = q.filter(db.or_(db.func.lower(Ospite.cognome).like(like), db.func.lower(Ospite.nome).like(like), db.func.lower(Ospite.email).like(like)))
    righe = q.order_by(Ospite.cognome, Ospite.nome).limit(300).all()
    soggiorni = dict(db.session.query(Prenotazione.ospite_id, db.func.count(Prenotazione.id))
                     .filter(Prenotazione.stato == 'partita').group_by(Prenotazione.ospite_id).all())
    spesa = dict(db.session.query(Prenotazione.ospite_id, db.func.sum(Prenotazione.importo))
                 .filter(Prenotazione.stato.in_(OCCUPANO)).group_by(Prenotazione.ospite_id).all())
    return render_template('front/ospiti.html', righe=righe, soggiorni=soggiorni, spesa=spesa, q=t)


@bp.route('/ospiti/<int:oid>', methods=['GET', 'POST'])
def ospite(oid):
    o = db.get_or_404(Ospite, oid)
    if request.method == 'POST' and _csrf():
        f = request.form
        if f.get('azione') == 'anonimizza':
            if Prenotazione.query.filter(Prenotazione.ospite_id == o.id, Prenotazione.stato.in_(('opzione', 'confermata', 'in_casa'))).first():
                flash('L\'ospite ha prenotazioni aperte: si anonimizza dopo la partenza', 'err')
            else:
                o.cognome, o.nome, o.email, o.telefono, o.note = 'Anonimo', f'#{o.id}', '', '', ''
                o.numero_documento = o.tipo_documento = o.luogo_nascita = o.luogo_rilascio = ''
                o.data_nascita, o.consenso_marketing = None, False
                db.session.commit()
                flash('Dati personali cancellati (restano le prenotazioni per la contabilità)', 'ok')
            return redirect(url_for('front.ospite', oid=o.id))
        for k in ('cognome', 'nome', 'email', 'telefono', 'lingua', 'paese', 'sesso', 'luogo_nascita', 'provincia_nascita', 'stato_nascita',
                  'cittadinanza', 'tipo_documento', 'numero_documento', 'luogo_rilascio', 'note'):
            if k in f:
                setattr(o, k, (f.get(k) or '').strip()[:500 if k == 'note' else 200])
        o.data_nascita = _data(f.get('data_nascita'))
        consenso = bool(f.get('consenso_marketing'))
        if consenso and not o.consenso_marketing:
            o.consenso_il = datetime.utcnow()
        o.consenso_marketing = consenso
        if not o.cognome:
            flash('Il cognome è obbligatorio', 'err')
            db.session.rollback()
        else:
            db.session.commit()
            flash('Scheda ospite salvata', 'ok')
        return redirect(url_for('front.ospite', oid=o.id))
    storico = Prenotazione.query.filter_by(ospite_id=o.id).order_by(Prenotazione.arrivo.desc()).all()
    return render_template('front/ospite.html', o=o, storico=storico, STATI=STATI)


@bp.route('/ospiti/marketing.csv')
def ospiti_csv():
    """Solo gli ospiti con consenso al marketing (GDPR), per newsletter e campagne."""
    import csv
    import io
    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(['cognome', 'nome', 'email', 'telefono', 'lingua', 'paese', 'consenso_il'])
    for o in Ospite.query.filter(Ospite.consenso_marketing.is_(True), Ospite.email != '').order_by(Ospite.cognome).all():
        w.writerow([o.cognome, o.nome, o.email, o.telefono, o.lingua, o.paese, o.consenso_il.strftime('%d/%m/%Y') if o.consenso_il else ''])
    return Response('﻿' + out.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename="ospiti_marketing.csv"'})


# ---------------- housekeeping ----------------

@bp.route('/housekeeping')
def housekeeping():
    oggi = date.today()
    camere = Camera.query.filter_by(attiva=True).order_by(Camera.piano, Camera.numero).all()
    in_casa = {p.camera_id: p for p in Prenotazione.query.filter(Prenotazione.stato == 'in_casa').all()}
    arrivi = {p.camera_id: p for p in Prenotazione.query.filter(Prenotazione.stato == 'confermata', Prenotazione.arrivo == oggi).all()}
    piani = {}
    for c in camere:
        p = in_casa.get(c.id)
        if p and p.partenza <= oggi:
            situazione = 'partenza'
        elif p:
            situazione = 'fermata'
        elif c.id in arrivi:
            situazione = 'arrivo'
        else:
            situazione = 'libera'
        piani.setdefault(c.piano or '—', []).append({'c': c, 'situazione': situazione, 'ospite': (p or arrivi.get(c.id)), 'arrivo': arrivi.get(c.id)})
    conta = {s: sum(1 for c in camere if c.stato == s) for s in ('pulita', 'sporca', 'ispezionata', 'fuori_servizio')}
    return render_template('front/housekeeping.html', piani=piani, conta=conta)


@bp.route('/api/camere/<int:cid>/stato', methods=['POST'])
def api_stato_camera(cid):
    c = db.get_or_404(Camera, cid)
    stato = (request.get_json(silent=True) or {}).get('stato')
    if stato not in ('pulita', 'sporca', 'ispezionata', 'fuori_servizio'):
        return jsonify({'ok': False, 'error': 'stato non valido'}), 400
    if stato == 'fuori_servizio' and g.utente.ruolo == 'piani':
        return jsonify({'ok': False, 'error': 'Il fuori servizio lo decide la direzione o la reception'}), 403
    if stato == 'fuori_servizio' and Prenotazione.query.filter(Prenotazione.camera_id == c.id, Prenotazione.stato == 'in_casa').first():
        return jsonify({'ok': False, 'error': 'Camera occupata: sposta prima l\'ospite'}), 400
    c.stato = stato
    db.session.commit()
    _tutor()
    return jsonify({'ok': True})


# ---------------- alloggiati e imposta di soggiorno ----------------

def _pad(v, n):
    return (v or '')[:n].ljust(n)


@bp.route('/alloggiati')
def alloggiati():
    d = _data(request.args.get('data'), date.today())
    pren = Prenotazione.query.filter(Prenotazione.arrivo == d, Prenotazione.stato.in_(('in_casa', 'partita'))).order_by(Prenotazione.id).all()
    if request.args.get('formato') == 'txt':
        righe = []
        for p in pren:
            for a in p.alloggiati:
                righe.append(_pad(a.tipo, 2) + p.arrivo.strftime('%d/%m/%Y') + str(min(p.notti, 30)).rjust(2, '0') + _pad(a.cognome.upper(), 50)
                             + _pad(a.nome.upper(), 30) + ('1' if a.sesso == 'M' else '2' if a.sesso == 'F' else ' ')
                             + (a.data_nascita.strftime('%d/%m/%Y') if a.data_nascita else ' ' * 10) + _pad(a.luogo_nascita, 9)
                             + _pad(a.provincia_nascita, 2) + _pad(a.stato_nascita, 9) + _pad(a.cittadinanza, 9)
                             + (_pad(a.tipo_documento, 5) + _pad(a.numero_documento, 20) + _pad(a.luogo_rilascio, 9) if a.tipo in ('16', '17', '18') else ' ' * 34))
        return Response('\r\n'.join(righe) + ('\r\n' if righe else ''), mimetype='text/plain',
                        headers={'Content-Disposition': f'attachment; filename="alloggiati_{d.isoformat()}.txt"'})
    mancanti = [p for p in pren if len(p.alloggiati) < p.persone]
    return render_template('front/alloggiati.html', pren=pren, d=d, mancanti=mancanti)


@bp.route('/imposta-soggiorno')
def imposta():
    oggi = date.today()
    mese = request.args.get('mese') or oggi.strftime('%Y-%m')
    try:
        da = date.fromisoformat(mese + '-01')
    except ValueError:
        da = oggi.replace(day=1)
    a = (da.replace(day=28) + timedelta(days=4)).replace(day=1)
    righe, tot = [], {'persone': 0, 'pernottamenti': 0, 'tassati': 0, 'esenti': 0, 'importo': 0.0}
    for p in Prenotazione.query.filter(Prenotazione.partenza > da, Prenotazione.partenza <= a, Prenotazione.stato.in_(('partita', 'in_casa'))).order_by(Prenotazione.partenza).all():
        t = h.tassa(p)
        persone = len(p.alloggiati) or p.persone
        righe.append({'p': p, 't': t, 'persone': persone, 'esenti': max(0, persone - t['persone'])})
        tot['persone'] += persone
        tot['pernottamenti'] += persone * p.notti
        tot['tassati'] += t['persone'] * t['notti']
        tot['esenti'] += max(0, persone - t['persone'])
        tot['importo'] += t['totale']
    return render_template('front/imposta.html', righe=righe, tot=tot, mese=da.strftime('%Y-%m'), da=da, importo=h.par('tassa_importo'),
                           max_notti=h.par('tassa_max_notti'), eta=h.par('tassa_eta_esenti'), comune=h.par('comune'))
