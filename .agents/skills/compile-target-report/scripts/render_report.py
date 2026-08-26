#!/usr/bin/env python3
"""Render a target report as two analytical pages plus a source appendix."""

from __future__ import annotations

import argparse
import html
import json
import math
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Flowable, Paragraph, Table, TableStyle

from validate_report import validate_data


PAGE_W, PAGE_H = landscape(letter)
MARGIN = 24
CONTENT_W = PAGE_W - 2 * MARGIN
FOOTER_Y = 13
EVIDENCE_ORDER = {"Genetic": 0, "Human PD": 1, "Animal": 2, "Cell": 3, "Mechanistic": 4}
EVIDENCE_STRENGTH = {0: "Gap", 1: "Limited", 2: "Moderate", 3: "Strong"}
MODEL_AVAILABILITY = {0: "Build", 1: "Published only", 2: "Recoverable", 3: "Stocked"}
SETUP_DIFFICULTY = {0: "Routine", 1: "Qualify", 2: "Specialist", 3: "Develop"}
POSITIVE_MODULATIONS = {"agonism", "activation", "gain of function"}
NEGATIVE_MODULATIONS = {"antagonism", "inhibition", "loss of function"}


def _hex(value: str) -> colors.Color:
    return colors.HexColor(value)


def _load_theme() -> dict[str, colors.Color]:
    path = Path(__file__).resolve().parents[1] / "assets" / "report-theme.json"
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    return {key: _hex(value) for key, value in raw.items()}


THEME = _load_theme()


