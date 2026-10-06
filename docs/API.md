# 本地 REST API

本地默认地址 `http://127.0.0.1:8000`，交互式 OpenAPI 文档在 `/docs`。仅适用于本机单用户；未提供公网身份认证。浏览器跨站写请求拒绝，Host 仅允许回环名称。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | /api/health | 服务健康/version |
| GET | /api/capabilities | 配置是否齐全、分割权重是否存在；从不包含密钥 |
| POST | /api/assets?remove_background=false | multipart `file` 上传；返回 id/url/width/height/size |
| GET | /api/assets/{id} | 资产元数据 |
| GET | /api/assets/{id}/file | 预览文件；`?download=true` 返回附件 |
| POST | /api/jobs | JSON JobRequest；201 或未配置 503 |
| GET | /api/jobs | 最新 100 个任务 |
| GET | /api/jobs/{id} | 单个任务、阶段 ID、产物列表 |
| POST | /api/jobs/{id}/review | JSON `{"approve": true}` 确认姿势；false 放弃 |

上传需包含 Content-Length；上限约 10 MiB 加 multipart 开销。图片再经实际读取长度与解码验证。无图像资产或资产类型错误返回 422。任务输入禁止任意公网 URL，由本地已上传资产 ID 引用。

JobRequest 的完整模式以 OpenAPI 为准。主要参数为 front、views、pose_mode、pose_reference、face_count、topology、polygon_type、face_level、texture、texture_size、rig、neutral_pose_confirmed、export_fbx、seed。多视角与修改姿势互斥；custom 与 rig 互斥。

响应状态码：404 为不存在，409 为审核状态不匹配，413 为过大，422 为输入无效，503 为所需服务或本地权重未配置。云端执行错误写入任务 state=failed，不把供应商返回的敏感正文暴露出来。

客户端不提供 API Key 设置功能，也不调用配置接口。平台模型配置由运维通过服务端 `.env` / 环境变量管理，变更后重启后端。公网 `/api/settings` 返回 404，OpenAPI 不列出旧配置接口；仅无公网 origin、无浏览器 Origin、无代理转发的本地 CLI 维护请求保留兼容。

旧 CLI 维护接口只接受 `tencent_endpoint`、`tencent_region`、`tencent_model`、`tencent_secret_id`、`tencent_secret_key`、`pose_endpoint`、`pose_model`、`pose_api_key`。`PATCH` 可只提交修改的字段；密钥只允许写入，读取接口返回对应的 `*_set` 布尔值。若字段由进程环境变量提供，修改该字段返回 409。设置响应使用 `Cache-Control: no-store`。配置文件保存为仅当前用户可读写；现有配置文件的其他字段和注释保留。
