from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# Estos son los campos que aparecen en "Novedades Diarias", la planilla de papel que
# ya usan en el campo: Finca, Lote, Sección, Apellido y Nombre de quien hizo la
# tarea, Tarea (código y descripción), Cantidad y Contratista. "Variedad" y "Fuente
# Nitrogenada" no están en ese papel - eran específicos de fertilización y se
# le pedían a la persona en toda tarea, aunque no aplicaran. Se sacaron.
BUSINESS_FIELDS = (
    "fecha",
    "finca",
    "lote",
    "seccion",
    "trabajador",
    "codigo_tarea",
    "descripcion_tarea",
    "cantidad",
    "contratista",
    "nombre_capataz",
)

# Al peón nunca se le pide el código de tarea: él dice la tarea como le sale
# ("fumigué", "carpí el lote de arriba"), y eso se guarda tal cual en
# descripcion_tarea. El código se deduce del catálogo si la descripción coincide, y
# si no coincide queda vacío para que lo complete la oficina - pero el reporte se
# registra igual, nunca se pierde lo que dijo la persona.
REQUIRED_FIELDS = tuple(field for field in BUSINESS_FIELDS if field != "codigo_tarea")

# Cómo se nombra cada campo cuando se le habla a la persona: nunca el nombre
# técnico (codigo_tarea), que no significa nada para un capataz.
FIELD_LABELS = {
    "fecha": "la fecha",
    "finca": "la finca",
    "lote": "el lote",
    "seccion": "la sección",
    "trabajador": "el nombre de quién hizo la tarea",
    "codigo_tarea": "la tarea",
    "descripcion_tarea": "la tarea",
    "cantidad": "la cantidad (con la unidad: horas, hectáreas, surcos o viajes)",
    "contratista": "el contratista",
    "nombre_capataz": "tu nombre",
}

# La palabra que se usa para nombrar cada campo al escribir "CORREGIR <esto>: valor".
# No son necesariamente iguales a FIELD_LABELS (que están pensados para leerse en una
# frase, no para escribirse como comando).
FIELD_COMMAND_NAMES = {
    "fecha": "fecha",
    "finca": "finca",
    "lote": "lote",
    "seccion": "sección",
    "trabajador": "trabajador",
    "codigo_tarea": "código tarea",
    "descripcion_tarea": "descripción tarea",
    "cantidad": "cantidad",
    "contratista": "contratista",
    "nombre_capataz": "nombre del capataz",
}

SHEETS_HEADERS = (
    "Fecha",
    "Finca",
    "Lote",
    "Sección",
    "Trabajador",
    "Código Tarea",
    "Descripción Tarea",
    "Cantidad",
    "Contratista",
    "Nombre del capataz",
)


class EstadoProceso(StrEnum):
    RECIBIDO = "RECIBIDO"
    PROCESANDO = "PROCESANDO"
    PENDIENTE_DATOS = "PENDIENTE_DATOS"
    PENDIENTE_CONFIRMACION = "PENDIENTE_CONFIRMACION"
    CONFIRMADO = "CONFIRMADO"
    GUARDADO = "GUARDADO"
    ERROR_AUDIO = "ERROR_AUDIO"
    ERROR_IA = "ERROR_IA"
    ERROR_VALIDACION = "ERROR_VALIDACION"
    ERROR_ESCRITURA = "ERROR_ESCRITURA"
    PENDIENTE_REVISION = "PENDIENTE_REVISION"


class ReporteExtraido(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fecha: Optional[str] = None
    finca: Optional[str] = None
    lote: Optional[str] = None
    seccion: Optional[str] = None
    trabajador: Optional[str] = None
    codigo_tarea: Optional[str] = None
    descripcion_tarea: Optional[str] = None
    cantidad: Optional[str] = None
    contratista: Optional[str] = None
    nombre_capataz: Optional[str] = None


class ReporteValidado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fecha: str
    finca: str
    lote: str
    seccion: str
    trabajador: str
    codigo_tarea: str
    descripcion_tarea: str
    cantidad: str
    contratista: str
    nombre_capataz: str

    def to_sheet_row(self) -> list[str]:
        return [
            self.fecha,
            self.finca,
            self.lote,
            self.seccion,
            self.trabajador,
            self.codigo_tarea,
            self.descripcion_tarea,
            self.cantidad,
            self.contratista,
            self.nombre_capataz,
        ]


class ValidationErrorItem(BaseModel):
    campo: str
    mensaje: str


class CapatazInfo(BaseModel):
    """Lo que ya sabemos de un capataz por su teléfono: no hace falta que lo repita
    en cada reporte. contratista y finca son opcionales porque un capataz puede
    trabajar para más de uno - si no están cargados, se le siguen preguntando."""

    model_config = ConfigDict(extra="forbid")

    nombre: str
    contratista: Optional[str] = None
    finca: Optional[str] = None


class Catalogs(BaseModel):
    capataces_por_telefono: dict[str, CapatazInfo] = Field(default_factory=dict)
    lotes_secciones: set[tuple[str, str]] = Field(default_factory=set)
    tareas: dict[str, str] = Field(default_factory=dict)
    contratistas: set[str] = Field(default_factory=set)


class EstadoTecnico(BaseModel):
    message_id: str
    telefono: str
    estado: EstadoProceso
    intentos: int = 0
    ruta_audio: Optional[str] = None
    reporte_extraido: Optional[ReporteExtraido] = None
    errores_validacion: list[ValidationErrorItem] = Field(default_factory=list)
    fecha_recepcion: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    fecha_actualizacion: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class WhatsAppMessage(BaseModel):
    message_id: str
    telefono: str
    audio_id: Optional[str] = None
    text: Optional[str] = None
