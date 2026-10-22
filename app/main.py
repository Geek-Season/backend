from fastapi import FastAPI

from app.modules.auth.router import router as auth_router
from app.modules.exam.router import router as exam_router
from app.modules.progress.router import router as progress_router
from app.modules.rating.router import router as rating_router
from app.modules.tasks.router import numbers_router, tasks_router

app = FastAPI(
    title="Geek Season",
    description="Подготовка к ЕГЭ по информатике: банк номеров, прототипы и проверка ответов.",
    version="0.5.0",
)

# Подключаем модули к одному приложению.
app.include_router(auth_router)
app.include_router(numbers_router)
app.include_router(tasks_router)
app.include_router(progress_router)
app.include_router(exam_router)
app.include_router(rating_router)


@app.get("/", tags=["Проверка запуска"])
def root():
    return {"message": "Geek Season работает", "docs": "/docs"}