def ascii_text(value: object) -> str:
    text = str(value)
    replacements = {
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
        "\u2212": "-", "\u2192": "->", "\u2191": "up", "\u2193": "down",
        "\u03b3": "gamma", "\u03b1": "alpha", "\u03b2": "beta", "\u03bc": "u",
        "\u00d7": "x", "\u2265": ">=", "\u2264": "<=", "\u2022": ";",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def refs(row: dict) -> str:
    return "[" + ",".join(row.get("sources", [])) + "]"


def joined(items: list[str]) -> str:
    return "; ".join(ascii_text(item) for item in items)


def para_style(size: float = 6.4, leading: float | None = None, *, bold: bool = False, color=None, align=TA_LEFT) -> ParagraphStyle:
    return ParagraphStyle(
        name=f"p-{size}-{bold}-{align}",
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size,
        leading=leading or size * 1.2,
        textColor=color or THEME["ink"],
        alignment=align,
        allowWidows=0,
        allowOrphans=0,
        splitLongWords=True,
        spaceAfter=0,
        spaceBefore=0,
    )


def p(value: object, size: float = 6.4, *, bold: bool = False, color=None, align=TA_LEFT) -> Paragraph:
    safe = html.escape(ascii_text(value)).replace("\n", "<br/>")
    return Paragraph(safe, para_style(size, bold=bold, color=color, align=align))


def rich_p(markup: str, size: float = 6.2, *, color=None) -> Paragraph:
    return Paragraph(markup, para_style(size, color=color))


def bullet_p(items: list[str], source_text: str, size: float = 5.9) -> Paragraph:
    lines = []
    for index, item in enumerate(items):
        suffix = f" {html.escape(source_text)}" if index == len(items) - 1 else ""
        lines.append(f"&bull;&nbsp;{html.escape(ascii_text(item))}{suffix}")
    return rich_p("<br/>".join(lines), size)


def modality_case_cell(row: dict) -> Paragraph:
    lines = []
    for claim in row["first_principles"]:
        basis = html.escape(ascii_text(claim["basis"]))
        statement = html.escape(ascii_text(claim["claim"]))
        lines.append(f"<b>{basis}:</b> {statement} {html.escape(refs(claim))}")
    return rich_p("<br/>".join(lines), 5.15)


def modality_decision_cell(row: dict) -> Paragraph:
    patent = row["patent_differentiation"]
    markup = (
        f"<b>Boundary:</b> {html.escape(ascii_text(row['evidence_boundary']))}<br/>"
        f"<b>Key risk:</b> {html.escape(ascii_text(row['key_risk']))}<br/>"
        f"<b>Reject with:</b> {html.escape(ascii_text(row['decisive_experiment']))} {html.escape(refs(row))}<br/>"
        f"<b>Patent burden - {html.escape(ascii_text(patent['burden']))}:</b> "
        f"{html.escape(ascii_text(patent['claim_landscape']))}<br/>"
        f"<b>Differentiate:</b> {html.escape(ascii_text(patent['differentiation_needed']))} "
        f"{html.escape(refs(patent))}"
    )
    return rich_p(markup, 4.8)


def assay_call_cell(row: dict) -> Paragraph:
    markup = (
        f"<b>{html.escape(ascii_text(row['assay']))}</b><br/>"
        f"<b>Measures:</b> {html.escape(ascii_text(row['measured']))}<br/>"
        f'<font color="{_html_color(THEME["teal"])}"><b>POS:</b></font> '
        f"{html.escape(ascii_text(row['positive_readout']))}<br/>"
        f'<font color="{_html_color(THEME["red"])}"><b>NEG:</b></font> '
        f"{html.escape(ascii_text(row['negative_readout']))}"
    )
    return rich_p(markup, 5.15)


def model_availability_cell(row: dict) -> Paragraph:
    markup = (
        f"{html.escape(ascii_text(row['model']))}<br/>"
        f"<b>{MODEL_AVAILABILITY[row['model_availability_score']]}:</b> "
        f"{html.escape(ascii_text(row['model_availability']))}"
    )
    if "species_conservation" in row:
        conservation = row["species_conservation"]
        markup += (
            f"<br/><b>Receptor:</b> {html.escape(ascii_text(conservation['receptor']))}"
            f"<br/><b>Pathway:</b> {html.escape(ascii_text(conservation['pathway']))}"
            f"<br/><b>Translate:</b> {html.escape(ascii_text(conservation['translation']))} "
            f"{html.escape(refs(conservation))}"
        )
        return rich_p(markup, 4.65)
    return rich_p(markup, 5.15)


def setup_precedent_cell(row: dict) -> Paragraph:
    markup = (
        f"<b>{SETUP_DIFFICULTY[row['setup_difficulty_score']]} setup:</b> {html.escape(ascii_text(row['setup']))}<br/>"
        f"<b>Phase 2:</b> {html.escape(ascii_text(row['phase2_precedent']))} "
        f"{html.escape(refs(row))}"
    )
    return rich_p(markup, 5.1)


def modulation_bucket(modulation: str) -> str:
    normalized = modulation.strip().lower()
    if normalized in POSITIVE_MODULATIONS:
        return "positive"
    if normalized in NEGATIVE_MODULATIONS:
        return "negative"
    return "mixed"


def effect_arrow_bucket(effect_direction: str) -> str:
    normalized = effect_direction.strip().lower()
    if normalized in {"increase", "decrease", "mixed"}:
        return normalized
    return "dash"


def _html_color(color: colors.Color) -> str:
    return f"#{round(color.red * 255):02X}{round(color.green * 255):02X}{round(color.blue * 255):02X}"


class PhenotypeCell(Flowable):
    """Compact effect symbol plus wrapped phenotype name."""

    def __init__(self, row: dict):
        super().__init__()
        self.arrow_bucket = effect_arrow_bucket(row["effect_direction"])
        self.effect_color = THEME["navy"]
        self.paragraph = p(row["phenotype"], 5.9)
        self._paragraph_height = 0.0

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = avail_width
        _, self._paragraph_height = self.paragraph.wrap(max(avail_width - 12, 1), avail_height)
        self.height = max(self._paragraph_height + 2, 14)
        return self.width, self.height

    def _triangle(self, points: list[tuple[float, float]]) -> None:
        path = self.canv.beginPath()
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        path.close()
        self.canv.drawPath(path, fill=1, stroke=0)

    def draw(self) -> None:
        # Arrow shape communicates phenotype direction. Its neutral color keeps
        # perturbation class confined to the separate modulation/result column.
        self.canv.setStrokeColor(self.effect_color)
        self.canv.setFillColor(self.effect_color)
        self.canv.setLineWidth(1.5)
        center_y = self.height / 2
        if self.arrow_bucket in {"increase", "decrease", "mixed"}:
            self.canv.line(5, center_y - 4, 5, center_y + 4)
        if self.arrow_bucket in {"increase", "mixed"}:
            self._triangle([(2, center_y + 2), (5, center_y + 7), (8, center_y + 2)])
        if self.arrow_bucket in {"decrease", "mixed"}:
            self._triangle([(2, center_y - 2), (5, center_y - 7), (8, center_y - 2)])
        if self.arrow_bucket == "dash":
            self.canv.setLineWidth(2)
            self.canv.line(1.5, center_y, 8.5, center_y)
        self.paragraph.drawOn(self.canv, 12, (self.height - self._paragraph_height) / 2)


def phenotype_cell(row: dict) -> PhenotypeCell:
    return PhenotypeCell(row)


def direction_cell(row: dict) -> Paragraph:
    bucket = modulation_bucket(row["modulation"])
    color = {
        "positive": THEME["teal"],
        "negative": THEME["red"],
        "mixed": colors.HexColor("#A66B00"),
    }[bucket]
    markup = (
        f'<font color="{_html_color(color)}"><b>{html.escape(ascii_text(row["modulation"]).upper())}</b></font>'
        f' - {html.escape(ascii_text(row["effect"]))}'
    )
    return rich_p(markup, 5.7)


def draw_section_title(canvas: Canvas, title: str, y: float, key: str | None = None) -> float:
    canvas.setFillColor(THEME["navy"])
    canvas.roundRect(MARGIN, y - 15, CONTENT_W, 15, 3, fill=1, stroke=0)
    canvas.setFillColor(THEME["white"])
    canvas.setFont("Helvetica-Bold", 8.5)
    canvas.drawString(MARGIN + 7, y - 11, ascii_text(title))
    if key:
        canvas.setFont("Helvetica", 5.3)
        canvas.drawRightString(PAGE_W - MARGIN - 7, y - 10.5, ascii_text(key))
    return y - 18


def draw_table(
    canvas: Canvas,
    y: float,
    headers: list[str],
    rows: list[list[object]],
    widths: list[float],
    *,
    font_size: float = 6.0,
    max_height: float | None = None,
    padding_y: float = 2.4,
) -> float:
    data = [[p(head, font_size, bold=True, color=THEME["white"], align=TA_CENTER) for head in headers]]
    for row in rows:
        data.append([cell if isinstance(cell, Flowable) else p(cell, font_size) for cell in row])
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), THEME["blue"]),
                ("TEXTCOLOR", (0, 0), (-1, 0), THEME["white"]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), padding_y),
                ("BOTTOMPADDING", (0, 0), (-1, -1), padding_y),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D3DA")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [THEME["white"], THEME["light"]]),
            ]
        )
    )
    width, height = table.wrap(CONTENT_W, PAGE_H)
    if max_height and height > max_height:
        raise ValueError(f"Table exceeds allocated height ({height:.1f} > {max_height:.1f}); shorten report JSON")
    table.drawOn(canvas, MARGIN, y - height)
    return y - height - 5


