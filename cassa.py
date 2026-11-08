"""Cassa: conti separati (ospite e azienda), ricevute e fatture elettroniche con numerazione progressiva, crediti verso
le aziende (city ledger), vendite al punto cassa e chiusura della giornata (night audit).

Come nei PMS del mercato (Opera, Mews, Scrigno, Passepartout):
- il conto della prenotazione si divide: notti a carico dell'azienda se la prenotazione ha un'azienda, extra sul conto
  che si sceglie a ogni addebito (ospite o azienda);
- il conto dell'azienda si chiude con una fattura «a credito»: il saldo passa al conto aziende e si incassa dopo;
- ogni documento ha numero progressivo per anno e tipo; la fattura ha il file XML FatturaPA (FPR12);
- la chiusura del giorno controlla arrivi, partenze, conti e camere, fissa i numeri della giornata e non si ripete.
"""
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape

import hotel as h
from models import (OCCUPANO, Addebito, Azienda, Camera, ChiusuraGiorno, Documento, NotteVenduta, Pagamento, Prenotazione,
                    VenditaCassa, db)

METODO_CREDITO = 'azienda'


class ErroreCassa(h.Errore):
    pass


def r2(x):
    return round(x + 1e-9, 2)


# ---------------- conti separati ----------------

def conti(p):
    """{'ospite': {...}, 'azienda': {...}}: righe, totale, pagato e saldo di ciascun conto della prenotazione."""
    c = h.conto(p)
    out = {k: {'righe': [], 'totale': 0.0, 'pagato': 0.0} for k in ('ospite', 'azienda')}
    addebiti = {a.id: a for a in p.addebiti}
    for r in c['righe']:
        if r['tipo'] == 'addebito':
            dove = (addebiti[r['id']].conto or 'ospite') if p.azienda_id else 'ospite'
        else:
            dove = 'azienda' if p.azienda_id else 'ospite'
        out[dove]['righe'].append(r)
    if c['tassa']['totale']:
        out['ospite']['righe'].append({'data': p.partenza.isoformat(), 'descrizione': 'Imposta di soggiorno', 'quantita': 1,
                                       'prezzo': c['tassa']['totale'], 'totale': c['tassa']['totale'], 'iva': None, 'tipo': 'tassa'})
    for k in out:
        out[k]['totale'] = r2(sum(r['totale'] for r in out[k]['righe']))
    for x in p.pagamenti:
        dove = 'azienda' if x.metodo == METODO_CREDITO else 'ospite'
        out[dove]['pagato'] = r2(out[dove]['pagato'] + x.importo)
    for k in out:
        out[k]['saldo'] = r2(out[k]['totale'] - out[k]['pagato'])
    return out


# ---------------- documenti ----------------

def _righe_documento(righe):
    """Righe del documento con imponibile e IVA scorporati (prezzi IVA inclusa); la tassa di soggiorno è fuori campo."""
    out = []
    for r in righe:
        aliq = r['iva']
        lordo = r2(r['totale'])
        if aliq is None:
            out.append({'descrizione': r['descrizione'], 'quantita': r['quantita'], 'lordo': lordo, 'aliquota': 0, 'natura': 'N1',
                        'imponibile': lordo, 'iva': 0.0})
        else:
            imp = r2(lordo / (1 + aliq / 100))
            out.append({'descrizione': r['descrizione'], 'quantita': r['quantita'], 'lordo': lordo, 'aliquota': aliq, 'natura': '',
                        'imponibile': imp, 'iva': r2(lordo - imp)})
    return out


def riepilogo(righe):
    """Totali per aliquota (o natura), come nel blocco DatiRiepilogo della FatturaPA."""
    g = defaultdict(lambda: {'imponibile': 0.0, 'lordo': 0.0})
    for r in righe:
        k = (r['aliquota'], r['natura'])
        g[k]['imponibile'] += r['lordo'] if r['natura'] else r['lordo'] / (1 + r['aliquota'] / 100)
        g[k]['lordo'] += r['lordo']
    out = []
    for (aliq, nat), v in sorted(g.items()):
        imp = r2(v['imponibile'])
        out.append({'aliquota': aliq, 'natura': nat, 'imponibile': imp, 'iva': r2(v['lordo'] - imp) if not nat else 0.0, 'lordo': r2(v['lordo'])})
    return out


def prossimo_numero(tipo, anno):
    ultimo = Documento.query.filter_by(tipo=tipo, anno=anno).order_by(Documento.numero.desc()).first()
    return (ultimo.numero + 1) if ultimo else 1


