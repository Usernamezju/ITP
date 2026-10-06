# ITP · Image to Pose & 3D

国内服务优先的角色姿势控制与 3D 资产工作台。参考 Meshy Custom Pose 视频，支持角色图、姿势参考图、多视角、几何生成、拓扑、PBR、绑骨与资产导出。

## 当前可用

本地图片上传、可选 CPU 去背景、多视角输入、姿势设置、持久任务流程、GLB 预览与历史列表。云端适配器覆盖千问姿势编辑、混元几何、智能拓扑、PBR 纹理、自动绑骨与 FBX 转换，且等待真实服务配置和样例验收。穿搭推荐解析图生 3D 产出的 GLB，估算肩宽、腰线、胯宽与腿身比，再从本地精选目录按比例给出成套穿搭；尚未生成模型时按通用体型推荐。每套穿搭会从图片检索服务取真实穿搭图片并缓存在本机，默认使用免 key 的 360 图片，可在设置页切换到 Unsplash 或 Pixabay，取不到图片时回落到配色示意。

穿搭推荐同时支持**按尺码指标匹配**：「人体建模」页可以选填身高、体重、肩宽、胸围、腰围、臀围（不填也能用，未填项由模型比例推算并标注「估算」）；服装侧由商家录入，指标包含尺寸、适配区间与版型/弹性/克重/主色等属性。推荐结果逐维度给出「你的数值 / 该款区间 / 合身状态 / 中文理由」，而不是只有标签和分数。商家用左侧导航的**「商家后台」**页面注册登录、上传商品图并填写指标；同一套契约也开放为 HTTP 接口，供第三方系统接入。详见 [指标与匹配规范](docs/modules/METRICS.md) 与 [商家接口](docs/modules/MERCHANT.md)。

API 字段默认留空。可在网页左侧的“设置”页面填写并保存，配置写入本地未跟踪的 `.env`，立即生效；页面只显示密钥是否已填写，不会回显密钥。也可参照 [`.env.example`](.env.example) 手动编辑 `.env`，手动编辑后需重启服务。没有 API 时仍可使用上传、去背景和导入本地 GLB 的功能。

## 本地运行

在本项目根目录执行：

```bash
uv sync --locked --index-url https://pypi.tuna.tsinghua.edu.cn/simple
npm ci --prefix frontend --registry=https://registry.npmmirror.com
npm run build --prefix frontend
uv run --no-sync uvicorn itp.api:create_app --factory --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000/`。小型去背景权重可通过 `uv run --no-sync python scripts/download_models.py` 单独安装；当前工作区已安装在被 Git 忽略的 `models/u2netp.onnx`。运行时不会下载权重。开发模式可同时在 `frontend/` 执行 `npm run dev`，打开 `http://127.0.0.1:5173/`。

准备接入云端时，在网页“设置”页面填写账号信息，具体字段见 [API 配置文档](docs/modules/PROVIDERS.md)。不要把密钥提交到 Git。当前服务是本机单用户工作台，不要直接暴露到公网。

## 验证

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest
npm run build --prefix frontend
```

本机 ROS 环境会自动加载一个缺少依赖的无关 pytest 插件，因此这里关闭第三方插件自动发现。测试使用替身云响应检查协议和流程；**尚未进行收费 API 的真实图生 3D 与视频效果质量验收**。

浏览器验收需先启动本地后端、安装 Playwright Chromium，然后在 `frontend/` 运行 `npm run test:e2e`。它覆盖桌面和手机布局的离线上传、姿势切换、本地 GLB 预览和配置状态。

## 文档导航

- [需求与验收](docs/REQUIREMENTS.md)
- [系统架构](docs/ARCHITECTURE.md)
- [技术调研与来源](docs/RESEARCH.md)
- [实施计划](docs/PLAN.md)
- [本地 REST API](docs/API.md)
- [Web 工作台](docs/modules/WEB.md)
- [六视图图生 3D](docs/modules/SIX_VIEWS.md)
- [SeedDream 虚拟试穿](docs/modules/TRYON.md)
- [穿搭推荐](docs/modules/OUTFITS.md)
- [指标与匹配规范](docs/modules/METRICS.md)
- [商家接口](docs/modules/MERCHANT.md)
- [FaceVerse 远程脸部精修](docs/modules/FACE_REFINEMENT.md)
- [模型与处理模块](docs/modules/GEOMETRY.md)
- [开发规范](CONTRIBUTING.md)

代码与开发注释使用英文，面向用户的界面和技术文档使用中文。模型、密钥、上传图片和生成资产不进入 Git。
