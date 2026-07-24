"""HTML pages: landing (capture + recordings list) and the player."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import metadata
from ..db import get_session
from ..models import Job
from ..storage import get_storage

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return RedirectResponse(url="/static/favicon.svg")


@router.get("/", response_class=HTMLResponse)
def index(request: Request, session: Session = Depends(get_session)):
    jobs = session.scalars(select(Job).order_by(Job.created_at.desc())).all()
    storage = get_storage()
    thumbs = {
        job.id: f"{storage.url(ref[0])}?v={ref[1]}"
        for job in jobs
        if (ref := metadata.thumbnail_ref(job.id))
    }
    return templates.TemplateResponse(request, "index.html", {"jobs": jobs, "thumbs": thumbs})


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def player(job_id: str, request: Request, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        return templates.TemplateResponse(request, "not_found.html", {}, status_code=404)
    return templates.TemplateResponse(request, "player.html", {"job_id": job_id})
