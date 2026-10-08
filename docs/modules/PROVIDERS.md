# 国内 API 适配与配置

模型凭据是平台运维配置，不是顾客或商家账号资料。客户端没有 API Key、token、服务地址配置表单或页面，也不请求 `/api/settings`；未配置的服务显示“暂不可用”。运维在服务端 `.env` 或进程环境变量填写下列字段，再重启后端。`.env.example` 为不含密钥的模板，部署时不得覆盖已有 `.env`。公网 `/api/settings` 返回 404，OpenAPI 不公开该旧维护接口；仅无公网 origin 的回环部署保留旧 CLI 兼容，不接受浏览器 Origin 或代理请求。

## 腾讯混元 AI3D

| 配置项 | 后续填写内容 |
| --- | --- |
| ITP_TENCENT_ENDPOINT | 国内 API hostname：`ai3d.tencentcloudapi.com`（无协议和路径） |
| ITP_TENCENT_SECRET_ID / ITP_TENCENT_SECRET_KEY | 自己账号的 CAM 凭据 |
| ITP_TENCENT_REGION | 控制台对该服务支持的地域，例如核对后填 `ap-guangzhou` |
| ITP_TENCENT_MODEL | 默认 `3.1`，应与账号支持的模型匹配 |

使用官方 `tencentcloud-sdk-python-ai3d`，API 版本 `2025-05-13`，签名由 SDK 生成。SDK 自动重试关闭，防止提交超时后重复扣费。四个连接字段全部非空后，服务能力接口才报告可用；该状态不等于已通过账号权限或实际推理验证。

| 阶段 | 提交 | 查询 | 关键输入 |
| --- | --- | --- | --- |
| 几何 | SubmitHunyuanTo3DProJob | QueryHunyuanTo3DProJob | ImageBase64、Model、GenerateType=Geometry、FaceCount、可选 MultiViewImages |
| 拓扑（已下线） | SubmitReduceFaceJob | DescribeReduceFaceJob | File3D、PolygonType、FaceLevel |
| 纹理（已下线） | SubmitTextureTo3DJob | DescribeTextureTo3DJob | File3D、Image、EnablePBR、TextureSize |
| 绑骨（已下线） | SubmitAutoRiggingJob | DescribeAutoRiggingJob | File3D |
| FBX 转换（已下线） | Convert3DFormat（同步） | 不适用 | File3D URL、Format=FBX |

查询状态为 WAIT/RUN/FAIL/DONE；任务成功读取 `ResultFile3Ds`。下载链接有效期有限，工作流及时缓存每一步的产物，阶段间直接引用供应商 URL，不把本地路径误当公网 URL。

## 千问姿势编辑

| 配置项 | 后续填写内容 |
| --- | --- |
| ITP_POSE_ENDPOINT | 北京业务空间的完整 URL：`https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation` |
| ITP_POSE_API_KEY | 对应北京地域 API Key |
| ITP_POSE_MODEL | 默认固定为 `qwen-image-edit-plus-2025-12-15`，需确认账号可用 |

也支持国内旧域名 `dashscope.aliyuncs.com` 的同一路径；具体可用性以控制台为准。适配器发送图一角色、图二姿势（仅自定义模式）和编辑指令，使用 JPEG data URL；输出只取第一张图，保存后等待人工确认。

不使用国际端点；配置校验拒绝未知服务地址，以免误把密钥发往其他站点。若后续换供应商，应新增 provider，不能只修改地址套用不兼容的 JSON。

## 安全与错误

- 凭据通过 `SecretStr` 与 `.env` 隔离，Git 忽略 `.env`。运维应将 `.env` 设为仅服务账户可读写（0600）。不要在聊天或日志中展示密钥。
- 错误仅展示错误码/RequestId，不持久化可能包含签名 URL 或图片的供应商错误正文。
- 姿势编辑同步调用超时 180 秒；腾讯请求 60 秒；网络提交不自动重试。
- 查询最多连续重试 3 次；总阶段等待默认一小时。失败不代表远程任务被取消。
- 模型价格、实际输出格式和权限由服务端决定，代码不假定免费额度。

协议来源与核验日期见 [调研记录](../RESEARCH.md)。本版本的适配测试使用替身响应，没有消耗云端额度，真实账户与效果尚待接入后验收。
