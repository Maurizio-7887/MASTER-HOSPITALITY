"""Spazi di lavoro: ogni azienda cliente (o allievo di un corso) ha il suo spazio e vede solo i propri dati.

Tutte le tabelle operative hanno la colonna spazio_id. Un filtro automatico aggiunge "spazio_id = spazio corrente"
a ogni lettura, modifica o cancellazione fatta con l'ORM, e i nuovi record ricevono lo spazio corrente. Lo spazio
corrente è quello dell'utente collegato (il gestore della piattaforma può entrare negli altri spazi).
Fuori da una richiesta web (avvio, script) si usa lo spazio 1 o quello impostato con `nello_spazio(id)`.
"""
import contextvars
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from models import PerSpazio, db

_forzato = contextvars.ContextVar('spazio_forzato', default=None)
SPAZIO_PRINCIPALE = 1


def spazio_corrente():
    f = _forzato.get()
    if f is not None:
        return f
    try:
        from flask import g, has_request_context
        if has_request_context() and g.get('spazio_id'):
            return g.spazio_id
    except RuntimeError:
        pass
    return SPAZIO_PRINCIPALE


@contextmanager
def nello_spazio(sid):
    tok = _forzato.set(sid)
    try:
        yield
    finally:
        _forzato.reset(tok)


@event.listens_for(Session, 'do_orm_execute')
def _filtra(stato):
    if stato.execution_options.get('tutti_gli_spazi'):
        return
    if not (stato.is_select or stato.is_update or stato.is_delete):
        return
    sid = spazio_corrente()
    stato.statement = stato.statement.options(
        with_loader_criteria(PerSpazio, lambda cls: cls.spazio_id == sid, include_aliases=True))


@event.listens_for(Session, 'before_flush')
def _assegna(session, contesto, istanze):
    sid = None
    for obj in session.new:
        if isinstance(obj, PerSpazio) and getattr(obj, 'spazio_id', None) is None:
            sid = sid or spazio_corrente()
            obj.spazio_id = sid


def chiave(nome):
    """Chiave di Impostazione propria dello spazio corrente."""
    return f'{spazio_corrente()}:{nome}'
