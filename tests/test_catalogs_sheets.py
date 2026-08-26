"""Tests for pure catalog parsing functions (no gspread dependency)."""

import pytest

from app.catalogs import (
    _parse_capataces,
    _parse_lotes_secciones,
    _parse_nombre_set,
    _parse_tareas,
)


def test_parse_capataces_simple():
    """Test parsing capataces from rows."""
    rows = [
        {"telefono": "5491111111111", "nombre": "Juan Pérez"},
        {"telefono": "5492222222222", "nombre": "María García"},
    ]
    result = _parse_capataces(rows)

    assert result == {"5491111111111": "Juan Pérez", "5492222222222": "María García"}


def test_parse_capataces_with_empty_values():
    """Test that empty values are skipped."""
    rows = [
        {"telefono": "5491111111111", "nombre": "Juan Pérez"},
        {"telefono": "", "nombre": "Invalid"},
        {"telefono": "5492222222222", "nombre": ""},
    ]
    result = _parse_capataces(rows)

    assert result == {"5491111111111": "Juan Pérez"}


def test_parse_capataces_with_whitespace():
    """Test that whitespace is stripped."""
    rows = [
        {"telefono": "  5491111111111  ", "nombre": "  Juan Pérez  "},
    ]
    result = _parse_capataces(rows)

    assert result == {"5491111111111": "Juan Pérez"}


def test_parse_lotes_secciones_simple():
    """Test parsing lotes y secciones."""
    rows = [
        {"lote": "20", "seccion": "3"},
        {"lote": "21", "seccion": "1"},
    ]
    result = _parse_lotes_secciones(rows)

    assert result == {("20", "3"), ("21", "1")}


def test_parse_lotes_secciones_with_empty_values():
    """Test that empty values are skipped."""
    rows = [
        {"lote": "20", "seccion": "3"},
        {"lote": "", "seccion": "3"},
        {"lote": "21", "seccion": ""},
    ]
    result = _parse_lotes_secciones(rows)

    assert result == {("20", "3")}


def test_parse_tareas_simple():
    """Test parsing tareas."""
    rows = [
        {"codigo": "145", "descripcion": "Fertilización"},
        {"codigo": "201", "descripcion": "Pulverización"},
    ]
    result = _parse_tareas(rows)

    assert result == {"145": "Fertilización", "201": "Pulverización"}


def test_parse_tareas_with_empty_values():
    """Test that empty values are skipped."""
    rows = [
        {"codigo": "145", "descripcion": "Fertilización"},
        {"codigo": "", "descripcion": "Invalid"},
        {"codigo": "201", "descripcion": ""},
    ]
    result = _parse_tareas(rows)

    assert result == {"145": "Fertilización"}


def test_parse_nombre_set_simple():
    """Test parsing a simple set of names."""
    rows = [
        {"nombre": "ACA 603"},
        {"nombre": "DM 46R18"},
    ]
    result = _parse_nombre_set(rows, "nombre")

    assert result == {"ACA 603", "DM 46R18"}


def test_parse_nombre_set_with_empty_values():
    """Test that empty values are skipped."""
    rows = [
        {"nombre": "ACA 603"},
        {"nombre": ""},
        {"nombre": "DM 46R18"},
    ]
    result = _parse_nombre_set(rows, "nombre")

    assert result == {"ACA 603", "DM 46R18"}


def test_parse_nombre_set_custom_key():
    """Test parsing with a custom key."""
    rows = [
        {"fuente": "Urea"},
        {"fuente": "UAN"},
    ]
    result = _parse_nombre_set(rows, "fuente")

    assert result == {"Urea", "UAN"}


def test_parse_nombre_set_missing_key():
    """Test that missing keys are handled gracefully."""
    rows = [
        {"nombre": "Value1"},
        {"another_key": "Value2"},
    ]
    result = _parse_nombre_set(rows, "nombre")

    assert result == {"Value1"}
