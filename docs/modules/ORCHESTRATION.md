# 任务编排与存储

实现：`pipeline.py`、`storage.py`。一个后台线程串行处理任务，SQLite WAL 持久化任务与资产。数据目录持有进程文件锁，阻止两个实例同时操作云任务。

> 顾客任务现在运行在 `transient.py` 的临时 Store 上：元数据只在 RAM，输入与工作文件只在标记的临时目录，交付结果由浏览器保存后确认删除。下面的持久化路径描述的是 `storage.py` 这一基类及其遗留部署，顾客数据的存储契约以 [顾客本地数据与临时计算](PRIVACY.md) 为准。

## 任务状态

queued → running → succeeded / failed。姿势编辑增加 running → awaiting_review → queued；拒绝为 rejected。审核使用 SQLite `BEGIN IMMEDIATE`，重复确认返回 409，避免启动重复任务。

阶段状态为 pending → submitting → submitted → download → done。同步姿势和格式转换跳过 submitted。

- 提交前记录 submitting；若进程在请求中断开，重启发现该状态后停止并报告结果不确定，不能自动重提。
- 已有 provider_job_id 的 submitted 阶段只继续查询原任务。
- 已收到结果 URL 的 download 阶段继续下载；已保存的产物按阶段和索引跳过。
- done 阶段直接复用结果。
- failed 任务不自动重试。网络提交失败/等待超时后，远程仍可能完成；先通过已记录 ID/RequestId 核对。首版无任务取消或付费失败重提按钮。

## 数据

`data/studio.sqlite3` 保存元数据，`data/assets/<uuid>.<ext>` 保存文件，`data/worker.lock` 是单实例锁。应用不提供路径参数读取任意文件。Public API 隐去阶段原始签名 URL，文件通过资产 ID 访问。

进程使用内存中的当前任务，但每个状态边界都落盘。文件/SQLite 不是跨介质原子事务；若在文件完成与数据库提交之间断电，可能留下未引用资产。首版采用保留策略，不自动删除，后续可实现带审计的清理工具。

任务历史最多返回最近 100 条；数据库保留更早记录。磁盘配额、备份轮转、多用户隔离、分布式调度、自动重新签发过期 URL 属于后续运维能力。