def emetti(p, tipo='ricevuta', conto='ospite', azienda=None, il=None):
    """Emette ricevuta o fattura per un conto della prenotazione. La fattura all'azienda del suo conto è «a credito»:
    il saldo passa al conto aziende (pagamento di tipo «azienda») e si incassa più tardi."""
    il = il or date.today()
    if tipo not in ('ricevuta', 'fattura'):
        raise ErroreCassa('Tipo di documento non valido')
    if p.stato in ('preventivo', 'opzione'):
        raise ErroreCassa('Prima conferma la prenotazione')
    cc = conti(p)
    if conto not in cc:
        raise ErroreCassa('Conto non valido')
    if Documento.query.filter(Documento.prenotazione_id == p.id, Documento.tipo != 'nota_credito',
                              Documento.righe.like(f'%"conto": "{conto}"%')).first():
        raise ErroreCassa(f'Per il conto {conto} di questa prenotazione il documento è già stato emesso (per correggerlo emetti una nota di credito)')
    righe = cc[conto]['righe']
    if not righe:
        raise ErroreCassa('Il conto è vuoto')
    azienda = azienda or (p.azienda if conto == 'azienda' else None)
    if tipo == 'fattura' and azienda is None and conto == 'azienda':
        raise ErroreCassa('Indica l\'azienda da fatturare')
    rd = _righe_documento(righe)
    rie = riepilogo(rd)
    d = Documento(tipo=tipo, anno=il.year, numero=prossimo_numero(tipo, il.year), data=il, prenotazione_id=p.id,
                  azienda_id=azienda.id if azienda else None,
                  intestatario=(azienda.ragione_sociale if azienda else p.ospite.nominativo)[:160],
                  imponibile=r2(sum(x['imponibile'] for x in rie if not x['natura'])), iva=r2(sum(x['iva'] for x in rie)),
                  esente=r2(sum(x['lordo'] for x in rie if x['natura'])), totale=r2(sum(x['lordo'] for x in rie)),
                  righe=json.dumps({'conto': conto, 'righe': rd, 'riepilogo': rie}, ensure_ascii=False))
    if conto == 'azienda' and azienda is not None:
        d.a_credito = True
        saldo = cc['azienda']['saldo']
        if saldo > 0.009:
            h.registra_pagamento(p, saldo, METODO_CREDITO, f'A credito: fattura {d.numero}/{d.anno}', il)
    db.session.add(d)
    db.session.flush()
    if tipo == 'fattura':
        d.xml = fattura_xml(d)
    return d


def nota_di_credito(doc, il=None):
    """Storna un documento (per correggerlo): stesso importo con segno negativo, nuova numerazione."""
    il = il or date.today()
    if doc.tipo == 'nota_credito':
        raise ErroreCassa('Non si storna una nota di credito')
    dati = json.loads(doc.righe or '{}')
    righe = [dict(r, lordo=-r['lordo'], imponibile=-r['imponibile'], iva=-r['iva']) for r in dati.get('righe', [])]
    dati['conto'] = f"storno-{dati.get('conto', '')}"
    dati['righe'] = righe
    dati['riepilogo'] = [dict(x, imponibile=-x['imponibile'], iva=-x['iva'], lordo=-x['lordo']) for x in dati.get('riepilogo', [])]
    dati['storna'] = doc.sigla
    nc = Documento(tipo='nota_credito', anno=il.year, numero=prossimo_numero('nota_credito', il.year), data=il,
                   prenotazione_id=doc.prenotazione_id, azienda_id=doc.azienda_id, intestatario=doc.intestatario,
                   imponibile=-doc.imponibile, iva=-doc.iva, esente=-doc.esente, totale=-doc.totale, righe=json.dumps(dati, ensure_ascii=False))
    # il documento stornato si può riemettere: si libera il suo conto
    vecchio = json.loads(doc.righe or '{}')
    vecchio['conto'] = f"stornato-{vecchio.get('conto', '')}"
    doc.righe = json.dumps(vecchio, ensure_ascii=False)
    if doc.a_credito and not doc.incassato_il and doc.prenotazione_id:
        p = db.session.get(Prenotazione, doc.prenotazione_id)
        h.registra_pagamento(p, -doc.totale, METODO_CREDITO, f'Storno credito {doc.sigla}', il)
        doc.a_credito = False
    db.session.add(nc)
    db.session.flush()
    if doc.tipo == 'fattura':
        nc.xml = fattura_xml(nc)
    return nc


