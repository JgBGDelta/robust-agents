"""Tests de `style.py`: paleta de colores, guardado, anotacion de datos faltantes."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt

from analysis.diagrams_module import style


def test_get_color_exact_configuration_id_match():
    """Comprueba que get color exact configuration id match."""
    assert style.get_color("default") == style.COLOR_MAP["default"]


def test_get_color_falls_back_to_agent_id_prefix():
    """Comprueba que get color falls back to agent id prefix."""
    color = style.get_color("robust_balanced__gemini-2.5-flash")
    assert color == style.COLOR_MAP["robust_balanced"]


def test_get_color_unknown_configuration_is_deterministic():
    """Comprueba que get color unknown configuration is deterministic."""
    color_a = style.get_color("nuevo_agente__modelo-x")
    color_b = style.get_color("nuevo_agente__modelo-x")
    assert color_a == color_b
    assert color_a not in style.COLOR_MAP.values() or True  # solo exige determinismo


def test_get_color_different_unknown_configs_can_differ():
    """Comprueba que get color different unknown configs can differ."""
    colors = {style.get_color(f"agente_{i}") for i in range(8)}
    # No exigimos unicidad total (hash modulo tamano de paleta), pero si variedad razonable.
    assert len(colors) >= 2


def test_save_figure_creates_file_and_closes(tmp_path: Path):
    """Comprueba que save figure creates file and closes."""
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    output_path = tmp_path / "sub" / "figure.png"

    result = style.save_figure(fig, output_path)

    assert result == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0
    assert fig.number not in plt.get_fignums()


def test_sample_size_labels_format():
    """Comprueba que sample size labels format."""
    labels = style.sample_size_labels(["a", "b"], [10, 5])
    assert labels == ["a\n(n=10)", "b\n(n=5)"]


def test_format_agent_label_shortens_externals():
    """Los agentes externos usan nombres cortos sin modelo ni fecha."""
    assert style.format_agent_label("agentless_v1.5") == "agentless"
    assert style.format_agent_label("moatless_claude35_20241117") == "moatless"
    assert style.format_agent_label("robust_balanced__gemini-2.5-flash") == "robust_balanced"
    assert style.format_agent_label("default") == "default (mini-swe-agent)"


def test_plot_rate_bar_shows_zero_percent_label(tmp_path: Path):
    """Un 0% real debe mostrarse como etiqueta numerica, no confundirse con 'sin datos'."""
    fig, ax = plt.subplots()
    style.plot_rate_bar(ax, 0, 0.0, "#1f77b4")
    style.configure_rate_axis(ax)
    output = tmp_path / "zero_rate.png"
    style.save_figure(fig, output)
    assert output.exists()
    texts = [t.get_text() for t in ax.texts]
    assert "0.0%" in texts
