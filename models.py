"""MASTER HOSPITALITY — modelli.

Struttura (tipologie, camere, piani tariffari, stagioni, canali, eventi, costi), ospiti, prenotazioni con le notti
vendute, conto (addebiti e pagamenti) e alloggiati. Tutte le tabelle operative sono divise per spazio di lavoro
(un hotel cliente, un allievo del corso, una demo): vedi spazi.py.

Prezzi: si registrano IVA inclusa, come li vede l'ospite; ricavi e margini si calcolano al netto dell'IVA.
"""
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import declared_attr

db = SQLAlchemy()


class PerSpazio:
    """Tabelle divise per spazio di lavoro: vedi spazi.py."""
    @declared_attr
    def spazio_id(cls):
        return db.Column(db.Integer, db.ForeignKey('spazi.id', ondelete='CASCADE'), nullable=False, index=True, default=None)


class Spazio(db.Model):
    """Uno spazio di lavoro: un hotel o B&B cliente, un allievo di un corso, una demo."""
    __tablename__ = 'spazi'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    tipo = db.Column(db.String(20), default='azienda', nullable=False)   # azienda | formazione | demo
    scadenza = db.Column(db.Date, nullable=True)                         # oltre: sola lettura (None = senza scadenza)
    attivo = db.Column(db.Boolean, default=True, nullable=False)
    email = db.Column(db.String(200), default='')
    note = db.Column(db.String(500), default='')
    stripe_cliente = db.Column(db.String(80), default='')
    stripe_abbonamento = db.Column(db.String(80), default='')
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)

    def scaduto(self, oggi=None):
        from datetime import date as _d
        return (not self.attivo) or (self.scadenza is not None and self.scadenza < (oggi or _d.today()))


class Utente(db.Model):
    """Ruoli: admin (direzione, tutto), revenue (tutto tranne utenti), reception (front office), piani (housekeeping)."""
    __tablename__ = 'utenti'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(60), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    ruolo = db.Column(db.String(20), nullable=False, default='reception')
    attivo = db.Column(db.Boolean, default=True, nullable=False)
    spazio_id = db.Column(db.Integer, db.ForeignKey('spazi.id', ondelete='CASCADE'), nullable=False, default=1, index=True)
    gestore = db.Column(db.Boolean, default=False, nullable=False)   # gestore della piattaforma: crea e gestisce gli spazi
    ultimo_accesso = db.Column(db.DateTime, nullable=True)
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)


class Impostazione(db.Model):
    """Coppie chiave/valore (parametri della struttura, stato del tutor...), con la chiave prefissata dallo spazio."""
    __tablename__ = 'impostazioni'
    chiave = db.Column(db.String(80), primary_key=True)
    valore = db.Column(db.String(500), default='')

    @staticmethod
    def leggi(chiave, default=None):
        from spazi import chiave as k
        r = db.session.get(Impostazione, k(chiave))
        return r.valore if r else default

    @staticmethod
    def scrivi(chiave, valore):
        from spazi import chiave as k
        chiave = k(chiave)
        r = db.session.get(Impostazione, chiave) or Impostazione(chiave=chiave)
        r.valore = '' if valore is None else str(valore)
        db.session.add(r)


# ---------------- struttura ----------------

class TipoCamera(PerSpazio, db.Model):
    __tablename__ = 'tipi_camera'
    id = db.Column(db.Integer, primary_key=True)
    codice = db.Column(db.String(10), nullable=False)
    nome = db.Column(db.String(80), nullable=False)
    posti_base = db.Column(db.Integer, default=2, nullable=False)       # occupazione compresa nel prezzo
    posti_max = db.Column(db.Integer, default=2, nullable=False)
    prezzo_base = db.Column(db.Float, default=100, nullable=False)      # prezzo di listino a notte (BAR), IVA inclusa
    supplemento_persona = db.Column(db.Float, default=0)                # per ogni persona oltre posti_base, a notte
    costo_variabile = db.Column(db.Float, default=0)                    # per camera venduta a notte: pulizia, biancheria, cortesie, energia
    prezzo_minimo = db.Column(db.Float, nullable=True)                  # facoltativo: sotto non si scende
    prezzo_massimo = db.Column(db.Float, nullable=True)
    descrizione = db.Column(db.String(300), default='')
    ordine = db.Column(db.Integer, default=0)
    __table_args__ = (db.UniqueConstraint('spazio_id', 'codice', name='uq_tipo_spazio_codice'),)
    camere = db.relationship('Camera', back_populates='tipo', order_by='Camera.numero')


