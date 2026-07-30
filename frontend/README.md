# Audio2Text Frontend

本地单用户音频转写工作台，基于 Next.js App Router 与 TypeScript。

## 启动

需要 Node.js 20.9 或更高版本。

```bash
npm install
npm run dev
```

页面默认连接 `http://localhost:8000`。如需更改，在 `frontend/.env.local` 中设置：

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## 后端 API 契约

前端使用以下端点：

- `POST /jobs`：multipart 上传，字段为 `file`
- `GET /jobs/{id}`：读取任务状态
- `GET /jobs/{id}/markdown`：读取 Markdown
- `PUT /jobs/{id}/markdown`：保存 `{ "markdown": "..." }`
- `POST /jobs/{id}/retry`：重试失败任务
- `POST /jobs/{id}/docx`：重新生成 DOCX
- `GET /jobs/{id}/download/{md|json|docx}`：下载产物

后端的 `processing` 状态会结合 `stage` 映射为页面上的预处理、转写、
翻译或渲染阶段。

后端应启用对前端开发地址（默认 `http://localhost:3000`）的 CORS。

## 验证

```bash
npm run lint
npm test
npm run build
```