def draw_header(canvas: Canvas, report: dict) -> float:
    target = report["target"]
    assessment = report["assessment"]
    canvas.setFillColor(THEME["navy"])
    canvas.rect(0, PAGE_H - 60, PAGE_W, 60, fill=1, stroke=0)
    canvas.setFillColor(THEME["white"])
    canvas.setFont("Helvetica-Bold", 17)
    canvas.drawString(MARGIN, PAGE_H - 27, f"{ascii_text(target['symbol'])} / {ascii_text(target['name'])}")
    canvas.setFont("Helvetica", 9)
    canvas.drawString(MARGIN, PAGE_H - 44, f"Target quality assessment - {ascii_text(target['indication'])}")
    canvas.setFont("Helvetica", 7)
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 26, f"Evidence cutoff {target['as_of']}")
    aliases = ", ".join(ascii_text(alias) for alias in target.get("aliases", [])) or "None"
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 43, f"Aliases: {aliases[:90]}")

    y = PAGE_H - 67
    canvas.setFillColor(THEME["light"])
    canvas.roundRect(MARGIN, y - 61, CONTENT_W, 57, 4, fill=1, stroke=0)
    confidence_color = {"High": THEME["teal"], "Moderate": THEME["amber"], "Low": THEME["red"]}[assessment["confidence"]]
    canvas.setFillColor(confidence_color)
    canvas.roundRect(MARGIN + 6, y - 30, 66, 22, 4, fill=1, stroke=0)
    canvas.setFillColor(THEME["navy"] if assessment["confidence"] == "Moderate" else THEME["white"])
    canvas.setFont("Helvetica-Bold", 8.5)
    canvas.drawCentredString(MARGIN + 39, y - 22, assessment["confidence"].upper())

    verdict = p(assessment["verdict"], 7.2, bold=True)
    verdict.wrapOn(canvas, CONTENT_W - 86, 30)
    verdict.drawOn(canvas, MARGIN + 80, y - 28)

    labels = [
        ("Opportunity", assessment["opportunity"], THEME["teal"]),
        ("Key risk", assessment["key_risk"], THEME["red"]),
        ("Limits", joined(assessment["limitations"]), THEME["muted"]),
    ]
    col_w = CONTENT_W / 3
    for index, (label, text, color) in enumerate(labels):
        x = MARGIN + index * col_w + 7
        canvas.setFillColor(color)
        canvas.setFont("Helvetica-Bold", 6.2)
        canvas.drawString(x, y - 41, label.upper())
        paragraph = p(text, 5.35)
        paragraph.wrapOn(canvas, col_w - 14, 15)
        paragraph.drawOn(canvas, x, y - 60)
    return y - 67


