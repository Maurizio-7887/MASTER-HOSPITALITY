"""Modello ML didattico: regressione ridge addestrata sulle occupazioni giornaliere dell'hotel.
Nessun dato cliente inviato all'esterno. Fallback esplicito quando lo storico e' insufficiente.
"""
from datetime import date, timedelta
from math import sqrt
from models import Camera, NotteVenduta, Prenotazione, OCCUPANO, db

def _features(d, baseline):
    # Intercetta, giorno settimana one-hot, stagionalita' annua e trend di domanda.
    return [1.] + [float(d.weekday() == i) for i in range(7)] + [
        (d.timetuple().tm_yday - 183) / 183., baseline]

def _fit(rows, alpha=3.0, epochs=2200):
    if not rows:
        raise ValueError('Storico insufficiente')
    n = len(rows[0][0])
    weights = [0.] * n
    for _ in range(epochs):
        grad = [0.] * n
        for x, y in rows:
            err = sum(a * b for a, b in zip(x, weights)) - y
            for j in range(n):
                grad[j] += err * x[j]
        for j in range(n):
            weights[j] -= .12 * (grad[j] / len(rows) + (alpha * weights[j] / len(rows) if j else 0))
    return weights

def report(oggi=None, orizzonte=14):
    oggi = oggi or date.today()
    orizzonte = max(1, min(90, int(orizzonte)))
    cap = Camera.query.filter_by(attiva=True).filter(Camera.stato != 'fuori_servizio').count()
    if not cap:
        return {'pronto': False, 'motivo': 'Non ci sono camere attive', 'previsioni': []}
    start = oggi - timedelta(days=240)
    righe = (db.session.query(NotteVenduta.data, db.func.count(NotteVenduta.id))
        .join(Prenotazione, Prenotazione.id == NotteVenduta.prenotazione_id)
        .filter(Prenotazione.stato.in_(OCCUPANO), NotteVenduta.data >= start,
                NotteVenduta.data < oggi)
        .group_by(NotteVenduta.data).all())
    per_data = {d: min(1., n / cap) for d, n in righe}
    # Includere anche i giorni senza vendite evita la distorsione sui soli giorni occupati.
    giorni = [(start + timedelta(days=i)) for i in range((oggi - start).days)]
    values = [(d, per_data.get(d, 0.)) for d in giorni]
    # Una camera mai venduta per mesi non genera una previsione affidabile.
    if sum(v > 0 for _, v in values) < 30:
        return {'pronto': False, 'motivo': 'Servono almeno 30 giornate storiche con vendite', 'previsioni': []}
    means = [sum(v for d, v in values if d.weekday() == i) / max(1, sum(d.weekday() == i for d, _ in values)) for i in range(7)]
    samples = [(_features(d, means[d.weekday()]), v) for d, v in values]
    cut = int(len(samples) * .8)
    w = _fit(samples[:cut])
    def pred(x):
        return max(0., min(1., sum(a*b for a,b in zip(x,w))))
    test = samples[cut:]
    mae = sum(abs(pred(x)-y) for x,y in test)/max(1,len(test))
    baseline_mae = sum(abs(x[-1]-y) for x,y in test)/max(1,len(test))
    # Riaddestramento con tutti i giorni, dopo la valutazione temporale senza leakage.
    w = _fit(samples)
    future = []
    for i in range(orizzonte):
        d = oggi + timedelta(days=i)
        future.append({'data': d.isoformat(), 'occupazione_prevista': round(pred(_features(d,means[d.weekday()])),3)})
    return {'pronto': True, 'metodo':'ridge_regression_v1', 'giorni_training':cut,
            'giorni_validazione':len(test), 'mae':round(mae,4), 'mae_baseline':round(baseline_mae,4),
            'migliora_baseline':mae < baseline_mae, 'previsioni':future,
            'nota':'Modello didattico; validare su piu stagioni prima di usarlo per decidere prezzi.'}
