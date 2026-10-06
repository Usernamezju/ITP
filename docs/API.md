# 本地 REST API

本地默认地址 `http://127.0.0.1:8000`，交互式 OpenAPI 文档在 `/docs`。
账号、资金和建模任务使用统一 JWT；公网仍有过渡性的共享工作台访问门禁，
顾客资产与临时工作区已在 C04 完成切换到浏览器本地（见
[顾客本地数据与临时计算](modules/PRIVACY.md)）。浏览器跨站写请求拒绝，
Host 仅允许回环名称，公网由受控 Nginx 转发。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | /api/health | 服务健康/version |
| GET | /api/capabilities | 配置是否齐全、分割权重是否存在；从不包含密钥 |
| POST | /api/auth/register | 统一顾客/商家注册，默认 customer |
| POST | /api/auth/login | 同一 scrypt/JWT 实现签发会话 |
| POST | /api/auth/logout | Bearer；服务端吊销当前令牌 |
| GET / PATCH | /api/account/me | Bearer；查看/修改个人资料，角色不可自行更改 |
| POST | /api/account/password | Bearer；验证当前密码并吊销全部旧密码会话 |
| GET | /api/pricing | 服务端配置的整数分价格与套餐 |
| GET | /api/account/commerce | Bearer；自己的余额、会员权益和周期上传额度 |
| GET | /api/account/ledger | Bearer；自己的流水，limit/offset 分页 |
| POST | /api/assets?remove_background=false | Bearer；上传一次临时素材；返回 id/url/width/height/size |
| GET | /api/assets/{id} | Bearer；本人资产的元数据 |
| GET | /api/assets/{id}/file | Bearer；预览本人在服务端尚未清理的文件 |
| POST | /api/face-photos | Bearer；脸部精修用高清照片的临时资产 |
| POST | /api/model-assets | Bearer；推荐用的 GLB 临时资产 |
| POST | /api/jobs | Bearer + Idempotency-Key；扣钱包整数分；201/402/409/503 |
| GET | /api/jobs | Bearer；只返回自己尚未清理的任务 |
| GET | /api/jobs/{id} | Bearer；自己的任务、阶段 ID、产物列表 |
| POST | /api/jobs/{id}/acknowledge | Bearer；浏览器保存好产物后删除服务端副本，204 |
| POST | /api/jobs/{id}/review | Bearer；JSON `{"approve": true}` 确认姿势；false 放弃并退款 |
| POST | /api/tryons | Bearer；六视图试穿任务 |
| GET | /api/tryons、/api/tryons/{id} | Bearer；进度与结果资产 id |
| POST | /api/tryons/{id}/acknowledge | Bearer；同 jobs 的确认删除 |
| POST | /api/face-refinements | Bearer；脸部精修任务 |
| GET | /api/face-refinements/{id} | Bearer；进度与结果 |
| POST | /api/face-refinements/{id}/acknowledge | Bearer；同 jobs 的确认删除 |
| GET | /api/outfits | 公开；不含顾客数据的通用推荐目录 |
| POST | /api/outfits/recommend | Bearer；本机 GLB 与人体数值单次上传，算完即删 |

上传需包含 Content-Length；上限约 10 MiB 加 multipart 开销。图片再经实际读取长度与解码验证。无图像资产或资产类型错误返回 422。任务输入禁止任意公网 URL，由本地已上传资产 ID 引用。

JobRequest 的完整模式以 OpenAPI 为准。主要参数为 front、views、pose_mode、pose_reference、face_count、topology、polygon_type、face_level、texture、texture_size、rig、neutral_pose_confirmed、export_fbx、seed。多视角与修改姿势互斥；custom 与 rig 互斥。

响应状态码：404 为不存在，409 为审核状态不匹配，413 为过大，422 为输入无效，503 为所需服务或本地权重未配置。云端执行错误写入任务 state=failed，不把供应商返回的敏感正文暴露出来。

客户端不提供 API Key 设置功能，也不调用配置接口。平台模型配置由运维通过服务端 `.env` / 环境变量管理，变更后重启后端。公网 `/api/settings` 返回 404，OpenAPI 不列出旧配置接口；仅无公网 origin、无浏览器 Origin、无代理转发的本地 CLI 维护请求保留兼容。

旧 CLI 维护接口只接受 `tencent_endpoint`、`tencent_region`、`tencent_model`、`tencent_secret_id`、`tencent_secret_key`、`pose_endpoint`、`pose_model`、`pose_api_key`。`PATCH` 可只提交修改的字段；密钥只允许写入，读取接口返回对应的 `*_set` 布尔值。若字段由进程环境变量提供，修改该字段返回 409。设置响应使用 `Cache-Control: no-store`。配置文件保存为仅当前用户可读写；现有配置文件的其他字段和注释保留。
