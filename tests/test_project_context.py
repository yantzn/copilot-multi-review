from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from ai_review.project_context import collect_project_context


def test_collect_project_context_reads_requirements_rules_and_excel(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements"
    requirements.mkdir()
    (requirements / "requirements.md").write_text(
        "# 要件\n重複メールは409を返す。\n",
        encoding="utf-8",
    )

    rules = tmp_path / "project-rules"
    rules.mkdir()
    (rules / "rules.md").write_text(
        "# ルール\nAPI層はRepositoryを直接参照しない。\n",
        encoding="utf-8",
    )

    design = tmp_path / "design"
    design.mkdir()
    workbook_path = design / "system-design.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "API仕様"
    sheet.append(["API-ID", "Condition", "HTTP Status"])
    sheet.append(["API-001", "duplicate_email", 409])
    workbook.save(workbook_path)

    context = collect_project_context(tmp_path)

    assert context["requirements"][0]["source_file"] == "requirements/requirements.md"
    assert "409" in context["requirements"][0]["content"]

    assert context["project_rules"][0]["source_file"] == "project-rules/rules.md"
    assert "Repository" in context["project_rules"][0]["content"]

    design_context = context["design_context"]
    api_sheet = next(item for item in design_context if item.get("sheet") == "API仕様")
    assert api_sheet["source_file"] == "design/system-design.xlsx"
    assert api_sheet["provenance"]["sheet"] == "API仕様"
    assert any(cell["cell"] == "A2" and cell["value"] == "API-001" for cell in api_sheet["cells"])
    assert any(cell["cell"] == "C2" and cell["value"] == 409 for cell in api_sheet["cells"])


def test_collect_project_context_reports_unsupported_binary_design_formats(tmp_path: Path) -> None:
    design = tmp_path / "design"
    design.mkdir()
    (design / "legacy.xls").write_bytes(b"not-a-real-xls")
    (design / "screen.pdf").write_bytes(b"%PDF-placeholder")

    context = collect_project_context(tmp_path)

    warnings = "\n".join(context["warnings"])
    assert "legacy.xls" in warnings
    assert "screen.pdf" in warnings
    assert "構造化解析には対応していません" in warnings
