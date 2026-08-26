import pytest

from app.models import EstadoProceso
from app.state_machine import can_transition, ensure_transition


def test_allows_normal_confirmation_flow():
    assert can_transition(EstadoProceso.RECIBIDO, EstadoProceso.PROCESANDO)
    assert can_transition(EstadoProceso.PROCESANDO, EstadoProceso.PENDIENTE_CONFIRMACION)
    assert can_transition(EstadoProceso.PENDIENTE_CONFIRMACION, EstadoProceso.CONFIRMADO)
    assert can_transition(EstadoProceso.CONFIRMADO, EstadoProceso.GUARDADO)


def test_rejects_save_without_confirmation():
    assert not can_transition(EstadoProceso.PENDIENTE_CONFIRMACION, EstadoProceso.GUARDADO)
    with pytest.raises(ValueError):
        ensure_transition(EstadoProceso.PENDIENTE_CONFIRMACION, EstadoProceso.GUARDADO)