class Camera(PerSpazio, db.Model):
    __tablename__ = 'camere'
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(10), nullable=False)
    tipo_id = db.Column(db.Integer, db.ForeignKey('tipi_camera.id', ondelete='CASCADE'), nullable=False)
    piano = db.Column(db.String(10), default='')
    stato = db.Column(db.String(16), default='pulita', nullable=False)   # pulita | sporca | ispezionata | fuori_servizio
    attiva = db.Column(db.Boolean, default=True, nullable=False)
    note = db.Column(db.String(200), default='')
    __table_args__ = (db.UniqueConstraint('spazio_id', 'numero', name='uq_camera_spazio_numero'),)
    tipo = db.relationship('TipoCamera', back_populates='camere')


class PianoTariffario(PerSpazio, db.Model):
    """Condizioni di vendita: variazione sul listino, colazione, cancellazione, caparra."""
    __tablename__ = 'piani_tariffari'
    id = db.Column(db.Integer, primary_key=True)
    codice = db.Column(db.String(10), nullable=False)
    nome = db.Column(db.String(80), nullable=False)
    variazione_pct = db.Column(db.Float, default=0)          # -12 = 12% in meno del listino
    colazione = db.Column(db.Boolean, default=True)          # colazione compresa
    prezzo_colazione = db.Column(db.Float, default=0)        # per persona a notte, IVA inclusa, aggiunto al prezzo camera
    costo_colazione = db.Column(db.Float, default=0)         # costo per persona (materie prime)
    rimborsabile = db.Column(db.Boolean, default=True)
    giorni_cancellazione = db.Column(db.Integer, default=2)  # cancellazione gratuita fino a N giorni prima dell'arrivo
    caparra_pct = db.Column(db.Float, default=0)             # % del soggiorno da versare alla conferma
    attivo = db.Column(db.Boolean, default=True)
    __table_args__ = (db.UniqueConstraint('spazio_id', 'codice', name='uq_piano_spazio_codice'),)


class Stagione(PerSpazio, db.Model):
    __tablename__ = 'stagioni'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(60), nullable=False)
    dal = db.Column(db.Date, nullable=False)
    al = db.Column(db.Date, nullable=False)                  # compreso
    coefficiente = db.Column(db.Float, default=1.0)          # 1,20 = listino +20%


class Restrizione(PerSpazio, db.Model):
    """Per data (e tipologia, o tutte): soggiorno minimo all'arrivo, chiuso all'arrivo, chiuso alla vendita."""
    __tablename__ = 'restrizioni'
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Date, nullable=False, index=True)
    tipo_id = db.Column(db.Integer, db.ForeignKey('tipi_camera.id', ondelete='CASCADE'), nullable=True)
    min_notti = db.Column(db.Integer, default=1)
    chiuso_arrivo = db.Column(db.Boolean, default=False)
    chiuso = db.Column(db.Boolean, default=False)


class TariffaGiorno(PerSpazio, db.Model):
    """Prezzo deciso per un giorno e una tipologia (da DynaPrice o a mano): sostituisce listino × stagione."""
    __tablename__ = 'tariffe_giorno'
    id = db.Column(db.Integer, primary_key=True)
    tipo_id = db.Column(db.Integer, db.ForeignKey('tipi_camera.id', ondelete='CASCADE'), nullable=False)
    data = db.Column(db.Date, nullable=False, index=True)
    prezzo = db.Column(db.Float, nullable=False)
    origine = db.Column(db.String(12), default='manuale')    # manuale | dynaprice
    __table_args__ = (db.UniqueConstraint('spazio_id', 'tipo_id', 'data', name='uq_tariffa_giorno'),)


class Canale(PerSpazio, db.Model):
    __tablename__ = 'canali'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(60), nullable=False)
    tipo = db.Column(db.String(12), default='diretto')       # diretto | ota | agenzia
    commissione_pct = db.Column(db.Float, default=0)          # sul prezzo pagato dall'ospite
    attivo = db.Column(db.Boolean, default=True)
    colore = db.Column(db.String(10), default='#1971c2')


class Evento(PerSpazio, db.Model):
    """Fiere, concerti, festività: spostano la domanda (impatto in % sulla domanda attesa)."""
    __tablename__ = 'eventi'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    dal = db.Column(db.Date, nullable=False)
    al = db.Column(db.Date, nullable=False)
    impatto_pct = db.Column(db.Float, default=20)
    note = db.Column(db.String(200), default='')


