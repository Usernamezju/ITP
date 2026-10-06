# 虚拟试穿

独立侧栏页面，不修改原人体建模入口。人物与服装各上传 1–6 张图，视角仅限正、背、左、右、左前、右前，至少各一张；先经 `/api/assets` 保存为本地资产，`POST /api/tryons` 创建独立任务。未提供的视角选取方位角最接近的已上传参考图引导生成；输入越少，身份和服装一致性越难保证。旧版 `consistent_confirmed` 字段可兼容接收，但不作为创建前置条件。模型 API Key 由平台运维在服务端配置，不向客户端回传，也不要求顾客或商家填写。

任务按正面、背面、左侧、右侧、左前、右前顺序生成。正面使用两张原图；其余视角各使用人物与服装当前或回退视角、两张补充参考与已生成正面，共五张参考图。提示词约束人物身份、脸部、体型、姿势、服装版型与色彩，但生成式模型不提供严格的一致性保证；结果需人工检查。每张完成后转为本地 PNG 资产，逐张显示与下载。调用状态持久化；如调用中进程中断，不自动重提付费请求，以免重复计费。

可选 `seedream`、`flux`、`flux_max`、`flux_klein`、`flux_klein_9b`、`gpt_image` 六种 `provider`。`GET /api/capabilities` 的 `tryon_providers` 返回各自是否已配置。SeedDream 使用北京方舟；FLUX 使用 BFL 异步接口（Pro 为 `/v1/flux-2-pro`，Max 为 `/v1/flux-2-max`）、轮询 `/v1/get_result` 并下载结果，或下述海鲸试验协议；GPT Image 2 使用兼容 OpenAI `/v1/images/edits` 的多图 JSON 请求与 base64 结果。FLUX 和 GPT 可填写从本机可访问的兼容 HTTPS 地址，官方站点在中国大陆的直连可用性不作保证。BFL 模型由接口路径选定，`flux_model` 和 `flux_max_model` 仅用于任务记录；海鲸与 GPT 模型 ID 进入请求体。服务调用失败不自动切换其他服务。

## FLUX.2 Max API

在生图模型下拉框选择 `FLUX.2 Max`（`provider=flux_max`）。服务端环境变量包含独立的服务地址、模型标识和 API Key；默认地址和 Key 为空，不复制 Pro 或 Klein 的密钥，不下载本地权重。模型标识默认为 `flux-2-max`。运维修改后重启后端，未配置时界面显示“暂不可用”并禁止提交试穿任务，但不影响原人体建模。

配置项为 `ITP_FLUX_MAX_ENDPOINT`、`ITP_FLUX_MAX_MODEL`、`ITP_FLUX_MAX_API_KEY`。官方地址是 `https://api.bfl.ai/v1/flux-2-max`；也可填写可访问的 **BFL 协议兼容 HTTPS 服务**，路径必须为 `/v1/flux-2-max`，不能直接填写 OpenAI 风格接口。使用 `x-key` 鉴权，图片字段为 `input_image`、`input_image_2` 至 `input_image_8`（JPEG base64，最多八张）；提交返回任务 `id` 和 `polling_url`，就绪时下载 `result.sample`。轮询地址必须属于所配置服务的同一主机 `/v1/get_result`，密钥不会附带到结果图片下载请求。

Max 沿用当前六视图生成顺序和人物/服装/正面结果参考策略，最终生成六张本地图片，可继续原有 3D 流程。本次仅进行了协议单元测试，不调用收费 API；“已配置”不代表账号权限、余额或实际生图质量已验证。

### 海鲸多参考图试验接入

Pro 与 Max 均接受并保存 `https://api.haijingai.com/v2/images/generations`，使用各自独立的模型与密钥配置，不再因 BFL 路径限制拒绝保存。公网禁止访问 `/api/settings`，客户端没有模型凭据设置页面；参见 [部署权限说明](../../deploy/autodl/README.md)。

按项目所有者提供的 cURL 示例实现试验适配，代码位于 `src/itp/image_relay.py`，由 `FluxProvider` 根据上述精确服务地址选择。POST 请求使用 `Authorization: Bearer <对应模型的 API Key>`，不使用 BFL 的 `x-key`，也不进入 BFL 异步轮询。请求体包含 `model`（`flux-2-pro` 或 `flux-2-max`，由平台运维在服务端配置）、原有一致性提示词、`aspect_ratio: "3:4"` 以及 `input_image`、`input_image_2` 等编号参考图。正面使用人物、服装两张参考图；其他视角保持上述五参考图策略。原人体建模和其他生图模型的调用方式不变。

