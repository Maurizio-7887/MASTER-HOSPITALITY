import os


def _database_url():
    url = os.environ.get('DATABASE_URL', '')
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]
    return url or 'sqlite:///masterhospitality.db'


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-masterhospitality-cambiami-in-produzione')
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False


    # Primo amministratore (facoltativo: in alternativa la pagina /primo-avvio)
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', '')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
    # Cookie di sessione solo su HTTPS in produzione (Railway); 0 per provare in locale senza HTTPS
    SESSION_COOKIE_SECURE = os.environ.get('COOKIE_SICURO', '1') == '1' and bool(os.environ.get('DATABASE_URL'))
    # Dati dimostrativi al primo avvio (azienda simulata)
    DEMO_DATA = os.environ.get('DEMO_DATA', '1') == '1'
    DEMO_PASSWORD = os.environ.get('DEMO_PASSWORD', '')   # crea gli utenti di prova reception.demo e piani.demo
    # Intestazione dei report stampati
    RAGIONE_SOCIALE = os.environ.get('RAGIONE_SOCIALE', '')

    # abbonamento online (Stripe)
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
    STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID', '')
    STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
    PREZZO_TESTO = os.environ.get('PREZZO_TESTO', '')
    GIORNI_PROVA = os.environ.get('GIORNI_PROVA', '')
    URL_CONDIZIONI = os.environ.get('URL_CONDIZIONI', '')

    # attivazione con bonifico (finché non c'è Stripe)
    IBAN = os.environ.get('IBAN', '')
    INTESTATARIO_CONTO = os.environ.get('INTESTATARIO_CONTO', '')
    MAIL_AVVISI = os.environ.get('MAIL_AVVISI', 'info@mauriziogustinicchiconsulting.it')
    SMTP_HOST = os.environ.get('SMTP_HOST', '')
    SMTP_PORT = os.environ.get('SMTP_PORT', '465')
    SMTP_USER = os.environ.get('SMTP_USER', '')
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '')
    SMTP_FROM = os.environ.get('SMTP_FROM', '')
    # AI (concierge e commento ai numeri): stessa chiave Gemini degli altri programmi
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '') or os.environ.get('GOOGLE_API_KEY', '')
    GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