def draw_page_one(canvas: Canvas, report: dict) -> None:
    y = draw_header(canvas, report)
    y = draw_section_title(
        canvas, "Target-wide phenotype evidence", y,
        "Indication-agnostic | Strength: Strong / Moderate / Limited / Gap",
    )
    phenotype_rows = []
    for row in sorted(report["phenotypes"], key=lambda item: (EVIDENCE_ORDER[item["category"]], -item["score"])):
        phenotype_rows.append(
            [
                phenotype_cell(row),
                direction_cell(row),
                f"{row['category']} - {EVIDENCE_STRENGTH[row['score']]}",
                f"{row['evidence']} {refs(row)}",
                row["tissue"],
            ]
        )
    y = draw_table(
        canvas,
        y,
        ["Phenotype / effect", "Modulation / result", "Type / strength", "Evidence", "Tissue"],
        phenotype_rows,
        [96, 121, 70, 315, 118],
        font_size=5.8,
        max_height=160,
        padding_y=1.0,
    )

    y = draw_section_title(canvas, "Modality strategy", y)
    modality_rows = []
    for row in sorted(report["modalities"], key=lambda item: item["rank"]):
        modality_rows.append(
            [
                f"{row['rank']}. {row['modality']}",
                modality_case_cell(row),
                modality_decision_cell(row),
            ]
        )
    y = draw_table(
        canvas,
        y,
        ["Rank / modality", "First-principles case", "Evidence, risk, test and patent distance"],
        modality_rows,
        [135, 302.5, 282.5],
        font_size=5.55,
        max_height=182,
        padding_y=1.6,
    )

    y = draw_section_title(canvas, "Candidate landscape", y)
    candidate_rows = []
    for row in report["candidates"]:
        status = row["status"]
        if row["reason"] not in {"Not applicable", "None"}:
            status += f"; {row['reason']}"
        candidate_rows.append(
            [
                row["name"], row["modality"], row["sponsor"], row["route"], row["directness"],
                row["indication"], f"{status} {refs(row)}",
            ]
        )
    y = draw_table(
        canvas,
        y,
        ["Candidate", "Modality", "Sponsor", "Route", "Directness", "Indication", "Status / reason"],
        candidate_rows,
        [90, 72, 92, 58, 58, 90, 260],
        font_size=5.2,
        max_height=max(154, y - 25),
        padding_y=1.05,
    )
    if y < 24:
        raise ValueError(f"Page 1 content enters footer area ({y:.1f} < 24); shorten report JSON")


def _node_position(node: dict, x: float, y: float, width: float, height: float) -> tuple[float, float]:
    return x + float(node["x"]) * width, y + float(node["y"]) * height


