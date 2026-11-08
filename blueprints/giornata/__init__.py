"""La giornata: arrivi, partenze, in casa, camere da rifare, occupazione dei prossimi 14 giorni."""
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

import hotel as h
from blueprints._comune import csrf as _csrf, data as _data, tutor as _tutor
from models import (METODI, REP_ADDEBITO, STATI, Addebito, Alloggiato, Camera, Canale, OCCUPANO, Ospite, PianoTariffario,
                    Prenotazione, TipoCamera, db)

bp = Blueprint('giornata', __name__, template_folder='templates')

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
    return render_template('giornata/oggi.html', arrivi=arrivi, partenze=partenze, in_casa=in_casa, mancati=mancati, opzioni=opzioni,
                           preventivi=preventivi, sporche=sporche, venduto=venduto, totale=totale, prossimi=prossimi, mese=mese,
                           nuove=nuove, oggi_d=oggi, vuoto=not TipoCamera.query.first())
