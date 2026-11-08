"""Prenotazioni: elenco, nuova prenotazione e gruppi, preventivo, scheda con conto, check-in e check-out."""
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

import hotel as h
from blueprints._comune import csrf as _csrf, data as _data, tutor as _tutor
from models import (METODI, REP_ADDEBITO, STATI, Addebito, Alloggiato, Camera, Canale, OCCUPANO, Ospite, PianoTariffario,
                    Prenotazione, TipoCamera, db)

bp = Blueprint('prenotazioni', __name__, template_folder='templates')

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
    return render_template('prenotazioni/prenotazioni.html', righe=righe, STATI=STATI, stato=stato, canali=Canale.query.all(), args=request.args)


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
                return redirect(url_for('prenotazioni.prenotazioni', q=creati[0].gruppo or creati[0].ospite.cognome))
            p = creati[0]
            flash(f'{STATI[p.stato]} {p.codice} registrata' + (f' — camera {p.camera.numero}' if p.camera else ''), 'ok')
            return redirect(url_for('prenotazioni.scheda', pid=p.id))
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
    return render_template('prenotazioni/nuova.html', d=dati, ospite=ospite, **_opzioni_form())


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
        return redirect(url_for('prenotazioni.scheda', pid=p.id) + (request.form.get('ancora') or ''))
    c = h.conto(p)
    libere = h.camere_libere(None, p.arrivo, p.partenza, p.id) if p.stato in ('preventivo', 'opzione', 'confermata', 'in_casa') else []
    from dynaprice import rischio
    risk = rischio(p) if p.stato in ('opzione', 'confermata') else None
    return render_template('prenotazioni/scheda.html', p=p, c=c, STATI=STATI, METODI=METODI, REP_ADDEBITO=REP_ADDEBITO, libere=libere,
                           penale=h.penale_cancellazione(p) if p.stato in ('confermata',) else 0, condizioni=h.condizioni(p.piano),
                           rischio=risk, oggi_d=date.today(), **_opzioni_form())


@bp.route('/prenotazioni/<int:pid>/conto')
def stampa_conto(pid):
    p = db.get_or_404(Prenotazione, pid)
    return render_template('prenotazioni/conto.html', p=p, c=h.conto(p), METODI=METODI, struttura={k: h.par(k) for k in h.PARAMETRI})


@bp.route('/prenotazioni/<int:pid>/conferma-email')
def testo_conferma(pid):
    """Testo della conferma da copiare nell'email all'ospite."""
    p = db.get_or_404(Prenotazione, pid)
    return render_template('prenotazioni/conferma.txt', p=p, condizioni=h.condizioni(p.piano), nome=h.par('nome_struttura'),
                           ci=h.par('ora_checkin'), co=h.par('ora_checkout'), tassa=h.par('tassa_importo')), 200, {'Content-Type': 'text/plain; charset=utf-8'}
