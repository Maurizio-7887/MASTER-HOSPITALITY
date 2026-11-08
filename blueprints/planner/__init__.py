"""Il planner delle camere: camere × giorni, trascinamento dei soggiorni."""
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

import hotel as h
from blueprints._comune import csrf as _csrf, data as _data, tutor as _tutor
from models import (METODI, REP_ADDEBITO, STATI, Addebito, Alloggiato, Camera, Canale, OCCUPANO, Ospite, PianoTariffario,
                    Prenotazione, TipoCamera, db)

bp = Blueprint('planner', __name__, template_folder='templates')

@bp.route('/planner')
def planner():
    return render_template('planner/planner.html', da=_data(request.args.get('da'), date.today() - timedelta(days=2)).isoformat())


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
