"""Funzioni comuni ai blueprint: controllo CSRF, lettura delle date, tutor del corso."""
from datetime import date

from flask import flash

from models import db


def csrf():
    from blueprints.accesso import _csrf_ok
    if not _csrf_ok():
        flash('Sessione scaduta: riprova', 'err')
        return False
    return True


def data(v, default=None):
    try:
        return date.fromisoformat(v) if v else default
    except ValueError:
        return default


def tutor():
    """Dopo ogni operazione il tutor del corso controlla se il passo è fatto."""
    try:
        from blueprints.corso import controlla_in_silenzio
        controlla_in_silenzio()
    except Exception:   # noqa: BLE001 — il tutor non deve mai bloccare il lavoro
        db.session.rollback()
