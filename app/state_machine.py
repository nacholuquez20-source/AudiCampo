from app.models import EstadoProceso


ALLOWED_TRANSITIONS: dict[EstadoProceso, set[EstadoProceso]] = {
    EstadoProceso.RECIBIDO: {EstadoProceso.PROCESANDO, EstadoProceso.PENDIENTE_REVISION},
    EstadoProceso.PROCESANDO: {
        EstadoProceso.PENDIENTE_DATOS,
        EstadoProceso.PENDIENTE_CONFIRMACION,
        EstadoProceso.ERROR_AUDIO,
        EstadoProceso.ERROR_IA,
        EstadoProceso.ERROR_VALIDACION,
        EstadoProceso.PENDIENTE_REVISION,
    },
    EstadoProceso.PENDIENTE_DATOS: {EstadoProceso.PENDIENTE_CONFIRMACION, EstadoProceso.PENDIENTE_REVISION},
    EstadoProceso.PENDIENTE_CONFIRMACION: {
        EstadoProceso.PENDIENTE_CONFIRMACION,
        EstadoProceso.CONFIRMADO,
        EstadoProceso.PENDIENTE_DATOS,
        EstadoProceso.PENDIENTE_REVISION,
    },
    EstadoProceso.CONFIRMADO: {EstadoProceso.GUARDADO, EstadoProceso.ERROR_ESCRITURA, EstadoProceso.PENDIENTE_REVISION},
    EstadoProceso.ERROR_AUDIO: {EstadoProceso.PROCESANDO, EstadoProceso.PENDIENTE_REVISION},
    EstadoProceso.ERROR_IA: {EstadoProceso.PROCESANDO, EstadoProceso.PENDIENTE_REVISION},
    EstadoProceso.ERROR_VALIDACION: {EstadoProceso.PENDIENTE_DATOS, EstadoProceso.PENDIENTE_REVISION},
    EstadoProceso.ERROR_ESCRITURA: {EstadoProceso.CONFIRMADO, EstadoProceso.PENDIENTE_REVISION},
    EstadoProceso.PENDIENTE_REVISION: set(),
    EstadoProceso.GUARDADO: set(),
}


def can_transition(current: EstadoProceso, target: EstadoProceso) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def ensure_transition(current: EstadoProceso, target: EstadoProceso) -> None:
    if not can_transition(current, target):
        raise ValueError(f"Transición inválida: {current} -> {target}")
