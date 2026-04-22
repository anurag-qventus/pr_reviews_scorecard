import io
import os
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone

_src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_project_dir = os.path.dirname(_src_dir)
sys.path.insert(0, _src_dir)
sys.path.insert(0, _project_dir)

from fastapi import Depends, FastAPI, HTTPException, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from langfuse.decorators import observe, langfuse_context

from db.api_keys import create_key, init_db, list_keys, revoke_key, validate_key
from services.llm_service import LLMService
from services.pr_service import collect_pr_text
from utils import extract_score, generate_pdf_bytes

ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")

app = FastAPI(title="PR Reviews Scorecard API", version="1.0.0")
init_db()

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_admin_header = APIKeyHeader(name="X-Admin-Secret", auto_error=False)


def require_api_key(key: str = Security(_api_key_header)) -> str:
    if not key or not validate_key(key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    return key


def require_admin(secret: str = Security(_admin_header)) -> None:
    if not ADMIN_SECRET:
        raise HTTPException(status_code=500, detail="ADMIN_SECRET is not configured on the server.")
    if secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid admin secret.")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    github_usernames: list[str]
    current_from: date
    current_to: date
    previous_from: date
    previous_to: date
    duration_label: str = "Custom Range"
    generate_pdf: bool = False


class AnalyzeResponse(BaseModel):
    github_username: str
    duration_label: str
    current_from: date
    current_to: date
    previous_from: date
    previous_to: date
    current_score: float | None
    previous_score: float | None
    analysis: str
    error: str | None = None


_ANALYZE_FIELDS        = "github_usernames, current_from, current_to, previous_from, previous_to, duration_label, generate_pdf"
_PRESET_ANALYZE_FIELDS = "github_usernames, duration_label, generate_pdf"
_ADMIN_KEY_FIELDS      = "label"

_ENDPOINT_FIELDS = {
    "/analyze":        _ANALYZE_FIELDS,
    "/analyze/preset": _PRESET_ANALYZE_FIELDS,
    "/admin/keys":     _ADMIN_KEY_FIELDS,
}

_TYPE_MESSAGES = {
    "missing":                        "Missing required field '{field}'.",
    "date_from_datetime_parsing":     "Invalid date format for '{field}'. Expected YYYY-MM-DD (e.g. 2024-01-31).",
    "date_parsing":                   "Invalid date format for '{field}'. Expected YYYY-MM-DD (e.g. 2024-01-31).",
    "list_type":                      "'{field}' must be a list (e.g. [\"username1\", \"username2\"]).",
    "string_type":                    "'{field}' must be a string.",
    "bool_type":                      "'{field}' must be true or false.",
}


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    endpoint = request.url.path
    accepted = _ENDPOINT_FIELDS.get(endpoint, "see /docs for accepted fields")
    messages = []
    for err in exc.errors():
        field = err["loc"][-1] if len(err["loc"]) > 1 else str(err["loc"][0])
        err_type = err["type"]
        template = _TYPE_MESSAGES.get(err_type, "Invalid value for '{field}': " + err["msg"] + ".")
        messages.append(template.format(field=field))
    return JSONResponse(
        status_code=422,
        content={
            "error": "Invalid request",
            "details": messages,
            "accepted_fields": accepted,
            "docs": str(request.base_url) + "docs",
        },
    )


_PRESET_DURATIONS = {
    "3 Months": 90,
    "6 Months": 180,
    "1 Year":   365,
}


class PresetAnalyzeRequest(BaseModel):
    github_usernames: list[str]
    duration_label: str  # "3 Months", "6 Months", or "1 Year"
    generate_pdf: bool = False


class CreateKeyRequest(BaseModel):
    label: str


class KeyRecord(BaseModel):
    key: str
    label: str
    created_at: str
    is_active: bool


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------

def _pdf_filename(username: str, duration_label: str) -> str:
    return f"pr_scorecard_{username.replace(' ', '_')}_{duration_label.replace(' ', '_')}.pdf"


def _build_pdf_response(results: list[AnalyzeResponse]) -> Response:
    """
    Single user  → returns a PDF file directly.
    Multiple users → returns a ZIP containing one PDF per user (errors skipped).
    """
    successful = [r for r in results if not r.error]

    if not successful:
        raise HTTPException(status_code=404, detail="No PR data found for any of the requested users.")

    if len(results) == 1:
        r = successful[0]
        pdf = generate_pdf_bytes(
            user_label=r.github_username,
            date_range_label=r.duration_label,
            current_from=r.current_from,
            current_to=r.current_to,
            previous_from=r.previous_from,
            previous_to=r.previous_to,
            current_score=r.current_score,
            previous_score=r.previous_score,
            report_text=r.analysis,
        )
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={_pdf_filename(r.github_username, r.duration_label)}"},
        )

    # Multiple users — return a ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in successful:
            pdf = generate_pdf_bytes(
                user_label=r.github_username,
                date_range_label=r.duration_label,
                current_from=r.current_from,
                current_to=r.current_to,
                previous_from=r.previous_from,
                previous_to=r.previous_to,
                current_score=r.current_score,
                previous_score=r.previous_score,
                report_text=r.analysis,
            )
            zf.writestr(_pdf_filename(r.github_username, r.duration_label), pdf)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=pr_scorecards.zip"},
    )


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

