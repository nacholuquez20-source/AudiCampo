from app.models import FIELD_LABELS, ReporteValidado


def audio_received_message() -> str:
    return "Recibí tu audio, dame un segundo que lo estoy escuchando..."


def audio_download_failed_message() -> str:
    return "Tuve un problema técnico para descargar tu audio. Probá mandarlo de nuevo en unos minutos."


def catalogs_unavailable_message() -> str:
    return "Tuve un problema técnico para validar tu reporte. Probá de nuevo en unos minutos."


def ai_unavailable_message() -> str:
    return (
        "Tuve un problema técnico para escuchar tu audio (el servicio de IA no está "
        "respondiendo). No hace falta que lo grabes de nuevo ahora: probá en unos minutos."
    )


def correction_understanding_failed_message() -> str:
    return "No entendí el dato corregido en ese audio. Mandame de nuevo un audio diciendo solo el dato correcto."


def unsupported_message_type_message() -> str:
    return "Por ahora solo entiendo audios y mensajes de texto. Mandame un audio contando tu reporte."


def welcome_message() -> str:
    return (
        "Hola, soy el asistente de reportes de campo.\n\n"
        "Para registrar un reporte, mandame un audio de voz contando: fecha, lote, "
        "sección, tarea, cantidad, variedad, fuente nitrogenada, contratista y tu nombre.\n\n"
        "Te voy a mandar un resumen con dos botones para confirmar o corregir."
    )


def pending_reminder_message() -> str:
    return (
        "Tenés un reporte pendiente de confirmar.\n\n"
        "Respondé sí para guardarlo, o mandame un audio nuevo diciendo el dato que hay que corregir."
    )


def correction_format_hint() -> str:
    return "Para corregir un dato, respondé con el formato: CORREGIR campo: valor (por ejemplo: CORREGIR lote: 21)."


def missing_field_message(campo: str) -> str:
    etiqueta = FIELD_LABELS.get(campo, campo)
    return f"Para guardar el reporte me falta {etiqueta}. Mandame un audio diciendo ese dato."


def invalid_unit_message() -> str:
    return "La unidad informada no es válida. Las unidades permitidas son: horas, hectáreas, surcos o viajes."


def confirmation_summary(reporte: ReporteValidado) -> str:
    return (
        "Reporte interpretado\n"
        f"Fecha: {reporte.fecha} · Lote: {reporte.lote} · Sección: {reporte.seccion} · "
        f"Código Tarea: {reporte.codigo_tarea} · Descripción Tarea: {reporte.descripcion_tarea} · "
        f"Cantidad: {reporte.cantidad} · Variedad: {reporte.variedad} · "
        f"Fuente Nitrogenada: {reporte.fuente_nitrogenada} · Contratista: {reporte.contratista} · "
        f"Nombre del capataz: {reporte.nombre_capataz}\n\n"
        "¿Está todo bien?"
    )


CONFIRMATION_BUTTONS = [("confirmar", "✅ Confirmar"), ("corregir", "✏️ Corregir")]


def save_failed_message() -> str:
    return "Tuve un problema técnico para guardar tu reporte. Probá de nuevo en unos minutos, respondiendo sí."


def saved_message() -> str:
    return "Reporte guardado correctamente."


def retry_exhausted_message() -> str:
    return "No pude procesar el audio correctamente. El caso quedó pendiente de revisión."