REPARTI = {'camere': 'Reparto camere', 'fb': 'Food & beverage', 'amministrazione': 'Amministrazione e direzione',
           'commerciale': 'Vendite e marketing', 'manutenzione': 'Manutenzioni', 'utenze': 'Utenze ed energia',
           'oneri': 'Affitti, leasing e assicurazioni (sotto il GOP)'}


class CostoFisso(PerSpazio, db.Model):
    """Costi mensili per reparto (schema USALI): personale, utenze, manutenzioni... servono per GOP e GOPPAR."""
    __tablename__ = 'costi_fissi'
    id = db.Column(db.Integer, primary_key=True)
    voce = db.Column(db.String(100), nullable=False)
    reparto = db.Column(db.String(20), default='camere', nullable=False)
    importo_mensile = db.Column(db.Float, default=0)


# ---------------- ospiti e prenotazioni ----------------

class Ospite(PerSpazio, db.Model):
    __tablename__ = 'ospiti'
    id = db.Column(db.Integer, primary_key=True)
    cognome = db.Column(db.String(60), nullable=False)
    nome = db.Column(db.String(60), default='')
    email = db.Column(db.String(200), default='')
    telefono = db.Column(db.String(40), default='')
    lingua = db.Column(db.String(20), default='it')
    paese = db.Column(db.String(60), default='')
    sesso = db.Column(db.String(1), default='')
    data_nascita = db.Column(db.Date, nullable=True)
    luogo_nascita = db.Column(db.String(80), default='')
    provincia_nascita = db.Column(db.String(4), default='')
    stato_nascita = db.Column(db.String(60), default='')
    cittadinanza = db.Column(db.String(60), default='')
    tipo_documento = db.Column(db.String(30), default='')
    numero_documento = db.Column(db.String(30), default='')
    luogo_rilascio = db.Column(db.String(80), default='')
    consenso_marketing = db.Column(db.Boolean, default=False)
    consenso_il = db.Column(db.DateTime, nullable=True)
    note = db.Column(db.String(500), default='')
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def nominativo(self):
        return f'{self.cognome} {self.nome}'.strip()


STATI = {'preventivo': 'Preventivo', 'opzione': 'Opzione', 'confermata': 'Confermata', 'in_casa': 'In casa',
         'partita': 'Partita', 'cancellata': 'Cancellata', 'no_show': 'No show'}
OCCUPANO = ('opzione', 'confermata', 'in_casa', 'partita')    # stati che tengono occupata la camera


class Prenotazione(PerSpazio, db.Model):
    __tablename__ = 'prenotazioni'
    id = db.Column(db.Integer, primary_key=True)
    codice = db.Column(db.String(16), nullable=False)
    ospite_id = db.Column(db.Integer, db.ForeignKey('ospiti.id', ondelete='RESTRICT'), nullable=False)
    arrivo = db.Column(db.Date, nullable=False, index=True)
    partenza = db.Column(db.Date, nullable=False, index=True)
    adulti = db.Column(db.Integer, default=2, nullable=False)
    bambini = db.Column(db.Integer, default=0, nullable=False)
    tipo_id = db.Column(db.Integer, db.ForeignKey('tipi_camera.id', ondelete='RESTRICT'), nullable=False)
    camera_id = db.Column(db.Integer, db.ForeignKey('camere.id', ondelete='SET NULL'), nullable=True)
    piano_id = db.Column(db.Integer, db.ForeignKey('piani_tariffari.id', ondelete='RESTRICT'), nullable=False)
    canale_id = db.Column(db.Integer, db.ForeignKey('canali.id', ondelete='RESTRICT'), nullable=False)
    stato = db.Column(db.String(12), default='confermata', nullable=False, index=True)
    opzione_fino = db.Column(db.Date, nullable=True)
    importo = db.Column(db.Float, default=0)                 # soggiorno (camera + colazione), IVA inclusa
    caparra = db.Column(db.Float, default=0)                 # richiesta alla conferma
    penale = db.Column(db.Float, default=0)                  # trattenuta in caso di cancellazione o no show
    gruppo = db.Column(db.String(80), default='')
    rif_canale = db.Column(db.String(40), default='')
    note = db.Column(db.String(500), default='')
    creato_il = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    cancellata_il = db.Column(db.DateTime, nullable=True)
    checkin_il = db.Column(db.DateTime, nullable=True)
    checkout_il = db.Column(db.DateTime, nullable=True)
    __table_args__ = (db.UniqueConstraint('spazio_id', 'codice', name='uq_prenotazione_spazio_codice'),)

    ospite = db.relationship('Ospite', lazy='joined')
    tipo = db.relationship('TipoCamera', lazy='joined')
    camera = db.relationship('Camera', lazy='joined')
    piano = db.relationship('PianoTariffario', lazy='joined')
    canale = db.relationship('Canale', lazy='joined')
    notti_vendute = db.relationship('NotteVenduta', cascade='all, delete-orphan', order_by='NotteVenduta.data')
    addebiti = db.relationship('Addebito', cascade='all, delete-orphan', order_by='Addebito.data')
    pagamenti = db.relationship('Pagamento', cascade='all, delete-orphan', order_by='Pagamento.data')
    alloggiati = db.relationship('Alloggiato', cascade='all, delete-orphan', order_by='Alloggiato.id')

    @property
    def notti(self):
        return (self.partenza - self.arrivo).days

    @property
    def persone(self):
        return (self.adulti or 0) + (self.bambini or 0)


