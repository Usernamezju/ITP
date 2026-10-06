# FaceVerse 远程脸部精细建模

原 3D 工作流保持独立；它完成并至少产生一个 GLB 后，结果页才出现“脸部精细建模”。用户上传原始高清人物正面照片到 `POST /api/face-photos`。这条路径不会执行几何输入的 1024px 缩小，允许短边 ≥512 像素、总像素 ≤2500 万、上传体积 ≤10 MiB。照片规范化为本地 PNG，去除 EXIF；照片与网格作为临时资产只在该次任务期间保留，浏览器确认结果后服务端删除。只有用户主动点“开始脸部精修”才创建 `POST /api/jobs/{id}/face-refinement`；`GET` 同路径查询任务。未配置 FaceVerse 服务器时返回 503，原模型和原任务均不受影响。

设置项：`ITP_FACEVERSE_ENDPOINT`、`ITP_FACEVERSE_MODEL`、`ITP_FACEVERSE_API_KEY`。地址需是 HTTPS 的 `/v1/face-refine`（本机测试允许 HTTP localhost/127.0.0.1）；访问令牌可选，空值时不发送 Authorization。密钥仅保存在本机 `.env`，不会回传前端。公网部署应配 TLS、访问控制与请求大小限制，因为高清人脸照片是敏感数据。

## 远程服务协议 v1

本地工作台对设置的地址发送 `POST application/json`，可选 `Authorization: Bearer <token>`：

```json
{
  "model": "faceverse-v4",
  "mesh_glb_base64": "<原始 GLB 的 base64>",
  "face_photo_base64": "<原始高清 PNG 的 base64>",
  "preserve": ["hair", "back_head", "neck"],
  "alignment_landmarks": ["eyes", "nose_tip", "mouth_corners", "chin", "head_width"],
  "required_operations": ["face_detection", "faceverse_reconstruction", "face_region_removal", "similarity_alignment", "boundary_matching", "laplacian_deformation", "remesh", "vertex_welding", "texture_fusion", "collision_check"]
}
```

成功时应返回 HTTP 200 JSON：`{"glb_base64":"<融合后 GLB>","report":{"face_bbox":[x1,y1,x2,y2],"operations":["..."],"landmarks":{},"quality":{}}}`。服务端需完成：检测/裁剪人脸、FaceVerse 专用头脸 Mesh 重建；定位并删除原模型正脸的低质量额头至下巴区域，同时保留头发、后脑、脖子；按双眼、鼻尖、嘴角、下巴和头宽估计 scale/rotation/translation；执行边界匹配、Laplacian 形变、remesh、顶点焊接和纹理融合；检查五官相似度、头部比例、侧脸连续性及穿模。`report.operations` 必须列明全部十项，否则本地视为失败并保留原模型。此协议由项目定义，不是 FaceVerse 官方内置 HTTP API；融合算法实现在 `services/faceverse/`，模型权重只在服务器上，不进入本地 Git 仓库。

输出经服务端 RAM 交付缓冲下载到本机浏览器，浏览器保存后确认，服务端随任务一并删除；它在原任务里记为额外的 `face_refine` 产物，不会覆盖原 GLB。远程调用中进程意外中断时不自动重试，以避免重复计算；用户可检查服务器日志后再发起。当前自动校验包含 GLB 文件头和报告操作清单；五官与缝合质量仍需人工/服务器指标验收。

远程实现位于 [`services/faceverse/`](../../services/faceverse/README.md)，已在指定 RTX 3080 Ti 服务器上完成真实照片 FaceVerse 重建和真实 GLB 融合验证。该服务单独部署，不影响原人体建模流程。服务会在 `report.quality` 中提供关键点拟合误差、接缝距离、剩余开放边及潜在碰撞采样数；这些数值不能替代正面和侧面人工检查。当前使用的 FaceVerse 权重来自第三方镜像，已进行哈希及实际推理验证，但尚不能证明与作者 OneDrive 原件逐字节一致。

模型参考：[FaceVerse v4 官方仓库](https://github.com/LizhenWangT/FaceVerse_v4)。