**图片格式仍需用户实测确认。** 用户示例使用 HTTPS 图片 URL；网页上传的是本地受保护资产，为避免公开用户图片或把网站登录密码交给中转站，这里在相同编号字段发送 JPEG Base64 字符串（不带 data URL 前缀）。海鲸是否接受 Base64 以及后续视角的五张参考图，尚未进行真实服务验证。若海鲸只接受 URL，需要另行设计安全的图片交付方式，而不能把需登录的资产链接直接交给它。当前已取消临时“传图协议待确认”禁用，完整配置即可发起试穿；“已配置”只代表必填项齐全，不代表实际生成成功。实际六次生成可能收费，失败不会自动重试或切换模型。

响应兼容 OpenAI Images 风格的 `data[0].url` 或 `data[0].b64_json`。下载只接受公开 HTTPS 地址，禁止携带 URL 用户名密码、已知本地地址和重定向；下载请求不附带 API Key，并限制为 10 MiB。远端 HTTP 错误保留状态码用于排错，不回显可能包含密钥或图片的响应正文。图片继续经过原有本地规范化流程后展示、保存、衔接图生 3D。

协议测试采用离线 HTTP transport，覆盖 Pro/Max、请求字段和鉴权、URL/Base64 结果、响应错误、危险地址、重定向与体积限制；浏览器测试覆盖桌面及窄屏下保存并提交海鲸配置。本次没有调用海鲸生成 API，实际效果由用户测试。[海鲸模型页面](https://api.haijingai.com/api-docs/model-detail/flux-2-max/)与 [公开 API 文档](https://api.haijingai.com/api-docs/api/image-generation/)仅作后续核对入口，不将本次试验格式标记为官方已验证协议。

FLUX.2 Klein 4B 是自建服务，代码见 `services/flux_klein/`。ITP 的 `flux_klein` 适配器通过带 Bearer Token 的 `POST /v1/flux-klein/edit` 发送 1–4 张 JPEG data URL、提示词与模型标识，接收 PNG base64；正面最多两张参考图，其他视角最多四张，优先保留当前人物、当前服装与已生成的正面换装图。`GET /api/tryon-providers/flux-klein/health` 检查远端模型是否实际加载；未就绪时不会创建任务。配置项：`ITP_FLUX_KLEIN_ENDPOINT`、`ITP_FLUX_KLEIN_API_KEY`、`ITP_FLUX_KLEIN_MODEL`。用户在右侧服务卡片的下拉框选择模型；窄屏的选择框位于左侧设置区。

FLUX.2 Klein 9B 使用独立的 `flux_klein_9b` 选项，配置为 `ITP_FLUX_KLEIN_9B_ENDPOINT`、`ITP_FLUX_KLEIN_9B_API_KEY`、`ITP_FLUX_KLEIN_9B_MODEL`（默认 `flux.2-klein-9b`）。它与 4B 采用相同的图片协议和四参考图策略，六张输出可直接继续生成 3D。`GET /api/tryon-providers/flux-klein-9b/health` 会同时检查就绪状态和模型标识；把 9B 地址误填为 4B 服务时会拒绝创建任务。两种服务配置与访问令牌互相独立。自建服务如何加载对应权重见 [FLUX 服务文档](../../services/flux_klein/README.md)。

`GET /api/tryons/{id}` 查询进度；`POST /api/tryons/{id}/continue` 仅在六张完成后创建原有 3D 任务，直接复用六个本地资产 ID，不重复上传。若不继续，图片仍可保存。接口未配置时试穿不可提交，但原有 `/api/jobs` 保持可用。

## 页面布局

试穿页面沿用人体建模工作台的视觉结构：左侧是任务名称、人物和服装上传框；中间是深色主预览、流程状态和六张结果缩略图；右侧是模型下拉选择、所选服务状态和逐视角生成进度。上传框完整显示图片，并可放大查看；窄屏将上传区与预览区纵向排列，模型选择移至左侧设置区。结果生成后可切换主预览、逐张保存，或继续生成 3D 模型。页面代码位于 `frontend/src/TryOnPage.tsx`，试穿工作台样式位于 `frontend/src/TryOnWorkspace.css`。

接口依据：[火山引擎图片生成 API](https://docs.volcengine.com/docs/ark/image-generation-api?lang=zh)、[BFL FLUX.2 API](https://github.com/black-forest-labs/skills/blob/master/skills/bfl-api/references/endpoints.md)、[FLUX.2 Klein 4B 模型卡](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B)、[OpenAI 图像编辑 API](https://developers.openai.com/api/reference/resources/images/methods/edit)、[GPT Image 2 模型](https://developers.openai.com/api/docs/models/gpt-image-2)。
