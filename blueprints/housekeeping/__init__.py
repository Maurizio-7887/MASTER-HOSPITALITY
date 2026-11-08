"""Housekeeping: stato delle camere per piano, anche dal telefono."""
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

import hotel as h
from blueprints._comune import csrf as _csrf, data as _data, tutor as _tutor
from models import (METODI, REP_ADDEBITO, STATI, Addebito, Alloggiato, Camera, Canale, OCCUPANO, Ospite, PianoTariffario,
                    Prenotazione, TipoCamera, db)

bp = Blueprint('housekeeping', __name__, template_folder='templates')

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
    return render_template('housekeeping/housekeeping.html', piani=piani, conta=conta)


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
