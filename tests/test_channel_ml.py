"""Integrazione Channel Manager simulato e forecasting ML."""
from datetime import date, timedelta
from models import Canale, PianoTariffario, TipoCamera, Prenotazione, db

def test_channel_manager_import_cancel_idempotent(demo, client):
    assert client.get('/channel-manager').status_code == 200
    j = client.get('/api/channel-manager/inventario?giorni=2').get_json()
    assert j['ok'] and j['modalita'] == 'simulazione' and j['righe']
    canale = Canale.query.filter_by(tipo='ota').first()
    piano = PianoTariffario.query.filter_by(attivo=True).first()
    tipo = TipoCamera.query.first()
    d = date.today() + timedelta(days=120)
    body = {'canale_id':canale.id,'tipo_id':tipo.id,'piano_id':piano.id,
            'riferimento':'OTA-TEST-123','arrivo':d.isoformat(),
            'partenza':(d+timedelta(days=2)).isoformat(),'cognome':'Collaudo'}
    r=client.post('/api/channel-manager/prenotazione',json=body)
    assert r.status_code==201, r.get_json()
    assert r.get_json()['ok']
    p=Prenotazione.query.filter_by(canale_id=canale.id,rif_canale='OTA-TEST-123').one()
    assert p.stato=='confermata'
    second=client.post('/api/channel-manager/prenotazione',json=body).get_json()
    assert second['duplicato'] and second['id']==p.id
    cancel={'canale_id':canale.id,'riferimento':'OTA-TEST-123'}
    assert client.post('/api/channel-manager/cancella',json=cancel).get_json()['ok']
    assert db.session.get(Prenotazione,p.id).stato=='cancellata'
    assert client.post('/api/channel-manager/cancella',json=cancel).get_json()['duplicato']

def test_channel_manager_validation(demo, client):
    assert client.get('/api/channel-manager/inventario?giorni=1000').status_code==400
    assert client.post('/api/channel-manager/prenotazione',json={}).status_code==400
    assert client.post('/api/channel-manager/cancella',json={}).status_code==400

def test_ml_api_graceful_fallback(demo, client):
    r=client.get('/api/revenue/ml')
    assert r.status_code==200
    data=r.get_json()
    assert data['ok'] and isinstance(data['pronto'],bool)
    if data['pronto']:
        assert len(data['previsioni'])==14
        assert 0<=data['mae']<=1
        assert 0<=data['mae_baseline']<=1
    else:
        assert data['motivo']
