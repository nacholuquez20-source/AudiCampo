from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


BUSINESS_FIELDS = (
    "fecha",
    "lote",
    "seccion",
    "codigo_tarea",
    "descripcion_tarea",
    "cantidad",
    "variedad",
    "fuente_nitrogenada",
    "contratista",
    "nombre_capataz",
)

# Cómo se nombra cada campo cuando se le habla a la persona: nunca el nombre
# técnico (codigo_tarea), que no significa nada para un capataz.
FIELD_LABELS = {
    "fecha": "la fecha",
    "lote": "el lote",
    "seccion": "la sección",
    "codigo_tarea": "la tarea",
    "descripcion_tarea": "la tarea",
    "cantidad": "la cantidad (con la unidad: horas, hectáreas, surcos o viajes)",
    "variedad": "la variedad",
    "fuente_nitrogenada": "la fuente nitrogenada",
    "contratista": "el contratista",
    "nombre_capataz": "tu nombre",
}

SHEETS_HEADERS = (
    "Fecha",
    "Lote",
    "Sección",
    "Código Tarea",
    "Descripción Tarea",
    "Cantidad",
    "Variedad",
    "Fuente Nitrogenada",
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
    lote: Optional[str] = None
    seccion: Optional[str] = None
    codigo_tarea: Optional[str] = None
    descripcion_tarea: Optional[str] = None
    cantidad: Optional[str] = None
    variedad: Optional[str] = None
    fuente_nitrogenada: Optional[str] = None
    contratista: Optional[str] = None
    nombre_capataz: Optional[str] = None


class ReporteValidado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fecha: str
    lote: str
    seccion: str
    codigo_tarea: str
    descripcion_tarea: str
    cantidad: str
    variedad: str
    fuente_nitrogenada: str
    contratista: str
    nombre_capataz: str

    def to_sheet_row(self) -> list[str]:
        return [
            self.fecha,
            self.lote,
            self.seccion,
            self.codigo_tarea,
            self.descripcion_tarea,
            self.cantidad,
            self.variedad,
            self.fuente_nitrogenada,
            self.contratista,
            self.nombre_capataz,
        ]


class ValidationErrorItem(BaseModel):
    campo: str
    mensaje: str


class Catalogs(BaseModel):
    capataces_por_telefono: dict[str, str] = Field(default_factory=dict)
    lotes_secciones: set[tuple[str, str]] = Field(default_factory=set)
    tareas: dict[str, str] = Field(default_factory=dict)
    variedades: set[str] = Field(default_factory=set)
    fuentes_nitrogenadas: set[str] = Field(default_factory=set)
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
