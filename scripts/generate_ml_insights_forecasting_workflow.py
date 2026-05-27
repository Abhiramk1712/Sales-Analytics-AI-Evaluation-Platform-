from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


OUTPUT_PATH = Path("docs/ml_insights_forecasting_workflow.png")


def load_font(size: int):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def draw_box(draw: ImageDraw.ImageDraw, box, title: str, lines: list[str], fill, outline, title_font, body_font):
    draw.rounded_rectangle(box, radius=18, fill=fill, outline=outline, width=3)
    x1, y1, x2, y2 = box
    draw.text((x1 + 16, y1 + 12), title, font=title_font, fill=(20, 20, 20))

    y = y1 + 56
    for line in lines:
        draw.text((x1 + 18, y), f"- {line}", font=body_font, fill=(35, 35, 35))
        y += 32


def draw_arrow(draw: ImageDraw.ImageDraw, start, end, color=(70, 70, 70), width=4):
    sx, sy = start
    ex, ey = end
    draw.line([sx, sy, ex, ey], fill=color, width=width)

    head = 12
    if abs(ex - sx) >= abs(ey - sy):
        if ex >= sx:
            points = [(ex, ey), (ex - head, ey - head // 2), (ex - head, ey + head // 2)]
        else:
            points = [(ex, ey), (ex + head, ey - head // 2), (ex + head, ey + head // 2)]
    else:
        if ey >= sy:
            points = [(ex, ey), (ex - head // 2, ey - head), (ex + head // 2, ey - head)]
        else:
            points = [(ex, ey), (ex - head // 2, ey + head), (ex + head // 2, ey + head)]
    draw.polygon(points, fill=color)


def draw_label(draw: ImageDraw.ImageDraw, pos, text: str, font):
    draw.text(pos, text, font=font, fill=(85, 85, 85))


def build_workflow_image(path: Path) -> None:
    width, height = 2800, 1900
    img = Image.new("RGB", (width, height), (248, 250, 252))
    draw = ImageDraw.Draw(img)

    title_font = load_font(52)
    section_font = load_font(30)
    body_font = load_font(23)
    label_font = load_font(22)

    draw.text((640, 24), "ML Insights and Forecasting Workflow", font=title_font, fill=(24, 36, 60))
    draw.text(
        (640, 92),
        "Sales Analytics AI - End-to-end runtime flow from UI controls to forecast insight delivery",
        font=label_font,
        fill=(65, 80, 100),
    )

    frontend_controls = (120, 170, 900, 450)
    api_calls = (980, 170, 1830, 450)
    history_build = (120, 560, 900, 900)
    model_orchestration = (980, 560, 1830, 900)
    model_eval = (1910, 560, 2690, 900)
    scenario_and_payload = (780, 1010, 1700, 1320)
    supplemental_insights = (1780, 1010, 2690, 1320)
    frontend_render = (780, 1430, 1700, 1750)

    draw_box(
        draw,
        frontend_controls,
        "1) Frontend Controls (MLInsightsPage)",
        [
            "User selects target, horizon, scenario",
            "Optional sequence/LSTM candidate toggle",
            "Debounced trigger to reduce API chatter",
            "Page refresh and interaction state handling",
        ],
        fill=(226, 240, 255),
        outline=(56, 119, 194),
        title_font=section_font,
        body_font=body_font,
    )

    draw_box(
        draw,
        api_calls,
        "2) API Requests (FastAPI)",
        [
            "POST /ml/forecast/run (core forecast)",
            "GET /ml/forecast/targets (metadata)",
            "GET /ml/explain/global-importance",
            "GET /ml/evaluate/deal-scoring",
            "GET /ml/cluster/reps",
        ],
        fill=(232, 252, 239),
        outline=(34, 139, 94),
        title_font=section_font,
        body_font=body_font,
    )

    draw_box(
        draw,
        history_build,
        "3) Target-specific History Loader",
        [
            "_load_history_for_forecast_type()",
            "Revenue, ARR, pipeline, booking, payout",
            "Commit/best_case probability transforms",
            "Fallback source handling + source warnings",
            "Period normalization to monthly series",
        ],
        fill=(255, 244, 229),
        outline=(191, 120, 38),
        title_font=section_font,
        body_font=body_font,
    )

    draw_box(
        draw,
        model_orchestration,
        "4) Forecasting Lab Orchestration",
        [
            "compare_models_for_target()",
            "run_model_forecast() across candidates",
            "Models: naive, MA, ETS, HW, ridge, SARIMAX, optional LSTM",
            "Holdout backtesting for each model",
            "Data profile extraction (trend, seasonality, volatility)",
        ],
        fill=(245, 237, 255),
        outline=(119, 83, 181),
        title_font=section_font,
        body_font=body_font,
    )

    draw_box(
        draw,
        model_eval,
        "5) Model Selection and Ranking",
        [
            "Adaptive score from MAPE, sMAPE, bias, DA",
            "Suitability bonus/penalty by data profile",
            "Directional-accuracy thresholds",
            "Optional DA-weighted ensemble top models",
            "Best strategy selected for requested target",
        ],
        fill=(255, 237, 237),
        outline=(185, 66, 66),
        title_font=section_font,
        body_font=body_font,
    )

    draw_box(
        draw,
        scenario_and_payload,
        "6) Scenario and Forecast Payload",
        [
            "run_forecast_for_target()",
            "Apply base/optimistic/conservative scenario",
            "Generate values, bounds, leaderboard, backtest",
            "Attach assumptions, business explanation, warnings",
            "Return generated_at + source metadata",
        ],
        fill=(236, 239, 244),
        outline=(75, 85, 99),
        title_font=section_font,
        body_font=body_font,
    )

    draw_box(
        draw,
        supplemental_insights,
        "7) Supplemental ML Insights",
        [
            "Global feature importance (deal-scoring model)",
            "Deal scoring evaluation metrics",
            "Rep clustering output and persona hints",
            "Used to contextualize forecast decisions",
        ],
        fill=(255, 247, 221),
        outline=(170, 124, 33),
        title_font=section_font,
        body_font=body_font,
    )

    draw_box(
        draw,
        frontend_render,
        "8) Frontend Insight Rendering",
        [
            "Scenario chart with confidence range",
            "Model leaderboard and quality verdict",
            "Warnings and business recommendation text",
            "Persona and explainability side-panels",
            "User-ready planning insights",
        ],
        fill=(226, 240, 255),
        outline=(56, 119, 194),
        title_font=section_font,
        body_font=body_font,
    )

    # Main arrows
    draw_arrow(draw, (900, 310), (980, 310))
    draw_label(draw, (915, 270), "request payload", label_font)

    draw_arrow(draw, (1410, 450), (520, 560))
    draw_arrow(draw, (1410, 450), (1410, 560))

    draw_arrow(draw, (900, 730), (980, 730))
    draw_label(draw, (910, 690), "normalized history", label_font)

    draw_arrow(draw, (1830, 730), (1910, 730))
    draw_label(draw, (1842, 690), "metrics + scores", label_font)

    draw_arrow(draw, (2300, 900), (1510, 1010))
    draw_arrow(draw, (1410, 900), (1240, 1010))

    draw_arrow(draw, (1700, 1160), (1780, 1160))
    draw_label(draw, (1708, 1120), "context", label_font)

    draw_arrow(draw, (1240, 1320), (1240, 1430))
    draw_arrow(draw, (2230, 1320), (1530, 1430))

    # Footer
    draw.text(
        (260, 1810),
        "Outcome: User receives forecast + confidence + model rationale + warnings + ML context in one decision flow.",
        font=label_font,
        fill=(40, 40, 40),
    )

    img.save(path)


def main() -> None:
    build_workflow_image(OUTPUT_PATH)


if __name__ == "__main__":
    main()
