# 可选解析引擎：MinerU / PaddleX（Docker Compose）

本目录用于编排自托管的 [MinerU](https://github.com/opendatalab/MinerU) 与
[PaddleX PP-StructureV3](https://github.com/PaddlePaddle/PaddleX) 解析服务。
两者面向扫描版 PDF / 图文混排文档，比本地 RapidOCR 更稳定（表格/公式/图片）。

> 说明：`native`（内置文本解析）与 `RapidOCR`（本地进程）都不需要本 Compose；
> 只有需要 MinerU / PaddleX 时才启动对应容器。

---

## 一、四档部署

| 档位 | 引擎 | 启动方式 | 最低资源建议 |
|---|---|---|---|
| 1. 仅 native | 内置文本/Office 解析 | 无容器 | 1C / 2G |
| 2. +RapidOCR | 本地 ONNX OCR（PDF/图片） | `pip install -r backend/requirements-ocr.txt` | 2C / 4G |
| 3. +MinerU | 扫描 PDF 质量最好 | 本 Compose | 8C / 16G（GPU 更佳） |
| 4. +PaddleX | 中文版面/表格强 | 本 Compose | 4C / 8G（GPU 更佳） |

> 档位 3 与 4 可叠加（同时 `--profile mineru --profile paddlex`）。

## 二、前置条件

- Docker Engine ≥ 24 + Docker Compose v2；
- 局域网 `https://huggingface.co`、`https://paddlepaddle` 模型源可访问（或自配镜像）；
- 首次启动会拉取镜像并下载模型，视网络约需数 GB / 数分钟。

## 三、启动 / 停止

```bash
cd deploy/parsers

# 仅 MinerU
docker compose --profile mineru up -d
# 仅 PaddleX
docker compose --profile paddlex up -d
# 两者都起
docker compose --profile mineru --profile paddlex up -d

# 停止（保留命名卷里的模型缓存）
docker compose --profile mineru --profile paddlex down

# 彻底删除（连同模型缓存卷，下次启动需重新下载）
docker compose --profile mineru --profile paddlex down -v
```

### GPU（可选）

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile mineru --profile paddlex up -d
```

GPU 需要本机 NVIDIA 驱动 + `nvidia-container-toolkit`；不可用时会回退到 CPU override
（默认 `docker-compose.yml` 已设 CPU）。

## 四、健康检查

```bash
# MinerU
curl -s http://127.0.0.1:30001/openapi.json | grep -o '/file_parse'
# PaddleX
curl -s http://127.0.0.1:8080/health
```

容器自带 healthcheck，`docker ps` 中 `STATUS (healthy)` 即就绪。

## 五、与后端对接

在 `backend/.env` 中确认指向本机端口：

```
MINERU_API_URI=http://127.0.0.1:30001
PADDLEX_URI=http://127.0.0.1:8080
UPLOAD_PDF_TEXT_MIN_CHARS=80   # 扫描 PDF（低于该阈值）才会路由到 OCR 引擎
```

后端 `/api/uploads/engines` 会返回各引擎健康快照（30s 缓存）；引擎不可达时
`auto` 路由自动降级到下一个可用引擎，不会中断上传。

## 六、排障

| 现象 | 处理 |
|---|---|
| `curl` 连不上 | 端口冲突，改 `deploy/parsers/.env` 里的 `MINERU_PORT` / `PADDLEX_PORT`，并在 backend `.env` 同步改 URI |
| 首次拉模型失败 | 自配 HF 镜像（`HF_ENDPOINT`）或代理，重试 `docker compose ... up -d` |
| GPU 拉起失败 | 确认 `nvidia-smi` 正常；或去掉 `docker-compose.gpu.yml` 改用 CPU override |
| 容器卡 healthcheck | 首次加载模型慢，`start_period` 已放宽；查看 `docker compose logs mineru/paddlex` |

## 七、安全约定

- 不放置任何云 API Key；
- 不挂载用户上传目录（引擎只读写各自的模型缓存卷，与后台隔离）；
- 端口一律 `127.0.0.1` 绑定，不对外暴露。