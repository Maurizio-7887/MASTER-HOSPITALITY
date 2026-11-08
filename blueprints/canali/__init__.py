"""Channel manager: canali collegati, mappature e allotment, invio di disponibilità e prezzi, prenotazioni dai portali,
calendari iCal, parità tariffaria e registro delle sincronizzazioni."""
from datetime import date, timedelta

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

import channel as ch
import hotel as h
from blueprints._comune import csrf as _csrf, data as _data, tutor as _tutor
from models import CONNESSIONI, Canale, CanaleTipo, Sincronizzazione, Spazio, db

bp = Blueprint('canali', __name__, template_folder='templates')


def _num(v, default=None):
    try:
        return float(str(v).replace(',', '.')) if v not in (None, '') else default
    except ValueError:
        return default


@bp.route('/canali', methods=['GET', 'POST'])
def pagina():
    if request.method == 'POST' and _csrf():
        azione = request.form.get('azione')
        try:
            if azione == 'invia_tutti':
                ris = ch.invia_a_tutti()
                db.session.commit()
                flash('; '.join(f'{c.nome}: {m}' for c, m in ris) or 'Nessun canale collegato a un portale', 'ok')
            db.session.commit()
        except ch.ErroreCanale as e:
            db.session.rollback()
            flash(str(e), 'err')
        return redirect(url_for('canali.pagina'))
    for c in Canale.query.all():
        if not c.token:
            ch.prepara_canale(c)
    db.session.commit()
    log = Sincronizzazione.query.order_by(Sincronizzazione.id.desc()).limit(40).all()
    return render_template('canali/pagina.html', canali=ch.stato_canali(), log=log, CONNESSIONI=CONNESSIONI,
                           booking_url=url_for('booking.cerca', sid=g.spazio_id, _external=True), booking_attivo=h.par('booking_attivo'))


@bp.route('/canali/<int:cid>', methods=['GET', 'POST'])
def canale(cid):
    c = db.get_or_404(Canale, cid)
    ch.prepara_canale(c)
    db.session.commit()
    if request.method == 'POST' and _csrf():
        azione = request.form.get('azione')
        f = request.form
        try:
            if azione == 'connessione':
                c.connessione = f.get('connessione') if f.get('connessione') in CONNESSIONI else 'manuale'
                c.codice_struttura = (f.get('codice_struttura') or '').strip()[:40]
                c.ical_url = (f.get('ical_url') or '').strip()[:400]
                flash('Connessione salvata', 'ok')
            elif azione == 'mappature':
                for m in ch.mappature(c):
                    m.codice_ota = (f.get(f'codice_{m.id}') or '').strip()[:40]
                    m.allotment = int(f.get(f'allotment_{m.id}')) if (f.get(f'allotment_{m.id}') or '').strip().isdigit() else None
                    m.ricarico_pct = _num(f.get(f'ricarico_{m.id}'), 0)
                    m.attivo = bool(f.get(f'attivo_{m.id}'))
                flash('Mappature salvate: ricordati di inviare disponibilità e prezzi', 'ok')
            elif azione == 'invia':
                esito = ch.invia(c, _data(f.get('da'), date.today()), _data(f.get('a'), date.today() + timedelta(days=90)))
                flash(esito['messaggio'], 'ok' if esito.get('inviato') or c.connessione == 'manuale' else '')
            elif azione == 'ricevi':
                kw = {'quante': int(f.get('quante') or 2)} if c.connessione == 'simulatore' else {}
                esiti = ch.ricevi(c, **kw)
                nuove = [p for p in esiti.get('nuova', []) if p]
                flash(f'Prenotazioni ricevute: {len(nuove)} nuove' + (f', {len(esiti["cancellata"])} cancellate' if esiti.get('cancellata') else '')
                      + (f', {len(esiti["errore"])} con errore (vedi registro)' if esiti.get('errore') else ''), 'ok')
            elif azione == 'ical_testo':
                testo = f.get('ics') or ''
                file = request.files.get('file_ics')
                if file and file.filename:
                    testo = file.read().decode('utf-8', 'replace')
                r = ch.ical_import(c, testo, f.get('tipo_id', type=int))
                flash(f'Calendario letto: {len(r["nuove"])} prenotazioni nuove, {r["doppie"]} già presenti, {r["bloccate"]} notti bloccate', 'ok')
            elif azione == 'ical_url':
                r = ch.scarica_ical(c)
                flash(f'Calendario scaricato: {len(r["nuove"])} prenotazioni nuove, {r["doppie"]} già presenti', 'ok')
            elif azione == 'nuovo_token':
                c.token = ''
                ch.prepara_canale(c)
                flash('Nuovo indirizzo iCal creato: aggiornalo sul portale', 'ok')
            db.session.commit()
            _tutor()
        except (ch.ErroreCanale, h.Errore) as e:
            db.session.commit()      # il registro dell'errore resta
            flash(str(e), 'err')
        return redirect(url_for('canali.canale', cid=c.id))
    da = _data(request.args.get('da'), date.today())
    righe = ch.ari(c, da, da + timedelta(days=14))
    log = Sincronizzazione.query.filter_by(canale_id=c.id).order_by(Sincronizzazione.id.desc()).limit(30).all()
    ical = {m.tipo_id: url_for('canali.ical', token=c.token, tipo_id=m.tipo_id, _external=True) for m in ch.mappature(c)}
    return render_template('canali/canale.html', c=c, mappe=ch.mappature(c), righe=righe, log=log, CONNESSIONI=CONNESSIONI,
                           ical=ical, da=da)


@bp.route('/canali/parita')
def parita():
    da = _data(request.args.get('da'), date.today())
    giorni = max(1, min(31, request.args.get('giorni', type=int) or 7))
    tabella = ch.parita(da, da + timedelta(days=giorni))
    rotte = sum(1 for r in tabella for x in r['canali'] if x['sotto_il_sito'])
    return render_template('canali/parita.html', tabella=tabella, da=da, giorni=giorni, rotte=rotte)


@bp.route('/canali/registro/<int:sid_log>')
def dettaglio(sid_log):
    s = db.get_or_404(Sincronizzazione, sid_log)
    return Response(s.dati or '{}', mimetype='application/json')


@bp.route('/ical/<token>/<int:tipo_id>.ics')
def ical(token, tipo_id):
    """Calendario pubblico per il portale (senza accesso): lo spazio si ricava dal token del canale."""
    from spazi import nello_spazio
    c = Canale.query.execution_options(tutti_gli_spazi=True).filter_by(token=token).first() if token else None
    if not c:
        abort(404)
    with nello_spazio(c.spazio_id):
        sp = db.session.get(Spazio, c.spazio_id)
        if sp is None or sp.scaduto():
            abort(404)
        testo = ch.ical_export(c, tipo_id)
    return Response(testo, mimetype='text/calendar', headers={'Content-Disposition': f'inline; filename=disponibilita-{tipo_id}.ics'})