class NotteVenduta(PerSpazio, db.Model):
    """Una notte del soggiorno con il suo prezzo: base di occupazione, ADR, RevPAR e ricavi per data."""
    __tablename__ = 'notti_vendute'
    id = db.Column(db.Integer, primary_key=True)
    prenotazione_id = db.Column(db.Integer, db.ForeignKey('prenotazioni.id', ondelete='CASCADE'), nullable=False, index=True)
    data = db.Column(db.Date, nullable=False, index=True)
    prezzo = db.Column(db.Float, default=0)                  # camera + colazione, IVA inclusa
    colazione = db.Column(db.Float, default=0)               # di cui colazione


REP_ADDEBITO = {'colazione': 'Colazione', 'bar': 'Bar e minibar', 'ristorante': 'Ristorante', 'servizi': 'Servizi (parcheggio, transfer, lavanderia)',
                'extra': 'Altro'}


class Addebito(PerSpazio, db.Model):
    __tablename__ = 'addebiti'
    id = db.Column(db.Integer, primary_key=True)
    prenotazione_id = db.Column(db.Integer, db.ForeignKey('prenotazioni.id', ondelete='CASCADE'), nullable=False, index=True)
    data = db.Column(db.Date, nullable=False)
    descrizione = db.Column(db.String(120), nullable=False)
    reparto = db.Column(db.String(12), default='extra')
    quantita = db.Column(db.Float, default=1)
    prezzo = db.Column(db.Float, default=0)                  # unitario, IVA inclusa
    iva_pct = db.Column(db.Float, default=10)
    costo = db.Column(db.Float, default=0)                   # unitario: costo variabile per il margine

    @property
    def totale(self):
        return round((self.quantita or 0) * (self.prezzo or 0), 2)


METODI = {'contanti': 'Contanti', 'carta': 'Carta', 'bonifico': 'Bonifico', 'ota': 'Incassato dal portale', 'altro': 'Altro'}


class Pagamento(PerSpazio, db.Model):
    __tablename__ = 'pagamenti'
    id = db.Column(db.Integer, primary_key=True)
    prenotazione_id = db.Column(db.Integer, db.ForeignKey('prenotazioni.id', ondelete='CASCADE'), nullable=False, index=True)
    data = db.Column(db.Date, nullable=False)
    importo = db.Column(db.Float, nullable=False)            # negativo = rimborso
    metodo = db.Column(db.String(12), default='carta')
    nota = db.Column(db.String(120), default='')


class Alloggiato(PerSpazio, db.Model):
    """Persona alloggiata, per la comunicazione alla Questura (Alloggiati Web) e l'imposta di soggiorno."""
    __tablename__ = 'alloggiati'
    id = db.Column(db.Integer, primary_key=True)
    prenotazione_id = db.Column(db.Integer, db.ForeignKey('prenotazioni.id', ondelete='CASCADE'), nullable=False, index=True)
    tipo = db.Column(db.String(2), default='16')             # 16 ospite singolo, 17 capofamiglia, 18 capogruppo, 19 familiare, 20 membro gruppo
    cognome = db.Column(db.String(60), nullable=False)
    nome = db.Column(db.String(60), nullable=False)
    sesso = db.Column(db.String(1), default='')
    data_nascita = db.Column(db.Date, nullable=True)
    luogo_nascita = db.Column(db.String(80), default='')
    provincia_nascita = db.Column(db.String(4), default='')
    stato_nascita = db.Column(db.String(60), default='')
    cittadinanza = db.Column(db.String(60), default='')
    tipo_documento = db.Column(db.String(30), default='')
    numero_documento = db.Column(db.String(30), default='')
    luogo_rilascio = db.Column(db.String(80), default='')
    esente_tassa = db.Column(db.Boolean, default=False)
    motivo_esenzione = db.Column(db.String(60), default='')
