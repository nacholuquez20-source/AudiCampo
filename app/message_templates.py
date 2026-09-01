from app.models import ReporteValidado


def welcome_message() -> str:
    return (
        "Hola, soy el asistente de reportes de campo.\n\n"
        "Para registrar un reporte, mandame un audio de voz contando: fecha, lote, "
        "sección, tarea, cantidad, variedad, fuente nitrogenada, contratista y tu nombre.\n\n"
        "Te voy a mandar un resumen para que confirmes con CONFIRMAR, o corrijas un dato "
        "con CORREGIR campo: valor."
    )


def pending_reminder_message() -> str:
    return (
        "Tenés un reporte pendiente de confirmar.\n\n"
        "Respondé CONFIRMAR para guardarlo, o CORREGIR campo: valor para corregir un dato."
    )


def correction_format_hint() -> str:
    return "Para corregir un dato, respondé con el formato: CORREGIR campo: valor (por ejemplo: CORREGIR lote: 21)."


def missing_field_message(campo: str) -> str:
    return f"No pude guardar el reporte porque falta: {campo}. Respondé sólo con ese dato."


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
        "Respondé CONFIRMAR para guardar.\n"
        "Para corregir, respondé: CORREGIR campo: valor"
    )


def saved_message() -> str:
    return "Reporte guardado correctamente."


def retry_exhausted_message() -> str:
    return "No pude procesar el audio correctamente. El caso quedó pendiente de revisión."
