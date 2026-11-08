"""Email automatiche agli ospiti: conferma della prenotazione, promemoria prima dell'arrivo, ringraziamento dopo la
partenza con l'invito alla recensione. Modelli modificabili con i segnaposto {cognome}, {codice}, {arrivo}...;
ogni invio si registra (niente doppioni). Senza SMTP configurato l'email si registra come «anteprima»."""
from datetime import date, timedelta

import hotel as h
from models import EVENTI_EMAIL, EmailInviata, ModelloEmail, Prenotazione, db

PREDEFINITI = {
    'conferma': ('Conferma della prenotazione {codice} — {struttura}',
                 'Gentile {nome} {cognome},\n\ngrazie per aver scelto {struttura}. Le confermiamo la prenotazione {codice}:\n'
                 'arrivo {arrivo} (check-in dalle {checkin}), partenza {partenza} (check-out entro le {checkout}),\n'
                 '{camera}, {persone} persone, trattamento {piano}.\nTotale del soggiorno: {totale} IVA inclusa.{caparra}\n\n'
                 '{condizioni}\nL\'imposta di soggiorno si paga in struttura.\n\nPer qualsiasi necessità risponda a questa email.\n\n'
                 'Cordiali saluti\n{struttura}'),
    'pre_arrivo': ('La aspettiamo il {arrivo} — {struttura}',
                   'Gentile {nome} {cognome},\n\nmancano pochi giorni al suo arrivo ({arrivo}). Il check-in è dalle {checkin}.\n'
                   'Per velocizzare l\'accoglienza tenga pronto un documento d\'identità per ogni ospite.\n'
                   'Ci scriva se desidera un parcheggio, un transfer o una richiesta particolare.\n\nA presto\n{struttura}'),
    'post_soggiorno': ('Grazie per il soggiorno — {struttura}',
                       'Gentile {nome} {cognome},\n\ngrazie per essere stato nostro ospite. La sua opinione ci aiuta a migliorare:\n'
                       'se ha due minuti, ci lasci una recensione.\nPer il prossimo soggiorno prenoti direttamente sul nostro sito: '
                       'il prezzo migliore è sempre lì.\n\nCordiali saluti\n{struttura}'),
}


def modello(evento):
    m = ModelloEmail.query.filter_by(evento=evento).first()
    if m is None:
        ogg, testo = PREDEFINITI[evento]
        m = ModelloEmail(evento=evento, oggetto=ogg, testo=testo, attivo=True)
        db.session.add(m)
        db.session.flush()
    return m


def campi(p):
    return {'nome': p.ospite.nome or '', 'cognome': p.ospite.cognome, 'codice': p.codice, 'struttura': h.par('nome_struttura'),
            'arrivo': p.arrivo.strftime('%d/%m/%Y'), 'partenza': p.partenza.strftime('%d/%m/%Y'), 'checkin': h.par('ora_checkin'),
            'checkout': h.par('ora_checkout'), 'camera': p.tipo.nome, 'persone': p.persone, 'piano': p.piano.nome,
            'totale': f'{p.importo:.2f} €'.replace('.', ','), 'condizioni': h.condizioni(p.piano),
            'caparra': (f'\nCaparra da versare: {p.caparra:.2f} €'.replace('.', ',') + (f' entro il {p.opzione_fino.strftime("%d/%m/%Y")}' if p.opzione_fino else '')) if p.caparra else ''}


class _Sicuro(dict):
    def __missing__(self, k):
        return '{' + k + '}'


def componi(p, evento):
    m = modello(evento)
    c = _Sicuro(campi(p))
    return m.oggetto.format_map(c), m.testo.format_map(c), m.attivo


def gia_inviata(p, evento):
    return EmailInviata.query.filter_by(prenotazione_id=p.id, evento=evento).filter(EmailInviata.esito != 'errore').first() is not None


def invia_evento(p, evento, forza=False):
    """Invia (una volta sola) l'email dell'evento alla prenotazione. Ritorna l'esito registrato o None se saltata."""
    if not p.ospite or not p.ospite.email or (gia_inviata(p, evento) and not forza):
        return None
    oggetto, testo, attivo = componi(p, evento)
    if not attivo:
        return None
    try:
        from blueprints.abbonamento import invia_email
        ok = invia_email(p.ospite.email, oggetto, testo, rispondi_a=h.par('email_struttura') or None)
        esito = 'ok' if ok else 'anteprima'
    except Exception:   # noqa: BLE001
        esito = 'errore'
    db.session.add(EmailInviata(prenotazione_id=p.id, evento=evento, destinatario=p.ospite.email, esito=esito))
    return esito


def da_inviare(oggi=None):
    """Le email di oggi: promemoria per chi arriva fra 3 giorni, ringraziamento per chi è partito ieri."""
    oggi = oggi or date.today()
    out = []
    for p in Prenotazione.query.filter(Prenotazione.arrivo == oggi + timedelta(days=3), Prenotazione.stato == 'confermata').all():
        if not gia_inviata(p, 'pre_arrivo'):
            out.append((p, 'pre_arrivo'))
    for p in Prenotazione.query.filter(Prenotazione.partenza.in_((oggi, oggi - timedelta(days=1))), Prenotazione.stato == 'partita').all():
        if not gia_inviata(p, 'post_soggiorno'):
            out.append((p, 'post_soggiorno'))
    return [(p, e) for p, e in out if p.ospite and p.ospite.email]


def invia_quelle_di_oggi(oggi=None):
    n = 0
    for p, e in da_inviare(oggi):
        if invia_evento(p, e):
            n += 1
    return n
