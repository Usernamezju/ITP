# 开发规范

1. 按需求、架构、实现、验证、文档、提交的顺序推进子任务。
2. 提交使用 Conventional Commits：`docs:`、`feat:`、`fix:`、`test:`、`chore:`。
3. 每个完成的子任务独立提交，同时更新对应模块文档与实施计划。
4. 代码/注释英文；文档与界面中文。Python 使用类型标注、Ruff；前端使用 TypeScript。
5. 外部服务封装在 provider 中，不让 HTTP 路由与供应商协议耦合。
6. 新功能必须有与风险对应的测试；云端协议测试不得冒充真实模型效果测试。
7. 不记录请求图像 Base64、凭据或带签名的下载链接；不提交生成数据或权重。
8. 临时验证文件置于 `/home/fjp/temp/itp-verification/`，正式回归测试放在 `tests/`。
9. 修改任务状态机或模型调用协议时，维护 ADR、接口说明和故障恢复文档。
10. 当前为单机单用户部署；增加外网访问前必须增加身份认证、用户隔离、配额和持久化工作队列。

## 跑完整回归

后端：

    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest

浏览器（Playwright，桌面与手机两个 project）：

    npm run build --prefix frontend
    npm run test:e2e --prefix frontend

浏览器套件需要一个真正在跑的本地服务，而且它注册的是**真实账号**、买的是**真实订单**，
所以服务必须按开发/测试配置启动，不能只按 `.env.example` 里的生产默认值：

    ITP_ENVIRONMENT=development \
    ITP_PAYMENT_MOCK_ENABLED=true \
    ITP_PAYMENT_MOCK_SECRET=local-development-mock-secret-32chars \
    uv run --no-sync uvicorn itp.api:create_app --factory --host 127.0.0.1 --port 8000 \
      2>&1 | tee /home/fjp/temp/itp-server.log

少了 `ITP_PAYMENT_MOCK_ENABLED` 时，`/api/payments/methods` 不会提供 `mock` 渠道，
`amendment-benefits.spec.ts` 的会员购买按钮会一直是禁用状态。

注册与登录按来源地址限流（`ITP_AUTH_REGISTER_PER_HOUR`、`ITP_AUTH_LOGIN_PER_5MIN`）。
全套一轮会注册十几个账号，同一小时内反复整套重跑会撞上预算；要连续跑就先把预算调高，
或者重启服务——计数器只存在于进程内存里。
