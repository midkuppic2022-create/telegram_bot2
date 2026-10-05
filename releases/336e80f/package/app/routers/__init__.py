from aiogram import Router

from app.routers import admin, common, fallback, inspections, registration, reports


def build_root_router() -> Router:
    router = Router(name="root")
    router.include_routers(
        registration.router,
        common.router,
        inspections.router,
        reports.router,
        admin.router,
        fallback.router,
    )
    return router
