"""Ospiti: schede con storico, documenti, consenso al marketing e anonimizzazione."""
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

import hotel as h
from blueprints._comune import csrf as _csrf, data as _data, tutor as _tutor
from models import (METODI, REP_ADDEBITO, STATI, Addebito, Alloggiato, Camera, Canale, OCCUPANO, Ospite, PianoTariffario,
                    Prenotazione, TipoCamera, db)

bp = Blueprint('ospiti', __name__, template_folder='templates')

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
    return render_template('ospiti/ospiti.html', righe=righe, soggiorni=soggiorni, spesa=spesa, q=t)


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
            return redirect(url_for('ospiti.ospite', oid=o.id))
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
        return redirect(url_for('ospiti.ospite', oid=o.id))
    storico = Prenotazione.query.filter_by(ospite_id=o.id).order_by(Prenotazione.arrivo.desc()).all()
    return render_template('ospiti/ospite.html', o=o, storico=storico, STATI=STATI)


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