def incassa_credito(doc, metodo='bonifico', il=None):
    if not doc.a_credito or doc.incassato_il:
        raise ErroreCassa('Il documento non ha un credito da incassare')
    doc.incassato_il = il or date.today()
    return doc


def crediti_aperti():
    oggi = date.today()
    out = []
    for d in Documento.query.filter_by(a_credito=True, incassato_il=None).order_by(Documento.data).all():
        giorni = (d.azienda.giorni_pagamento if d.azienda else 30) or 30
        scad = d.data + timedelta(days=giorni)
        out.append({'d': d, 'scadenza': scad, 'scaduto': scad < oggi, 'giorni': (oggi - scad).days})
    return out


# ---------------- FatturaPA ----------------

def _x(tag, v):
    return f'<{tag}>{escape(str(v))}</{tag}>' if v not in (None, '') else ''


def fattura_xml(d):
    """File XML della fattura elettronica (FatturaPA 1.2, formato FPR12) da trasmettere allo SdI tramite il proprio
    intermediario. Il gestionale non lo firma e non lo invia."""
    dati = json.loads(d.righe or '{}')
    a = d.azienda
    P = h.par
    piva = (P('partita_iva') or '').replace(' ', '')
    tipo_doc = 'TD04' if d.tipo == 'nota_credito' else 'TD01'
    progr = f'{d.anno % 100:02d}{d.numero:05d}'
    if a:
        cess = ('<CessionarioCommittente><DatiAnagrafici>'
                + (f'<IdFiscaleIVA><IdPaese>{escape(a.nazione or "IT")}</IdPaese><IdCodice>{escape(a.partita_iva)}</IdCodice></IdFiscaleIVA>' if a.partita_iva else '')
                + _x('CodiceFiscale', a.codice_fiscale) + f'<Anagrafica>{_x("Denominazione", a.ragione_sociale)}</Anagrafica></DatiAnagrafici>'
                + f'<Sede>{_x("Indirizzo", a.indirizzo or "-")}{_x("CAP", a.cap or "00000")}{_x("Comune", a.comune or "-")}{_x("Provincia", a.provincia)}{_x("Nazione", a.nazione or "IT")}</Sede>'
                + '</CessionarioCommittente>')
        dest, pec = (a.codice_sdi or '0000000'), a.pec
    else:
        cess = (f'<CessionarioCommittente><DatiAnagrafici><Anagrafica>{_x("Denominazione", d.intestatario)}</Anagrafica></DatiAnagrafici>'
                '<Sede><Indirizzo>-</Indirizzo><CAP>00000</CAP><Comune>-</Comune><Nazione>IT</Nazione></Sede></CessionarioCommittente>')
        dest, pec = '0000000', ''
    linee = []
    for i, r in enumerate(dati.get('righe', []), 1):
        tot = r['imponibile']
        qta = r.get('quantita') or 1
        linee.append(f'<DettaglioLinee><NumeroLinea>{i}</NumeroLinea>{_x("Descrizione", r["descrizione"][:1000])}'
                     f'<Quantita>{qta:.2f}</Quantita><PrezzoUnitario>{tot / qta:.2f}</PrezzoUnitario><PrezzoTotale>{tot:.2f}</PrezzoTotale>'
                     f'<AliquotaIVA>{r["aliquota"]:.2f}</AliquotaIVA>{_x("Natura", r["natura"])}</DettaglioLinee>')
    rie = ''.join(f'<DatiRiepilogo><AliquotaIVA>{x["aliquota"]:.2f}</AliquotaIVA>{_x("Natura", x["natura"])}'
                  f'<ImponibileImporto>{x["imponibile"]:.2f}</ImponibileImporto><Imposta>{x["iva"]:.2f}</Imposta>'
                  + ('<RiferimentoNormativo>Escluse art. 15 DPR 633/72 (imposta di soggiorno)</RiferimentoNormativo>' if x['natura'] == 'N1' else '<EsigibilitaIVA>I</EsigibilitaIVA>')
                  + '</DatiRiepilogo>' for x in dati.get('riepilogo', []))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<p:FatturaElettronica versione="FPR12" xmlns:p="http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2" '
            'xmlns:ds="http://www.w3.org/2000/09/xmldsig#" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<FatturaElettronicaHeader><DatiTrasmissione>'
            f'<IdTrasmittente><IdPaese>IT</IdPaese><IdCodice>{escape(piva or "00000000000")}</IdCodice></IdTrasmittente>'
            f'<ProgressivoInvio>{progr}</ProgressivoInvio><FormatoTrasmissione>FPR12</FormatoTrasmissione>'
            f'<CodiceDestinatario>{escape(dest)}</CodiceDestinatario>{_x("PECDestinatario", pec if dest == "0000000" else "")}</DatiTrasmissione>'
            '<CedentePrestatore><DatiAnagrafici>'
            f'<IdFiscaleIVA><IdPaese>IT</IdPaese><IdCodice>{escape(piva or "00000000000")}</IdCodice></IdFiscaleIVA>'
            + _x('CodiceFiscale', P('codice_fiscale_struttura'))
            + f'<Anagrafica>{_x("Denominazione", P("ragione_sociale") or P("nome_struttura"))}</Anagrafica>'
            f'<RegimeFiscale>{escape(P("regime_fiscale") or "RF01")}</RegimeFiscale></DatiAnagrafici>'
            f'<Sede>{_x("Indirizzo", P("indirizzo") or "-")}{_x("CAP", P("cap") or "00000")}{_x("Comune", P("comune") or "-")}'
            f'{_x("Provincia", P("provincia"))}<Nazione>IT</Nazione></Sede></CedentePrestatore>'
            f'{cess}</FatturaElettronicaHeader>'
            '<FatturaElettronicaBody><DatiGenerali><DatiGeneraliDocumento>'
            f'<TipoDocumento>{tipo_doc}</TipoDocumento><Divisa>EUR</Divisa><Data>{d.data.isoformat()}</Data><Numero>{d.numero}/{d.anno}</Numero>'
            f'<ImportoTotaleDocumento>{d.totale:.2f}</ImportoTotaleDocumento></DatiGeneraliDocumento></DatiGenerali>'
            f'<DatiBeniServizi>{"".join(linee)}{rie}</DatiBeniServizi></FatturaElettronicaBody></p:FatturaElettronica>\n')


