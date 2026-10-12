"""FastAPI 入口：案件、审批、报告与前端静态站点。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agriagents.api.service import CaseService


def get_service(request: Request) -> CaseService:
    return request.app.state.case_service


ServiceDep = Annotated[CaseService, Depends(get_service)]


class CreateCaseRequest(BaseModel):
    site_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    site_type: Literal["field", "greenhouse", "orchard", "machine"] = "field"
    as_of: date
    # 本地 API 暂不提供鉴权；因此只允许干跑，避免 HTTP 请求直接触发物理设备。
    dry_run: Literal[True] = True
    note: str = Field(default="", max_length=1000)


class ApprovalRequest(BaseModel):
    approved: bool
    approver: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=1000)


def create_app(service: CaseService | None = None) -> FastAPI:
    app = FastAPI(title="AgriAgents API", version="0.1.0")
    app.state.case_service = service or CaseService()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8000", "http://localhost:8000", "http://localhost:8001"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "checkpoint": True}

    @app.get("/api/cases")
    def list_cases(service: ServiceDep) -> list[dict]:
        return service.list_cases()

    @app.post("/api/cases", status_code=status.HTTP_202_ACCEPTED)
    def create_case(
        payload: CreateCaseRequest,
        background: BackgroundTasks,
        service: ServiceDep,
    ) -> dict:
        case = service.create_case(
            site_id=payload.site_id,
            site_type=payload.site_type,
            as_of=payload.as_of.isoformat(),
            dry_run=payload.dry_run,
            note=payload.note,
        )
        background.add_task(service.run_case, case["id"])
        return service.get_case(case["id"], refresh=False)

    @app.get("/api/cases/{case_id}")
    def get_case(case_id: str, service: ServiceDep) -> dict:
        try:
            return service.get_case(case_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="案件不存在") from exc

    @app.post("/api/cases/{case_id}/approval", status_code=status.HTTP_202_ACCEPTED)
    def approve_case(
        case_id: str,
        payload: ApprovalRequest,
        background: BackgroundTasks,
        service: ServiceDep,
    ) -> dict:
        try:
            case = service.get_case(case_id)
            if case["status"] != "awaiting_approval":
                raise HTTPException(status_code=409, detail=f"案件状态为 {case['status']}，不能审批")
            service.store.update(case_id, status="resuming", error=None)
            background.add_task(
                _resume_in_background,
                service,
                case_id,
                payload.approved,
                payload.approver,
                payload.note,
            )
            return service.get_case(case_id, refresh=False)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="案件不存在") from exc

    @app.get("/api/cases/{case_id}/report", response_class=PlainTextResponse)
    def get_report(case_id: str, service: ServiceDep) -> str:
        try:
            return service.report_markdown(case_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="案件不存在") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (frontend / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(frontend), html=True), name="frontend")
    return app


def _resume_in_background(
    service: CaseService,
    case_id: str,
    approved: bool,
    approver: str,
    note: str,
) -> None:
    try:
        service.approve_case(case_id, approved=approved, approver=approver, note=note)
    except Exception:
        # service 已记录 failed；后台任务不再把异常抛给 ASGI 服务器。
        return


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()

