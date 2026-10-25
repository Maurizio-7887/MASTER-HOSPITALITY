"""MASTER HOSPITALITY — gestionale per hotel e B&B: prenotazioni e planner, front office, housekeeping,
DynaPrice (revenue management con previsione e prezzo dinamico a margine garantito) e controllo di gestione."""
from datetime import date, timedelta

from flask import Flask, jsonify, redirect, url_for

from config import Config
from models import db

TABELLE_PER_SPAZIO = ['tipi_camera', 'camere', 'piani_tariffari', 'stagioni', 'restrizioni', 'tariffe_giorno', 'canali', 'eventi',
                      'costi_fissi', 'ospiti', 'prenotazioni', 'notti_vendute', 'addebiti', 'pagamenti', 'alloggiati']


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    app.config.setdefault('PERMANENT_SESSION_LIFETIME', timedelta(hours=12))
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_HTTPONLY'] = True

    db.init_app(app)

    import abbonamento
    import accesso
    import corso
    import piattaforma
    from blueprints import front, report, revenue, struttura
    for bp in (accesso.bp, piattaforma.bp, corso.bp, abbonamento.bp, front.bp, struttura.bp, revenue.bp, report.bp):
        app.register_blueprint(bp)
    accesso.proteggi(app)

    import spazi  # noqa: F401  (filtro automatico per spazio di lavoro)
    with app.app_context():
        db.create_all()
        _spazio_principale()
        accesso.crea_admin_da_ambiente(app)
        if app.config.get('DEMO_DATA') and not app.config.get('TESTING'):
            from seed_demo import carica_demo
            carica_demo()

    @app.route('/')
    def index():
        from flask import g
        if g.get('utente') is not None and g.utente.ruolo == 'piani':
            return redirect(url_for('front.housekeeping'))
        return redirect(url_for('front.cruscotto'))

    @app.route('/salute')
    def salute():
        return jsonify({'ok': True})

    @app.template_filter('euro')
    def euro(v, dec=0):
        if v is None:
            return '—'
        s = f'{v:,.{dec}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
        return f'{s} €'

    @app.template_filter('pct')
    def pct(v, dec=0):
        return '—' if v is None else f'{v * 100:.{dec}f}%'.replace('.', ',')

    @app.template_filter('dt')
    def dt(v):
        return v.strftime('%d/%m/%Y') if v else ''

    @app.context_processor
    def contesto():
        from flask import g
        nome, corso_attivo = '', False
        if g.get('utente') is not None:
            from corso import attivo
            from hotel import par
            nome, corso_attivo = par('nome_struttura'), attivo()
        return {'oggi': date.today().isoformat(), 'nome_struttura': nome, 'corso_attivo': corso_attivo}

    return app


def _spazio_principale():
    from models import Spazio
    if not db.session.get(Spazio, 1):
        db.session.add(Spazio(id=1, nome='Principale', tipo='azienda'))
        db.session.commit()
        if db.engine.dialect.name == 'postgresql':
            from sqlalchemy import text
            with db.engine.begin() as conn:
                conn.execute(text("SELECT setval(pg_get_serial_sequence('spazi','id'), GREATEST((SELECT MAX(id) FROM spazi), 1))"))


app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
