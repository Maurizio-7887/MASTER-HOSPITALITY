"""Booking engine: la pagina pubblica di prenotazione della struttura (senza accesso, senza commissioni).

L'ospite sceglie date e persone, vede le tipologie libere con i prezzi veri di ogni piano tariffario (stagioni,
prezzi di DynaPrice, restrizioni), compila i dati e prenota. La prenotazione entra nel gestionale sul canale diretto:
confermata se il piano non chiede caparra, altrimenti in opzione fino all'arrivo della caparra.
"""
from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

import hotel as h
from blueprints._comune import data as _data
from models import Canale, PianoTariffario, Prenotazione, Spazio, TipoCamera, db

bp = Blueprint('booking', __name__, template_folder='templates')


def _apri(sid):
    """Lo spazio della struttura: deve esistere, essere attivo e avere il booking engine acceso."""
    sp = db.session.get(Spazio, sid)
    if sp is None or sp.scaduto():
        abort(404)
    g.spazio_id, g.spazio = sid, sp
    if not h.par('booking_attivo'):
        abort(404)
    return sp


def canale_diretto():
    canali = Canale.query.filter_by(attivo=True).order_by(Canale.id).all()
    return (next((c for c in canali if 'booking engine' in c.nome.lower()), None)
            or next((c for c in canali if c.tipo == 'diretto'), None) or (canali[0] if canali else None))


def offerte(arrivo, partenza, adulti, bambini):
    out = []
    piani = PianoTariffario.query.filter_by(attivo=True).order_by(PianoTariffario.id).all()
    for t in TipoCamera.query.order_by(TipoCamera.ordine, TipoCamera.id).all():
        if adulti + bambini > t.posti_max or not h.camere_attive(t.id):
            continue
        tariffe = []
        for p in piani:
            try:
                q = h.preventivo(t.id, p.id, arrivo, partenza, adulti, bambini)
            except h.Errore:
                continue
            if q['disponibile'] and not q['restrizioni']:
                tariffe.append({'piano': p, 'q': q})
        if tariffe:
            out.append({'tipo': t, 'tariffe': tariffe, 'da': min(x['q']['totale'] for x in tariffe),
                        'ultime': min(x['q']['minimo_disponibili'] for x in tariffe)})
        else:
            # perché non si può prenotare: esaurito o restrizioni (soggiorno minimo, arrivi chiusi)
            try:
                q = h.preventivo(t.id, piani[0].id, arrivo, partenza, adulti, bambini) if piani else None
                motivo = '; '.join(q['restrizioni']) if q and q['restrizioni'] else 'esaurita per queste date'
            except h.Errore as e:
                motivo = str(e)
            out.append({'tipo': t, 'tariffe': [], 'motivo': motivo})
    return out


@bp.route('/prenota/<int:sid>')
def cerca(sid):
    _apri(sid)
    a = request.args
    oggi = date.today()
    arrivo = _data(a.get('arrivo'))
    partenza = _data(a.get('partenza'))
    adulti = max(1, min(8, a.get('adulti', type=int) or 2))
    bambini = max(0, min(6, a.get('bambini', type=int) or 0))
    risultati, errore = None, ''
    if arrivo and partenza:
        if arrivo < oggi:
            errore = 'La data di arrivo è già passata'
        elif partenza <= arrivo:
            errore = 'La partenza deve essere dopo l\'arrivo'
        elif (partenza - arrivo).days > 30:
            errore = 'Per soggiorni oltre 30 notti scrivici: ti prepariamo un\'offerta'
        else:
            risultati = offerte(arrivo, partenza, adulti, bambini)
    return render_template('booking/cerca.html', sid=sid, risultati=risultati, errore=errore, oggi=oggi,
                           d={'arrivo': (arrivo or oggi + timedelta(days=7)).isoformat(),
                              'partenza': (partenza or (arrivo or oggi + timedelta(days=7)) + timedelta(days=2)).isoformat(),
                              'adulti': adulti, 'bambini': bambini},
                           cerca_fatta=bool(arrivo and partenza), notti=(partenza - arrivo).days if arrivo and partenza else 0,
                           **_struttura())


