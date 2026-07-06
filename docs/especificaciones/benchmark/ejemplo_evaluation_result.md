# Ejemplo de `evaluation_result.json` (contrato objetivo)

> Un solo archivo por run. Sustituye `functional_result.json` + `extended_result.json`. Misma información; el Bloque 3 lee `functional.resolved` y `extended.run_metrics` / `extended.patch_metrics`.

## DefaultAgent (baseline)

```json
{
  "run_id": "r-a1b2c3d4",
  "instance_id": "django__django-001",
  "agent_id": "default",
  "trace_format": null,
  "functional": {
    "resolved": true,
    "status": "evaluated",
    "evaluation_backend": "sb_cli",
    "report_path": "groups/default__gemini-3-flash/functional_report/...",
    "evidence": {}
  },
  "extended": {
    "run_metrics": {
      "trajectory_format": "mini-swe-agent-1.1",
      "exit_status": "Submitted",
      "termination": "model_submitted",
      "steps_used": 12,
      "cost_total": null,
      "wallclock_seconds": 145.3
    },
    "patch_metrics": {
      "lines_added": 8,
      "lines_deleted": 2,
      "hunks_count": 1,
      "files_modified": ["django/core/handlers/base.py"],
      "files_intersection_with_gold": ["django/core/handlers/base.py"],
      "files_unexpected": [],
      "files_missing_vs_gold": [],
      "jaccard_files": 1.0,
      "jaccard_lines": 0.35
    },
    "availability": {
      "run_metrics": {"status": "available", "cause": null},
      "patch_metrics": {"status": "available", "cause": null}
    },
    "errors": []
  }
}
```

## RobustAgent (`agent_id: robust_balanced`)

El perfil de repositorio va en la config del agente (`config_overrides`), no como segmento de ruta. `trace_format` indica bloque `robust_agent` en la traza.

```json
{
  "run_id": "r-e5f6g7h8",
  "instance_id": "django__django-001",
  "agent_id": "robust_balanced",
  "trace_format": "robust-agent-1.0",
  "functional": {
    "resolved": true,
    "status": "evaluated",
    "evaluation_backend": "sb_cli",
    "report_path": null,
    "evidence": {}
  },
  "extended": {
    "run_metrics": {
      "trajectory_format": "mini-swe-agent-1.1",
      "exit_status": "Submitted",
      "termination": "controller_finalize",
      "steps_used": 15,
      "cost_total": 0.51,
      "wallclock_seconds": 198.7
    },
    "patch_metrics": {
      "lines_added": 5,
      "lines_deleted": 1,
      "hunks_count": 1,
      "files_modified": ["django/core/handlers/base.py"],
      "files_intersection_with_gold": ["django/core/handlers/base.py"],
      "files_unexpected": [],
      "files_missing_vs_gold": [],
      "jaccard_files": 1.0,
      "jaccard_lines": 0.41
    },
    "availability": {
      "run_metrics": {"status": "available", "cause": null},
      "patch_metrics": {"status": "available", "cause": null}
    },
    "errors": []
  }
}
```

## Run fallido (sin parche)

```json
{
  "run_id": "r-i9j0k1l2",
  "instance_id": "django__django-002",
  "agent_id": "default",
  "trace_format": null,
  "functional": {
    "resolved": null,
    "status": "skipped",
    "evaluation_backend": "none",
    "report_path": null,
    "evidence": {"cause": "empty_model_patch"}
  },
  "extended": {
    "run_metrics": {
      "trajectory_format": "mini-swe-agent-1.1",
      "exit_status": "LimitsExceeded",
      "termination": "limits_exceeded",
      "steps_used": 50,
      "cost_total": 1.2,
      "wallclock_seconds": 600.0
    },
    "patch_metrics": {
      "lines_added": 0,
      "lines_deleted": 0,
      "hunks_count": 0,
      "files_modified": [],
      "files_intersection_with_gold": null,
      "files_unexpected": null,
      "files_missing_vs_gold": null,
      "jaccard_files": null,
      "jaccard_lines": null
    },
    "availability": {
      "run_metrics": {"status": "available", "cause": null},
      "patch_metrics": {"status": "not_applicable", "cause": "empty_model_patch"}
    },
    "errors": []
  }
}
```

## Artefactos relacionados

| Archivo | Contenido |
|---------|-----------|
| `run_record.json` | `status`, `contamination_detected`, paths, duración |
| `trajectory.traj.json` | Traza completa; bloque `robust_agent` en agentes robustos |
