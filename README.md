# MASTER HOSPITALITY

Gestionale per hotel, B&B e case vacanza, con il modulo di revenue management **DynaPrice**. Parte della MASTER SUITE di Maurizio Gustinicchi Consulting: software di nostra produzione e proprietà.

## Cosa fa

| Pagina | Funzione |
|---|---|
| **Oggi** `/oggi` | Arrivi, partenze, ospiti in casa, camere da rifare, occupazione dei prossimi 14 giorni, opzioni in scadenza, mese a oggi. |
| **Planner** `/planner` | Camere × giorni; si trascinano i soggiorni per cambiare camera o date; clic su una casella libera per prenotare. |
| **Prenotazioni** `/prenotazioni` | Preventivo, opzione con scadenza, conferma, gruppi (più camere in un colpo), modifica con ricalcolo, cancellazione con penale secondo il piano, no show. |
| **Scheda prenotazione** | Conto (notti, extra, imposta di soggiorno, IVA), pagamenti e rimborsi, alloggiati, check-in e check-out, stampa del conto, testo della conferma per l'ospite, rischio di cancellazione. |
| **Ospiti** `/ospiti` | Schede con storico, documenti, consenso al marketing (export per newsletter) e anonimizzazione (GDPR). |
| **Housekeeping** `/housekeeping` | Camere per piano: da rifare, pulita, ispezionata, fuori servizio; partenze, fermate e arrivi. Si usa anche dal telefono. |
| **Alloggiati e imposta** `/alloggiati`, `/imposta-soggiorno` | File con il tracciato Alloggiati Web e riepilogo mensile dell'imposta di soggiorno (massimo di notti, età esenti, esenzioni). |
| **DynaPrice** `/revenue` | OTB, pickup, previsione dell'occupazione (curva delle prenotazioni + storico + eventi), prezzo suggerito con soglia di margine, overbooking, rischio di cancellazione, simulatore dei canali, commento dell'AI. |
| **Controllo** `/controllo` | Occupazione, ADR, RevPAR, TRevPAR, GOP, GOPPAR, CPOR, conto economico per reparti (USALI) fino all'EBITDA, margine per canale e per prenotazione (CSV). |
| **Struttura** `/struttura` | Parametri, tipologie, camere, piani tariffari, stagioni, canali e commissioni, eventi, restrizioni (soggiorno minimo, arrivi chiusi, stop vendite), costi fissi per reparto. |
| **Spazi** `/spazi` | Per il gestore: un hotel cliente o un allievo per spazio, con i dati separati, scadenza, classi del corso con credenziali generate, richieste di attivazione con bonifico. |

Ruoli: direzione (tutto), revenue manager, reception (senza DynaPrice, controllo e struttura), governante/piani (solo housekeeping).

## Corso con tutor

Ogni allievo riceve l'Hotel Le Logge (Firenze, 24 camere, storico e prenotazioni future) e un tutor laterale con 23 passi in 6 lezioni: struttura, tariffe e regole di vendita, prenotazioni e planner, front office, DynaPrice, controllo di gestione. Il tutor verifica da solo i dati dell'allievo; le domande finali si controllano sui numeri calcolati nel suo spazio.

## Variabili (Railway)

| Variabile | Uso |
|---|---|
| `DATABASE_URL` | PostgreSQL (Railway la imposta da solo) |
| `SECRET_KEY` | chiave delle sessioni |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | primo amministratore e gestore della piattaforma |
| `DEMO_DATA` | `1` (predefinito) carica l'Hotel Le Logge al primo avvio; `0` per partire vuoti |
| `DEMO_PASSWORD` | crea gli utenti di prova `reception.demo`, `governante.demo`, `revenue.demo` |
| `IBAN`, `INTESTATARIO_CONTO`, `PREZZO_TESTO` | attivazione con bonifico da `/abbonati` |
| `MAIL_AVVISI` | predefinito info@mauriziogustinicchiconsulting.it |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | invio email (465 = SSL) |
| `GEMINI_API_KEY` (o `GOOGLE_API_KEY`) | commenti dell'AI in DynaPrice e Controllo |

## Avvio in locale

```
pip install -r requirements.txt
ADMIN_USERNAME=admin ADMIN_PASSWORD=password-locale COOKIE_SICURO=0 flask --app app run
python -m pytest -q
```