# ---------------- punto cassa ----------------

def vendita(reparto, descrizione, quantita, prezzo, iva=10, costo=0, metodo='contanti', il=None, ricevuta=True):
    """Vendita a un cliente di passaggio (bar, ristorante), pagata subito, con la sua ricevuta."""
    if not descrizione:
        raise ErroreCassa('Scrivi che cosa hai venduto')
    il = il or date.today()
    v = VenditaCassa(data=il, reparto=reparto, descrizione=descrizione[:120], quantita=float(quantita or 1), prezzo=r2(float(prezzo)),
                     iva_pct=float(iva), costo=float(costo or 0), metodo=metodo)
    if v.totale <= 0:
        raise ErroreCassa('Importo non valido')
    db.session.add(v)
    if ricevuta:
        rd = _righe_documento([{'descrizione': v.descrizione, 'quantita': v.quantita, 'totale': v.totale, 'iva': v.iva_pct}])
        rie = riepilogo(rd)
        d = Documento(tipo='ricevuta', anno=il.year, numero=prossimo_numero('ricevuta', il.year), data=il, intestatario='Cliente al banco',
                      imponibile=r2(sum(x['imponibile'] for x in rie)), iva=r2(sum(x['iva'] for x in rie)), totale=v.totale,
                      righe=json.dumps({'conto': 'banco', 'righe': rd, 'riepilogo': rie}, ensure_ascii=False))
        db.session.add(d)
        db.session.flush()
        v.documento_id = d.id
    return v


# ---------------- giornata e chiusura ----------------

def data_di_lavoro():
    """Il giorno dopo l'ultima chiusura (o oggi, se non si è mai chiuso)."""
    ultima = ChiusuraGiorno.query.order_by(ChiusuraGiorno.data.desc()).first()
    return (ultima.data + timedelta(days=1)) if ultima else date.today()


