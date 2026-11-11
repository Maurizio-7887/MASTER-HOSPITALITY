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


def test_ota_lab_sync_timeout_recovery(demo,client):
    from models import Canale, OTAEvento
    c=Canale.query.filter_by(tipo='ota',attivo=True).first()
    page=client.get('/ota-lab')
    assert page.status_code==200
    assert b'Solo laboratorio didattico' in page.data
    payload={'canale_id':c.id,'giorni':3,'simula_errore':False}
    ok=client.post('/api/ota-lab/sincronizza',json=payload)
    assert ok.status_code==200 and ok.get_json()['ok']
    assert len(ok.get_json()['righe'])>=3
    bad=client.post('/api/ota-lab/sincronizza',json={**payload,'simula_errore':True})
    assert bad.status_code==503
    evento_id=bad.get_json()['evento_id']
    assert OTAEvento.query.filter_by(stato='errore').count()==1
    fixed=client.post('/api/ota-lab/recupera',json={'evento_id':evento_id})
    assert fixed.status_code==200 and fixed.get_json()['ok']
    assert OTAEvento.query.filter_by(stato='risolto').count()==1
    repeat=client.post('/api/ota-lab/recupera',json={'evento_id':evento_id})
    assert repeat.get_json()['duplicato']
    assert client.post('/api/ota-lab/recupera',json={'evento_id':999999}).status_code==404

def test_ota_lab_rejects_invalid_channel(demo,client):
    from models import Canale
    diretto=Canale.query.filter_by(tipo='diretto').first()
    r=client.post('/api/ota-lab/sincronizza',json={'canale_id':diretto.id})
    assert r.status_code==400
    assert client.post('/api/ota-lab/sincronizza',json={'canale_id':'nope'}).status_code==400
