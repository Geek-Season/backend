from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

from app.modules.achievements.router import router as achievements_router
from app.modules.admin.router import router as admin_router
from app.modules.auth.router import avatars_router
from app.modules.auth.router import router as auth_router
from app.modules.exam.router import router as exam_router
from app.modules.notifications.router import router as notifications_router
from app.modules.progress.router import router as progress_router
from app.modules.pts.router import router as pts_router
from app.modules.rating.router import router as rating_router
from app.modules.tasks.router import files_router, numbers_router, tasks_router

app = FastAPI(
    title="Geek Season",
    description="Подготовка к ЕГЭ по информатике: банк номеров, прототипы и проверка ответов.",
    version="0.8.0",
)

# Сайт на другом адресе (например, GitHub Pages), API — на сервере: браузеру нужно
# разрешение обращаться к API с адреса сайта. Вход — по заголовку Authorization,
# куки не используются, поэтому allow_credentials не нужен.
if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=3600,
    )

# Подключаем модули к одному приложению.
app.include_router(auth_router)
app.include_router(avatars_router)
app.include_router(numbers_router)
app.include_router(tasks_router)
app.include_router(files_router)
app.include_router(progress_router)
app.include_router(pts_router)
app.include_router(exam_router)
app.include_router(rating_router)
app.include_router(achievements_router)
app.include_router(notifications_router)
app.include_router(admin_router)


@app.get("/", tags=["Проверка запуска"])
def root():
    return {"message": "Geek Season работает", "docs": "/docs"}