def giornata(d):
    """I numeri di una giornata: camere vendute, ricavi camere ed extra, incassi per metodo, documenti emessi."""
    tot = len(h.camere_attive())
    notti = (NotteVenduta.query.join(Prenotazione, NotteVenduta.prenotazione_id == Prenotazione.id)
             .filter(NotteVenduta.data == d, Prenotazione.stato.in_(OCCUPANO)).all())
    camere = sum(n.prezzo - n.colazione for n in notti)
    colazioni = sum(n.colazione for n in notti)
    extra = sum(a.totale for a in Addebito.query.filter(Addebito.data == d).all())
    banco = VenditaCassa.query.filter(VenditaCassa.data == d).all()
    penali = sum(p.penale or 0 for p in Prenotazione.query.filter(Prenotazione.stato.in_(('cancellata', 'no_show')),
                                                                  db.func.date(Prenotazione.cancellata_il) == d).all())
    incassi = defaultdict(float)
    for x in Pagamento.query.filter(Pagamento.data == d).all():
        incassi[x.metodo] += x.importo
    for v in banco:
        incassi[v.metodo] += v.totale
    documenti = Documento.query.filter(Documento.data == d).order_by(Documento.tipo, Documento.numero).all()
    return {'data': d, 'camere_vendute': len(notti), 'camere_totali': tot, 'occupazione': len(notti) / tot if tot else 0,
            'ricavo_camere': r2(camere), 'colazioni': r2(colazioni), 'extra': r2(extra), 'banco': r2(sum(v.totale for v in banco)),
            'penali': r2(penali), 'adr': r2(camere / len(notti)) if notti else 0, 'revpar': r2(camere / tot) if tot else 0,
            'incassi': {k: r2(v) for k, v in sorted(incassi.items())},
            'incassato': r2(sum(v for k, v in incassi.items() if k != METODO_CREDITO)), 'documenti': documenti}


def controlli_chiusura(d):
    """Che cosa sistemare prima di chiudere la giornata (come il night audit dei PMS)."""
    avvisi = []
    for p in Prenotazione.query.filter(Prenotazione.arrivo <= d, Prenotazione.stato == 'confermata').all():
        avvisi.append(('arrivo', p, f'{p.ospite.nominativo}: arrivo del {p.arrivo.strftime("%d/%m")} non registrato (check-in o no show)'))
    for p in Prenotazione.query.filter(Prenotazione.partenza <= d, Prenotazione.stato == 'in_casa').all():
        avvisi.append(('partenza', p, f'{p.ospite.nominativo}: partenza del {p.partenza.strftime("%d/%m")} senza check-out'))
    for p in Prenotazione.query.filter(Prenotazione.stato == 'partita', Prenotazione.partenza == d).all():
        saldo = conti(p)['ospite']['saldo']
        if saldo > 0.009:
            avvisi.append(('saldo', p, f'{p.ospite.nominativo}: partito con {saldo:.2f} € da incassare'.replace('.', ',')))
        if not Documento.query.filter_by(prenotazione_id=p.id).first():
            avvisi.append(('documento', p, f'{p.ospite.nominativo}: partito senza ricevuta o fattura'))
    for p in Prenotazione.query.filter(Prenotazione.stato == 'in_casa', Prenotazione.arrivo <= d).all():
        if not p.alloggiati:
            avvisi.append(('alloggiati', p, f'{p.ospite.nominativo}: in casa senza schedina alloggiati'))
    return avvisi


def chiudi(d, utente='', note='', forza=False):
    if ChiusuraGiorno.query.filter_by(data=d).first():
        raise ErroreCassa(f'La giornata del {d.strftime("%d/%m/%Y")} è già chiusa')
    if d > date.today():
        raise ErroreCassa('Non si chiude una giornata futura')
    if d != data_di_lavoro() and ChiusuraGiorno.query.first() is not None:
        raise ErroreCassa(f'Prima chiudi il {data_di_lavoro().strftime("%d/%m/%Y")}')
    avvisi = [a for a in controlli_chiusura(d) if a[0] in ('arrivo', 'partenza')]
    if avvisi and not forza:
        raise ErroreCassa(f'{len(avvisi)} arrivi o partenze da sistemare prima della chiusura')
    gi = giornata(d)
    c = ChiusuraGiorno(data=d, utente=utente[:60], camere_vendute=gi['camere_vendute'], camere_totali=gi['camere_totali'],
                       ricavo_camere=gi['ricavo_camere'], ricavo_extra=r2(gi['extra'] + gi['banco'] + gi['colazioni']),
                       incassi=json.dumps(gi['incassi']), note=note[:500])
    db.session.add(c)
    for p in Pagamento.query.filter(Pagamento.data == d, Pagamento.chiusura_id.is_(None)).all():
        p.chiusura_id = -1
    db.session.flush()
    for p in Pagamento.query.filter(Pagamento.data == d, Pagamento.chiusura_id == -1).all():
        p.chiusura_id = c.id
    return c


def conti_in_casa():
    out = []
    for p in Prenotazione.query.filter(Prenotazione.stato == 'in_casa').order_by(Prenotazione.partenza).all():
        cc = conti(p)
        out.append({'p': p, 'ospite': cc['ospite'], 'azienda': cc['azienda']})
    return out
