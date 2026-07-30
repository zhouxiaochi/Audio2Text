# Audio2Text Backend

FastAPI backend for persistent, restart-safe audio transcription, speaker inference,
Simplified Chinese translation, and Markdown/DOCX export.

## Requirements

- Python 3.11+
- `ffmpeg` and `ffprobe` available on `PATH`
- An OpenAI-compatible remote endpoint supporting:
  - `POST /audio/transcriptions`
  - `POST /chat/completions`

Configuration is read from environment variables:

```text
REMOTE_BASE_URL=https://openrouter.ai/api/v1
REMOTE_API_KEY=...
TRANSCRIPTION_MODEL=openai/whisper-large-v3
LLM_MODEL=openai/gpt-4o-mini
```

## Run

```powershell
python -m pip install -e ".[test]"
uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

The service uses one in-process worker. Run exactly one Uvicorn worker to preserve this
guarantee. Jobs and checkpoints are stored under `data/` by default.

## API

- `POST /jobs` — create a job with multipart field `file`
- `GET /jobs` — list jobs
- `GET /jobs/{id}` — get job details and progress
- `GET /jobs/{id}/markdown` — read editable Markdown
- `PUT /jobs/{id}/markdown` — save `{"markdown":"..."}`
- `POST /jobs/{id}/retry` — retry a failed/completed job from checkpoints
- `POST /jobs/{id}/docx` — regenerate DOCX from current Markdown
- `GET /jobs/{id}/download/{json|md|docx}` — download an artifact
- `GET /health` — health check

## Test

```powershell
pytest
```
# Audio2Text

本地单用户的长音频双语文档工具。上传 40–90 分钟英文录音后，系统会：

1. 使用 FFmpeg 规范化并切分音频；
2. 通过 OpenRouter Speech-to-Text 转写；
3. 通过 OpenRouter LLM 推断 2–3 位说话人并翻译为中文；
4. 生成可编辑、可下载的中英对照 Markdown；
5. 将已保存的 Markdown 独立渲染为 Microsoft Word `.docx`。

> 说话人标签是语言模型根据上下文推断的结果，不是声纹识别。请在导出前检查并按需编辑 Markdown。

## 系统要求

- Python 3.11 或更高版本
- Node.js 20 LTS 或更高版本
- FFmpeg（`ffmpeg` 和 `ffprobe` 必须可通过 `PATH` 调用）
- 可用的 OpenRouter API Key

Windows 可使用 `winget` 安装缺失工具：

```powershell
winget install OpenJS.NodeJS.LTS
winget install Gyan.FFmpeg
```

安装后请重新打开终端，并分别运行 `node --version`、`npm --version` 和
`ffmpeg -version` 确认工具可用。

## 配置

项目根目录的 `.env` 至少需要以下值：

```dotenv
REMOTE_BASE_URL=https://openrouter.ai/api/v1
REMOTE_API_KEY=your-key
TRANSCRIPTION_MODEL=openai/whisper-1
LLM_MODEL=anthropic/claude-sonnet-4
```

完整配置项见 `.env.example`。真实 `.env` 已被 Git 忽略，不要提交 API Key。

模型需分别支持 OpenRouter 的 `/audio/transcriptions` 和
`/chat/completions` 接口。不同模型对 `verbose_json`、时间戳和 JSON Schema
的支持可能不同；默认配置优先兼容 OpenAI 风格接口。

## 启动后端

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
uvicorn backend.api:app --reload --port 8000
```

后端文档位于 <http://localhost:8000/docs>。

## 启动前端

另开一个终端：

```powershell
cd frontend
npm install
npm run dev
```

打开 <http://localhost:3000>。前端默认访问
`http://localhost:8000`；如需覆盖，在 `frontend/.env.local` 设置：

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## 数据与恢复

SQLite 数据库和每个任务的音频、切片、JSON、Markdown、DOCX 均保存在
`data/`，该目录已被 Git 忽略。处理阶段采用 checkpoint；应用重启后会恢复
未完成任务，已完成的音频片段不会重复请求。

删除 `data/` 会永久删除所有本地任务和生成物。公开部署前应增加身份认证、
独立任务队列、对象存储、限额、恶意文件扫描和数据保留策略。

## 验证

```powershell
pytest

cd .\frontend
npm run lint
npm run test
npm run build
```

真实端到端测试会产生 OpenRouter 费用。单元测试使用模拟响应，不应读取真实
API Key 或上传真实音频。

## DigitalOcean App Platform 演示部署

仓库包含两个生产镜像：

- `Dockerfile`：Python 3.12、FastAPI、FFmpeg、ffprobe 和 Noto CJK 字体。
- `frontend/Dockerfile`：Node.js 22 与 Next.js standalone 服务。

`.do/app.yaml` 将 `/api` 路由到后端，并将其余请求路由到前端。DigitalOcean
会为默认域名提供 HTTPS。部署前需要：

1. 将 `.do/app.yaml` 中两个 `github.repo` 改为实际的 `owner/repository`；
2. 按实际区域调整 `region`；
3. 在 App Platform 后端组件添加加密的运行时变量 `REMOTE_API_KEY`；
4. 如模型不同，覆盖 `TRANSCRIPTION_MODEL` 和 `LLM_MODEL`；
5. 当前前后端采用同域 `/api` 路由，浏览器不触发 CORS；只有拆分为不同域名时
   才需要将 `FRONTEND_ORIGIN` 更新为实际前端 Origin。

可通过 DigitalOcean 控制台导入仓库并分别添加两个 Web Service，也可以在
安装并登录 `doctl` 后使用：

```bash
doctl apps create --spec .do/app.yaml
```

当前 App Platform 配置是无持久服务的公开演示版本：

- 本地文件系统在部署、重启或实例替换后会被清空；
- SQLite、上传文件、checkpoint 和结果不会永久保存；
- 仅允许一个排队或处理中的任务；
- 已完成和失败任务默认保留 24 小时，并在下一次上传时清理；
- 页面会提示用户立即下载结果；
- 上传上限为 300 MiB，以降低 4 GiB 临时磁盘被填满的风险。

不要横向扩展后端，也不要把 Uvicorn worker 数量设为大于 1。正式使用必须将
任务状态迁移到 PostgreSQL，并将音频与产物迁移到 Spaces/S3；公开服务还应
增加认证、用户配额和基于网关的限流。