@app.post("/analyze")
@observe(name="api-analyze")
def analyze(req: AnalyzeRequest, api_key: str = Depends(require_api_key)):
    langfuse_context.update_current_trace(
        user_id=api_key,
        tags=[req.duration_label, "custom-range"],
        metadata={
            "github_usernames": req.github_usernames,
            "duration_label":   req.duration_label,
            "current_from":     str(req.current_from),
            "current_to":       str(req.current_to),
            "previous_from":    str(req.previous_from),
            "previous_to":      str(req.previous_to),
            "generate_pdf":     req.generate_pdf,
        },
    )
    llm = LLMService()
    results = []

    for username in req.github_usernames:
        current_text  = collect_pr_text(username, req.current_from, req.current_to)
        previous_text = collect_pr_text(username, req.previous_from, req.previous_to)

        if not current_text and not previous_text:
            results.append(AnalyzeResponse(
                github_username=username,
                duration_label=req.duration_label,
                current_from=req.current_from,
                current_to=req.current_to,
                previous_from=req.previous_from,
                previous_to=req.previous_to,
                current_score=None,
                previous_score=None,
                analysis="",
                error=f"No PR data found for '{username}'. Run the scheduler bootstrap first.",
            ))
            continue

        analysis = llm.generate_comparative_response(
            user_login=username,
            current_text=current_text,
            previous_text=previous_text,
            duration_label=req.duration_label,
        )
        results.append(AnalyzeResponse(
            github_username=username,
            duration_label=req.duration_label,
            current_from=req.current_from,
            current_to=req.current_to,
            previous_from=req.previous_from,
            previous_to=req.previous_to,
            current_score=extract_score(analysis, "current"),
            previous_score=extract_score(analysis, "previous"),
            analysis=analysis,
        ))

    if req.generate_pdf:
        return _build_pdf_response(results)
    return results


@app.post("/analyze/preset")
@observe(name="api-analyze-preset")
def analyze_preset(req: PresetAnalyzeRequest, api_key: str = Depends(require_api_key)):
    langfuse_context.update_current_trace(
        user_id=api_key,
        tags=[req.duration_label, "preset"],
        metadata={
            "github_usernames": req.github_usernames,
            "duration_label":   req.duration_label,
            "generate_pdf":     req.generate_pdf,
        },
    )
    if req.duration_label not in _PRESET_DURATIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid duration_label '{req.duration_label}'. "
                   f"Must be one of: {list(_PRESET_DURATIONS.keys())}",
        )

    span = _PRESET_DURATIONS[req.duration_label]
    today = date.today()
    current_from  = today - timedelta(days=span)
    current_to    = today
    previous_from = today - timedelta(days=span * 2)
    previous_to   = today - timedelta(days=span + 1)

    llm = LLMService()
    results = []

    for username in req.github_usernames:
        current_text  = collect_pr_text(username, current_from, current_to)
        previous_text = collect_pr_text(username, previous_from, previous_to)

        if not current_text and not previous_text:
            results.append(AnalyzeResponse(
                github_username=username,
                duration_label=req.duration_label,
                current_from=current_from,
                current_to=current_to,
                previous_from=previous_from,
                previous_to=previous_to,
                current_score=None,
                previous_score=None,
                analysis="",
                error=f"No PR data found for '{username}'. Run the scheduler bootstrap first.",
            ))
            continue

        analysis = llm.generate_comparative_response(
            user_login=username,
            current_text=current_text,
            previous_text=previous_text,
            duration_label=req.duration_label,
        )
        results.append(AnalyzeResponse(
            github_username=username,
            duration_label=req.duration_label,
            current_from=current_from,
            current_to=current_to,
            previous_from=previous_from,
            previous_to=previous_to,
            current_score=extract_score(analysis, "current"),
            previous_score=extract_score(analysis, "previous"),
            analysis=analysis,
        ))

    if req.generate_pdf:
        return _build_pdf_response(results)
    return results


# ---------------------------------------------------------------------------
# Admin — API key management
# ---------------------------------------------------------------------------

@app.post("/admin/keys", response_model=KeyRecord)
def admin_create_key(req: CreateKeyRequest, _=Depends(require_admin)):
    key = create_key(req.label)
    return KeyRecord(
        key=key,
        label=req.label,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        is_active=True,
    )


@app.get("/admin/keys", response_model=list[KeyRecord])
def admin_list_keys(_=Depends(require_admin)):
    return [
        KeyRecord(
            key=k["key"],
            label=k["label"],
            created_at=k["created_at"],
            is_active=bool(k["is_active"]),
        )
        for k in list_keys()
    ]


@app.delete("/admin/keys/{key}")
def admin_revoke_key(key: str, _=Depends(require_admin)):
    if not revoke_key(key):
        raise HTTPException(status_code=404, detail="Key not found.")
    return {"message": "Key revoked."}
