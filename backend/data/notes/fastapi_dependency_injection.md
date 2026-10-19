---
title: FastAPI 依赖注入笔记
tags: [fastapi, python, 后端]
created_at: 2026-09-12
---

# FastAPI 依赖注入笔记

## Depends 基本用法

FastAPI 的依赖注入通过 `Depends` 实现。依赖项可以是函数或类，在请求处理前执行。

```python
from fastapi import Depends, FastAPI

app = FastAPI()

async def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/users")
async def list_users(db=Depends(get_db)):
    return db.query(User).all()
```

## 依赖的生命周期

- 每个请求创建一次依赖实例（默认）。
- 使用 `yield` 的依赖会在响应发送后执行清理代码。
- 类依赖：把依赖写成类，配合 `__init__` 参数更易于组织多个相关依赖。

## 常见坑

1. 依赖函数里抛出的异常会被 FastAPI 转成 500；要返回 4xx 需主动 `raise HTTPException`。
2. `yield` 之后的代码在 StreamingResponse 场景下执行时机是流结束之后。
3. 子依赖与父依赖共享请求级缓存：`use_cache=True` 默认开启。
