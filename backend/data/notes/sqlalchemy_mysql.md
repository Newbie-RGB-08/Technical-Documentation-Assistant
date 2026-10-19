---
title: SQLAlchemy 连接 MySQL 要点
tags: [sqlalchemy, mysql, python]
created_at: 2026-09-28
---

# SQLAlchemy 连接 MySQL 要点

## 连接串

```python
from urllib.parse import quote_plus
from sqlalchemy import create_engine

password = quote_plus("rgb123456!@#")  # 含特殊字符必须 URL 编码
engine = create_engine(
    f"mysql+pymysql://root:{password}@localhost:3306/rag_meta?charset=utf8mb4",
    pool_pre_ping=True,
)
```

## 常见坑

1. **密码不编码**：密码里的 `@` 会被当成主机分隔符，`#` 会截断连接串，host 被解析成乱码。
2. **charset**：必须显式 `charset=utf8mb4`，否则中文和 emoji 存不进去。
3. **pool_pre_ping**：MySQL 默认 8 小时断开空闲连接，不开 pre_ping 会报 `MySQL server has gone away`。

## 会话管理

```python
from sqlalchemy.orm import sessionmaker

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

with SessionLocal() as session:
    session.add(obj)
    session.commit()
```

- 推荐 `with` 语法，自动关闭会话。
- `expire_on_commit=False` 避免 commit 后访问属性触发懒加载报错。
