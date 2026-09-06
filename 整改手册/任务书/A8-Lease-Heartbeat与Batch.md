# 任务书 A8：Lease、Heartbeat 与 Batch

## 目标

避免多 worker 重复领取，并逐步停止多个 batch SQLite 导致的状态膨胀。

## 执行内容

新增 worker_id、lease_until、heartbeat_at。领取任务时以事务更新 pending → fetching，并设置 lease_until = now + N。

worker 定时更新 heartbeat_at 与 lease_until。只有 lease_until < now 的任务才允许被其他 worker 回收。

禁止继续依据 updated_at 超过固定分钟数就自动回到 pending。

将 tasks_pmc_bN.sqlite 逐步收敛为 tasks、batches、batch_members，或采用 query-based batch；迁移必须保留历史任务和状态。

## 验收

并发 fixture 验证同一任务只能被一个有效 lease 持有；过期 lease 可回收，未过期 lease 不被抢占；batch 查询与历史记录一致。

完成后提交独立 commit，并暂停等待审查。