def draw_mechanism(canvas: Canvas, mechanism: dict, y_top: float, height: float) -> float:
    area_x = MARGIN
    area_y = y_top - height
    area_w = CONTENT_W
    canvas.setFillColor(THEME["light"])
    canvas.roundRect(area_x, area_y, area_w, height, 4, fill=1, stroke=0)
    inner_x, inner_y = area_x + 12, area_y + 24
    inner_w, inner_h = area_w - 24, height - 35
    nodes = {node["id"]: node for node in mechanism["nodes"]}
    positions = {node_id: _node_position(node, inner_x, inner_y, inner_w, inner_h) for node_id, node in nodes.items()}
    box_w, box_h = 104, 34

    canvas.setStrokeColor(THEME["muted"])
    canvas.setFillColor(THEME["muted"])
    for edge in mechanism["edges"]:
        x1, y1 = positions[edge["from"]]
        x2, y2 = positions[edge["to"]]
        dx, dy = x2 - x1, y2 - y1
        distance = max(math.hypot(dx, dy), 1)
        ux, uy = dx / distance, dy / distance
        start_x, start_y = x1 + ux * box_w * 0.42, y1 + uy * box_h * 0.42
        end_x, end_y = x2 - ux * box_w * 0.42, y2 - uy * box_h * 0.42
        canvas.setLineWidth(0.8)
        canvas.line(start_x, start_y, end_x, end_y)
        angle = math.atan2(end_y - start_y, end_x - start_x)
        for delta in (2.55, -2.55):
            canvas.line(end_x, end_y, end_x + 6 * math.cos(angle + delta), end_y + 6 * math.sin(angle + delta))
        edge_refs = refs(edge)
        label = f"{ascii_text(edge['label'])} {edge_refs}"
        mid_x, mid_y = (start_x + end_x) / 2, (start_y + end_y) / 2
        # Diagonal branches have little horizontal clearance between fixed-width
        # boxes. Keep only the citation tag on the arrow; the connected node
        # labels carry the branch semantics. Horizontal chains retain full labels.
        if abs(dy) > 20:
            label = edge_refs
        else:
            mid_y += 23
        canvas.setFont("Helvetica", 5.1)
        canvas.setFillColor(THEME["muted"])
        label_w = min(stringWidth(label, "Helvetica", 5.1) + 4, 108)
        canvas.setFillColor(THEME["white"])
        canvas.rect(mid_x - label_w / 2, mid_y - 4, label_w, 9, fill=1, stroke=0)
        canvas.setFillColor(THEME["muted"])
        canvas.drawCentredString(mid_x, mid_y - 1, label[:50])

    for node_id, node in nodes.items():
        cx, cy = positions[node_id]
        canvas.setFillColor(THEME["white"])
        canvas.setStrokeColor(THEME["blue"])
        canvas.setLineWidth(0.9)
        canvas.roundRect(cx - box_w / 2, cy - box_h / 2, box_w, box_h, 4, fill=1, stroke=1)
        label = f"{node['label']} {refs(node)}"
        paragraph = p(label, 5.4, bold=True, align=TA_CENTER)
        _, ph = paragraph.wrap(box_w - 8, box_h - 4)
        paragraph.drawOn(canvas, cx - box_w / 2 + 4, cy - ph / 2)

    caption = p(mechanism["caption"], 5.6, color=THEME["muted"])
    caption.wrapOn(canvas, inner_w, 18)
    caption.drawOn(canvas, inner_x, area_y + 5)
    return area_y - 5


def draw_sources(canvas: Canvas, sources: list[dict], y: float) -> None:
    entries: list[Paragraph] = []
    for source in sources:
        stable = source.get("pmid") or source.get("nct") or source.get("doi") or source["type"]
        domain = urlparse(source["url"]).netloc.replace("www.", "")
        markup = (
            f"<b>{html.escape(source['id'])}</b> {html.escape(ascii_text(source['citation']))} "
            f"({html.escape(ascii_text(stable))}) "
            f"<link href=\"{html.escape(source['url'], quote=True)}\" color=\"#2E6F9E\">{html.escape(domain)}</link>"
        )
        entries.append(rich_p(markup, 5.3))
    rows = []
    midpoint = (len(entries) + 1) // 2
    for index in range(midpoint):
        left = entries[index]
        right = entries[index + midpoint] if index + midpoint < len(entries) else p("", 5.3)
        rows.append([left, right])
    draw_table(
        canvas,
        y,
        ["Sources 1", "Sources 2"],
        rows,
        [CONTENT_W / 2, CONTENT_W / 2],
        font_size=5.3,
        max_height=max(110, y - 25),
        padding_y=1.5,
    )


