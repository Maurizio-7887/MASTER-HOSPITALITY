"""Comunicazioni agli ospiti: modelli delle email automatiche, coda di oggi e storico degli invii."""
from flask import Blueprint, flash, redirect, render_template, request, url_for

import comunicazioni as cm
from blueprints._comune import csrf as _csrf
from models import EVENTI_EMAIL, EmailInviata, Prenotazione, db

bp = Blueprint('comunicazioni', __name__, template_folder='templates')


@bp.route('/comunicazioni', methods=['GET', 'POST'])
def pagina():
    modelli = {e: cm.modello(e) for e in EVENTI_EMAIL}
    db.session.commit()
    if request.method == 'POST' and _csrf():
        azione = request.form.get('azione')
        if azione == 'salva':
            m = modelli.get(request.form.get('evento'))
            if m:
                m.oggetto = (request.form.get('oggetto') or m.oggetto)[:160]
                m.testo = request.form.get('testo') or m.testo
                m.attivo = bool(request.form.get('attivo'))
                flash('Modello salvato', 'ok')
        elif azione == 'invia_oggi':
            n = cm.invia_quelle_di_oggi()
            flash(f'Email inviate: {n}', 'ok')
        elif azione == 'predefinito':
            e = request.form.get('evento')
            if e in modelli:
                modelli[e].oggetto, modelli[e].testo = cm.PREDEFINITI[e]
                flash('Testo predefinito ripristinato', 'ok')
        db.session.commit()
        return redirect(url_for('comunicazioni.pagina'))
    esempio = Prenotazione.query.filter(Prenotazione.stato.in_(('confermata', 'in_casa'))).order_by(Prenotazione.arrivo.desc()).first()
    anteprime = {e: cm.componi(esempio, e)[:2] for e in EVENTI_EMAIL} if esempio else {}
    storico = EmailInviata.query.order_by(EmailInviata.id.desc()).limit(40).all()
    pren = {p.id: p for p in Prenotazione.query.filter(Prenotazione.id.in_([s.prenotazione_id for s in storico])).all()} if storico else {}
    return render_template('comunicazioni/pagina.html', modelli=modelli, EVENTI=EVENTI_EMAIL, coda=cm.da_inviare(), storico=storico,
                           pren=pren, anteprime=anteprime, esempio=esempio)