def _struttura():
    return {'s': {k: h.par(k) for k in ('nome_struttura', 'categoria', 'comune', 'indirizzo', 'telefono_struttura', 'email_struttura',
                                        'ora_checkin', 'ora_checkout')}}


@bp.route('/prenota/<int:sid>/dati', methods=['GET', 'POST'])
def dati(sid):
    _apri(sid)
    f = request.form if request.method == 'POST' else request.args
    try:
        tipo_id, piano_id = int(f.get('tipo_id') or 0), int(f.get('piano_id') or 0)
        adulti, bambini = int(f.get('adulti') or 2), int(f.get('bambini') or 0)
        q = h.preventivo(tipo_id, piano_id, f.get('arrivo'), f.get('partenza'), adulti, bambini)
    except (h.Errore, ValueError) as e:
        flash(str(e) if isinstance(e, h.Errore) else 'Scegli di nuovo la camera', 'err')
        return redirect(url_for('booking.cerca', sid=sid))
    tipo, piano = db.session.get(TipoCamera, tipo_id), db.session.get(PianoTariffario, piano_id)
    if request.method == 'POST':
        from blueprints.accesso import _csrf_ok
        errore = ''
        if not _csrf_ok():
            errore = 'Sessione scaduta: riprova'
        elif f.get('sito_web'):                       # campo trappola per i robot
            abort(400)
        elif not (f.get('cognome') or '').strip() or not (f.get('email') or '').strip() or '@' not in f.get('email', ''):
            errore = 'Scrivi almeno cognome ed email validi'
        elif not f.get('privacy'):
            errore = 'Per prenotare serve il consenso al trattamento dei dati per la prenotazione'
        elif not q['disponibile'] or q['restrizioni']:
            errore = 'Nel frattempo la camera non è più disponibile: scegline un\'altra'
        if errore:
            flash(errore, 'err')
        else:
            canale = canale_diretto()
            ospite = h.ospite_da_dati({k: (f.get(k) or '').strip() for k in ('cognome', 'nome', 'email', 'telefono', 'paese')})
            if f.get('marketing'):
                from datetime import datetime
                ospite.consenso_marketing, ospite.consenso_il = True, datetime.utcnow()
            stato = 'opzione' if q['caparra'] > 0 else 'confermata'
            try:
                p = h.crea_prenotazione(ospite, tipo_id, piano_id, canale.id, f.get('arrivo'), f.get('partenza'), adulti, bambini, stato,
                                        note=('Dal booking engine. ' + (f.get('note') or '').strip())[:500])
                if stato == 'opzione':
                    p.opzione_fino = min(p.arrivo - timedelta(days=1), date.today() + timedelta(days=h.par('giorni_opzione')))
                db.session.commit()
            except h.Errore as e:
                db.session.rollback()
                flash(str(e), 'err')
                return redirect(url_for('booking.cerca', sid=sid, arrivo=f.get('arrivo'), partenza=f.get('partenza'), adulti=adulti, bambini=bambini))
            try:
                import comunicazioni
                comunicazioni.invia_evento(p, 'conferma')
                db.session.commit()
            except Exception:   # noqa: BLE001 — l'email non deve bloccare la prenotazione
                db.session.rollback()
            session['booking_ultima'] = p.id
            return redirect(url_for('booking.fatto', sid=sid, codice=p.codice))
    return render_template('booking/dati.html', sid=sid, q=q, tipo=tipo, piano=piano, f=f, adulti=adulti, bambini=bambini, **_struttura())


@bp.route('/prenota/<int:sid>/fatto/<codice>')
def fatto(sid, codice):
    _apri(sid)
    p = Prenotazione.query.filter_by(codice=codice).first()
    if not p or session.get('booking_ultima') != p.id:
        abort(404)
    return render_template('booking/fatto.html', sid=sid, p=p, condizioni=h.condizioni(p.piano), iban=h.par('iban') if 'iban' in h.PARAMETRI else '',
                           **_struttura())