def draw_rating_key(canvas: Canvas, y: float) -> float:
    rows = [
        [
            "Evidence strength",
            "Strong = direct/replicated within category; Moderate = supportive with important qualification; Limited = indirect or single-study; Gap = none verified. Categories are not interchangeable.",
        ],
        [
            "Model availability",
            "Stocked = exact model orderable; Recoverable = repository sperm/cryo/special access; Published only = documented, not distributed; Build = assemble or engineer.",
        ],
        [
            "Setup effort",
            "Routine = off-the-shelf; Qualify = published protocol needs local qualification; Specialist = breeding/differentiation/aging/surgery; Develop = de novo engineering and validation.",
        ],
        [
            "Patent burden",
            "Low / Moderate / High = scoped technical design-around burden; Unknown = insufficient detail. Planning screen only - not a claim chart, FTO, or opinion on non-infringement, validity, enforceability, ownership, or clearance; counsel must review each jurisdiction.",
        ],
    ]
    return draw_table(
        canvas, y, ["Display", "Meaning"], rows, [105, 615],
        font_size=5.45, max_height=82, padding_y=1.8,
    )


def draw_page_two(canvas: Canvas, report: dict) -> None:
    canvas.setFillColor(THEME["navy"])
    canvas.rect(0, PAGE_H - 31, PAGE_W, 31, fill=1, stroke=0)
    canvas.setFillColor(THEME["white"])
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(MARGIN, PAGE_H - 20, f"{ascii_text(report['target']['symbol'])} - translation and mechanism")
    y = PAGE_H - 38
    for title, key in (("In vitro assays", "in_vitro_assays"), ("In vivo assays", "in_vivo_assays")):
        y = draw_section_title(
            canvas, title, y,
            "Availability: Stocked / Recoverable / Published only / Build | Setup: Routine / Qualify / Specialist / Develop",
        )
        rows = []
        for row in report[key]:
            rows.append(
                [
                    row["method"], assay_call_cell(row), row["mechanism_link"],
                    model_availability_cell(row), setup_precedent_cell(row),
                ]
            )
        is_in_vivo = key == "in_vivo_assays"
        y = draw_table(
            canvas,
            y,
            ["Readout method", "Exact assay and decision rule", "Mechanism tested", "Model / availability", "Setup / Phase 2 precedent"],
            rows,
            [78, 190, 102, 198, 152] if is_in_vivo else [82, 205, 112, 145, 176],
            font_size=5.3,
            max_height=198 if is_in_vivo else 155,
            padding_y=1.8,
        )
    y = draw_section_title(canvas, "Mechanism", y)
    draw_mechanism(canvas, report["mechanism"], y, min(180, y - 28))


def draw_page_three(canvas: Canvas, report: dict) -> None:
    canvas.setFillColor(THEME["navy"])
    canvas.rect(0, PAGE_H - 31, PAGE_W, 31, fill=1, stroke=0)
    canvas.setFillColor(THEME["white"])
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(MARGIN, PAGE_H - 20, f"{ascii_text(report['target']['symbol'])} - source appendix")
    y = draw_section_title(canvas, "Rating key", PAGE_H - 38)
    y = draw_rating_key(canvas, y)
    y = draw_section_title(canvas, "Sources", y)
    draw_sources(canvas, report["sources"], y)


def footer(canvas: Canvas, report: dict, page_number: int) -> None:
    canvas.setStrokeColor(colors.HexColor("#D5DDE2"))
    canvas.line(MARGIN, 21, PAGE_W - MARGIN, 21)
    canvas.setFillColor(THEME["muted"])
    canvas.setFont("Helvetica", 5.6)
    canvas.drawString(MARGIN, FOOTER_Y, "Independent specialist research + adversarial evidence review; no Neon database used.")
    page_label = f"page {page_number}/2 report" if page_number <= 2 else "source appendix"
    canvas.drawRightString(PAGE_W - MARGIN, FOOTER_Y, f"{report['target']['symbol']} | {page_label}")


def render(report: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(str(output), pagesize=(PAGE_W, PAGE_H), pageCompression=1)
    canvas.setTitle(f"{report['target']['symbol']} target quality assessment")
    canvas.setAuthor("Predictive Validity target-report workflow")
    draw_page_one(canvas, report)
    footer(canvas, report, 1)
    canvas.showPage()
    draw_page_two(canvas, report)
    footer(canvas, report, 2)
    canvas.showPage()
    draw_page_three(canvas, report)
    footer(canvas, report, 3)
    canvas.save()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Validated report JSON")
    parser.add_argument("output", type=Path, help="Output PDF")
    args = parser.parse_args()
    with args.input.open(encoding="utf-8") as handle:
        report = validate_data(json.load(handle))
    render(report, args.output)
    print(f"Rendered {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
